"""What each enabled plugin contributes to the harness, read from its directory.

Two sources, and the second is the authority. `installed_plugins.json` says what
is installed, at what version, from which commit. The plugin's own directory says
what it actually contains — and a manifest can claim a skill that is not there,
while a working copy's contents change without its version moving at all.

Enabled-ness is decided elsewhere (`settings_read`). This module is told which
keys are on and reports on those. A plugin installed but switched off is not part
of the harness, and inventorying it would attribute measures to components that
never loaded.

Three rules govern what reaches a record:

- **`gitCommitSha` is identity; `installedAt` and `lastUpdated` are not.** A
  timestamp in the digest makes every reinstall look like a new harness, which
  splits one composition's episodes into populations too small to compare.
- **No sha means a content digest.** Without the fallback, live-editing a local
  plugin never moves the composition — and the plugin most likely to be
  live-edited is whichever one its author is currently working on, including
  this one.
- **No absolute path and no raw command.** Install paths sit under the user's
  home and a hook command can carry an inline credential (ADR-010). A composition
  record is a file a user may attach to a bug report, so the reduction happens
  before anything is returned rather than at write time.

Nothing here raises. A corrupt manifest costs one field, not the session.
"""

import hashlib
import json
import os

import settings_read

# Files that declare what a plugin *is*. Hashed by content for the fallback
# digest, in this order.
_MANIFEST = os.path.join(".claude-plugin", "plugin.json")
_HOOKS_MANIFEST = os.path.join("hooks", "hooks.json")
_MCP_MANIFEST = ".mcp.json"

# A plugin's hook commands reference their scripts through this variable.
# Substituting it is what lets the content digest cover the scripts a directory
# plugin is actually edited in, rather than only its declarations.
_ROOT_VARS = ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT")

# Bounds on the fallback digest. A plugin directory can contain a vendored
# dependency tree; the digest covers the declaration surface plus declared hook
# scripts, and says so rather than walking whatever is there.
_MAX_DIGEST_FILES = 256
_MAX_DIGEST_BYTES = 4 * 1024 * 1024

_DIGEST_LEN = 12


def read_installed(home):
    """`(entries, unreadable)` from `installed_plugins.json`.

    `entries` maps plugin key to the raw install record. `unreadable` is `None`
    on success, else a reason — an absent file is success with nothing in it,
    because having no plugins installed is a normal state and not a fault.
    """
    path = os.path.join(_as_path(home), ".claude", "plugins", "installed_plugins.json")
    if not os.path.isfile(path):
        return {}, None

    try:
        with open(path) as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return {}, "unparseable"
    if not isinstance(data, dict):
        return {}, "not_an_object"

    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return {}, "not_an_object"

    return (
        {k: v for k, v in plugins.items() if isinstance(v, dict)},
        None,
    )


def read_marketplaces(home, extra=None):
    """Provenance per marketplace: where it came from, and whether it is live.

    `extra` is the merged `extraKnownMarketplaces` from settings, which declares
    marketplaces just as really as the file does.

    `installLocation` and `lastUpdated` are dropped: the first is an absolute
    path under the user's home, and the second would move the digest on every
    marketplace refresh — a refresh that changes nothing about the harness.
    """
    path = os.path.join(_as_path(home), ".claude", "plugins", "known_marketplaces.json")
    raw = {}

    if os.path.isfile(path):
        try:
            with open(path) as fh:
                loaded = json.load(fh)
        except (ValueError, OSError):
            loaded = None
        if isinstance(loaded, dict):
            raw.update(loaded)

    if isinstance(extra, dict):
        raw.update(extra)

    return {
        name: _marketplace_record(entry)
        for name, entry in raw.items()
        if isinstance(entry, dict)
    }


def _marketplace_record(entry):
    source = entry.get("source")
    kind = None
    repo = None
    if isinstance(source, dict):
        kind = source.get("source")
        repo = source.get("repo") or source.get("url")
    elif isinstance(source, str):
        kind = source

    if not isinstance(kind, str):
        kind = "unknown"

    return {
        "source_type": kind,
        # A repo slug or a remote URL is provenance worth keeping. A local path
        # is not — it names a directory under someone's home, and `live_edited`
        # already carries the only fact about it that matters.
        "repo": repo if _is_remote_ref(repo) else None,
        # A directory marketplace is a working copy: its contents can change
        # with no version and no sha moving. Everything downstream needs that
        # before it trusts a version number.
        "live_edited": kind == "directory",
    }


def _is_remote_ref(value):
    """True for `owner/repo` or a remote URL; false for anything local.

    The distinction is not cosmetic: a local marketplace's `repo` field is a
    filesystem path under the user's home, and no absolute path may reach a
    record.
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith(("http://", "https://", "git@")):
        return True
    if value.startswith(("/", "~", ".")):
        return False
    return value.count("/") == 1


def content_digest(plugin_dir):
    """A digest over what a plugin *contains*, for installs with no commit sha.

    Covers the declaration surface — `plugin.json`, `hooks/hooks.json`,
    `.mcp.json`, every `skills/*/SKILL.md` and every `agents/*.md` — plus the
    hook scripts those hooks declare, resolved through `${CLAUDE_PLUGIN_ROOT}`
    and only when they land inside the plugin directory.

    Hook scripts are included because a directory plugin under development is
    edited in its scripts, not its declarations. A digest covering declarations
    alone would sit still across every behavioural change to the plugin its
    author is iterating on.

    **Content, not mtimes** (ADR-001). A fresh clone rewrites every mtime and
    changes no behaviour; an mtime-based digest would report a composition change
    on checkout, splitting one composition's episodes across two digests and
    leaving both below the minimum n. Reading the files costs more, and buys a
    digest that moves if and only if the plugin does.

    Returns `None` when the directory is not there — an unknown, not a zero.
    """
    plugin_dir = _as_path(plugin_dir)
    if not os.path.isdir(plugin_dir):
        return None

    digest = hashlib.sha256()
    budget = [_MAX_DIGEST_BYTES]

    for rel in _digest_files(plugin_dir):
        # The relative path is part of the hash, so moving a skill counts as a
        # change even when the bytes are identical.
        digest.update(rel.encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update(_read_bounded(os.path.join(plugin_dir, rel), budget))
        digest.update(b"\0")

    return digest.hexdigest()[:_DIGEST_LEN]


def _digest_files(plugin_dir):
    """Relative paths to hash, in a deterministic order.

    Sorted rather than filesystem order: directory iteration order is not
    guaranteed across filesystems, and an unsorted walk would give the same
    plugin two digests on two machines.
    """
    rels = []

    for rel in (_MANIFEST, _HOOKS_MANIFEST, _MCP_MANIFEST):
        if os.path.isfile(os.path.join(plugin_dir, rel)):
            rels.append(rel)

    for skill in _skill_names(plugin_dir):
        rels.append(os.path.join("skills", skill, "SKILL.md"))
    for agent in _agent_names(plugin_dir):
        rels.append(os.path.join("agents", "%s.md" % agent))

    rels.extend(sorted(_declared_script_rels(plugin_dir)))

    return rels[:_MAX_DIGEST_FILES]


def _declared_script_rels(plugin_dir):
    """Scripts named by this plugin's own hook commands, inside its directory.

    Only files under `plugin_dir` are returned. A command naming `/etc/passwd`
    or a multi-gigabyte binary elsewhere on the machine must not be opened — the
    command string is data read off disk, not something this module chose.
    """
    block = _read_json(os.path.join(plugin_dir, _HOOKS_MANIFEST))
    if not isinstance(block, dict):
        return set()

    root = os.path.realpath(plugin_dir)
    prefix = root + os.sep
    found = set()

    # `hook_entries` deliberately hashes the command away, so the raw text is
    # re-read here, used only to resolve a path, and never returned.
    for command in _raw_commands(block.get("hooks")):
        for token in command.replace("'", " ").replace('"', " ").split():
            for var in _ROOT_VARS:
                if var not in token:
                    continue
                candidate = token.replace(var, plugin_dir)
                real = os.path.realpath(candidate)
                if real.startswith(prefix) and os.path.isfile(real):
                    found.add(os.path.relpath(real, root))
                break

    return found


def _raw_commands(hooks_block):
    """Command strings from a hooks block, for path resolution only.

    Nothing derived from these leaves this module except a relative path that
    was verified to exist inside the plugin directory.
    """
    if not isinstance(hooks_block, dict):
        return
    for groups in hooks_block.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("command"), str):
                    yield entry["command"]


def _read_bounded(path, budget):
    """File bytes, capped by a shared budget; a read failure contributes a marker.

    An unreadable file is not nothing — it is a file whose state we could not
    determine, and hashing a marker keeps that distinct from the file's absence.
    """
    if budget[0] <= 0:
        return b"<truncated>"
    try:
        with open(path, "rb") as fh:
            data = fh.read(budget[0] + 1)
    except OSError:
        return b"<unreadable>"
    if len(data) > budget[0]:
        budget[0] = 0
        return data[: len(data) - 1] + b"<truncated>"
    budget[0] -= len(data)
    return data


def scan(home, enabled_keys):
    """Inventory the enabled plugins.

    Returns `plugins` (one record per resolved key, in key order), `unresolved`
    (enabled keys with no install record — nothing is knowable about them beyond
    the key, and inventing a version would be worse than saying so),
    `installed_unreadable` and the marketplace map used for provenance.
    """
    home = _as_path(home)
    installed, installed_unreadable = read_installed(home)
    marketplaces = read_marketplaces(home)

    records = []
    unresolved = []

    for key in sorted(enabled_keys or ()):
        entry = installed.get(key)
        if entry is None:
            unresolved.append(key)
            continue
        records.append(_plugin_record(key, entry, marketplaces))

    return {
        "plugins": tuple(records),
        "unresolved": tuple(unresolved),
        "installed_unreadable": installed_unreadable,
        "marketplaces": marketplaces,
    }


def _plugin_record(key, entry, marketplaces):
    name, marketplace = _split_key(key)
    install_path = entry.get("installPath")
    on_disk = isinstance(install_path, str) and os.path.isdir(install_path)

    sha, sha_basis = _identity(entry, install_path if on_disk else None)
    provenance = marketplaces.get(marketplace) or {}

    record = {
        "key": key,
        "name": name,
        "marketplace": marketplace,
        "version": entry.get("version"),
        "scope": entry.get("scope"),
        "sha": sha,
        "sha_basis": sha_basis,
        "on_disk": on_disk,
        "marketplace_source": provenance.get("source_type", "unknown"),
        "live_edited": bool(provenance.get("live_edited")),
        # `installedAt` and `lastUpdated` are deliberately not carried. They
        # would make a reinstall read as a new harness.
        "hooks": _plugin_hooks(key, entry, install_path) if on_disk else (),
        "skills": _skill_names(install_path) if on_disk else (),
        "agents": _agent_names(install_path) if on_disk else (),
        "mcp_servers": _mcp_names(install_path) if on_disk else (),
    }
    return record


def _identity(entry, plugin_dir):
    """`(sha, basis)` — the strongest available answer to "which build is this?".

    `git` when the install records a commit. `content` when it does not but the
    directory is readable, so a live-edited plugin still moves. `version-only`
    when neither is available — an honest admission, not a fabricated identity.
    """
    sha = entry.get("gitCommitSha")
    if isinstance(sha, str) and sha:
        return sha, "git"

    if plugin_dir:
        digest = content_digest(plugin_dir)
        if digest:
            return digest, "content"

    return None, "version-only"


def _plugin_hooks(key, entry, plugin_dir):
    block = _read_json(os.path.join(plugin_dir, _HOOKS_MANIFEST))
    if not isinstance(block, dict):
        return ()
    scope = entry.get("scope") if isinstance(entry.get("scope"), str) else "plugin"
    return settings_read.hook_entries(block.get("hooks"), scope, owner=key)


def _skill_names(plugin_dir):
    """Skill directory names that actually hold a `SKILL.md`.

    A stray directory under `skills/` is not a loaded component, and counting it
    would inflate the profile the report prints.
    """
    root = os.path.join(_as_path(plugin_dir), "skills")
    return tuple(
        sorted(
            name
            for name in _listdir(root)
            if os.path.isfile(os.path.join(root, name, "SKILL.md"))
        )
    )


def _agent_names(plugin_dir):
    root = os.path.join(_as_path(plugin_dir), "agents")
    return tuple(
        sorted(
            name[:-3]
            for name in _listdir(root)
            if name.endswith(".md") and os.path.isfile(os.path.join(root, name))
        )
    )


def _mcp_names(plugin_dir):
    data = _read_json(os.path.join(_as_path(plugin_dir), _MCP_MANIFEST))
    if not isinstance(data, dict):
        return ()
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data.get("servers")
    if not isinstance(servers, dict):
        return ()
    return tuple(sorted(str(name) for name in servers))


def user_components(home):
    """User-level skills and agents — source 8, belonging to no plugin.

    Claude Code loads `~/.claude/skills/` and `~/.claude/agents/` alongside every
    plugin's. A plugin-only inventory cannot see them, and they are real
    composition.
    """
    claude = os.path.join(_as_path(home), ".claude")
    return {"skills": _skill_names(claude), "agents": _agent_names(claude)}


def _listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return ()


def _read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def _split_key(key):
    """`"abacus@abacus"` → `("abacus", "abacus")`; a bare key has no marketplace."""
    if "@" in key:
        name, _sep, marketplace = key.rpartition("@")
        return name, marketplace
    return key, None


def _as_path(value):
    return value if isinstance(value, str) else str(value)
