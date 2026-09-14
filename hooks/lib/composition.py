"""One identity for "what the harness was made of".

Episodes are grouped by `(composition_digest, env_hash)`, which makes this
module's output the primitive every measure rests on. The digest therefore has to
satisfy a two-sided property, and both sides cost something different:

- **Missing a change** merges two harnesses into one population, and the measure
  is computed across the mixture and reported as if it described one thing — a
  wrong number presented as a right one.
- **Moving spuriously** splits ten episodes into two groups of five, each below
  `min_episodes_per_digest`, so the measure comes back `insufficient_n` for a
  reason no report reader could trace back to a permissions grant.

So the split between what enters `canonical()` and what stays in the record is
the whole design, and each exclusion below names the failure it avoids.

`env_hash` is deliberately outside the digest (ADR-007). Model, effort and base
URL decide whether two runs are comparable at all; composition decides what is
being compared. Folded together, a model swap would read as a harness change.

**The `env` block is never read.** Not filtered — never opened. `settings_read`
exposes it, and this module does not ask for it, because
`~/.claude/settings.json` is where a live credential lives and a filter is a list
someone has to keep correct forever. The same discipline applies one level down:
hook and MCP command strings arrive already reduced to `cmd_basename` +
`cmd_digest` (ADR-010), so no raw command can reach a record even by accident.
"""

import hashlib
import json

import hfit_config
import hfit_time
import plugin_scan
import settings_read
import taxonomy

# Bumped when the record shape changes. Read by consumers; deliberately *not* in
# the digest — a schema bump would re-digest an unchanged harness and split its
# episode population for a reason that is not a harness change.
SCHEMA = 1

# Twelve, and deliberately *not* the width of `env_hash`, which is a full 64
# (`rig/schemas/result.schema.json` pins it to `^[0-9a-f]{64}$`, so a truncated one
# fails `--rig-handoff`). The asymmetry looks like an inconsistency and is not:
# twelve is the filename of `compositions/<digest12>.json`, which makes this width
# a storage contract, while sixty-four is imposed by the handoff schema. Widening
# this one to "match" would orphan every already-written composition file;
# narrowing `env_hash` to match would break validation (ADR-007).
DIGEST_LEN = 12


def digest(canonical_form):
    """A stable digest of an already-canonical structure.

    `sort_keys=True` is what makes two settings files with identical content but
    different key order produce one identity; `separators` removes the whitespace
    a pretty-printer would otherwise fold into the hash. `default=str` keeps a
    stray non-serialisable value from raising inside a `SessionStart` hook — a
    degraded digest beats a broken session.
    """
    blob = json.dumps(
        canonical_form, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:DIGEST_LEN]


def inventory(home, project_root):
    """Read the eight disk sources. No subprocess, no `bd`, no network.

    `home` and `project_root` are passed in rather than resolved from the
    environment. `settings_read.read()` derives both itself, which is right for a
    hook payload and wrong here: two sources of truth for the same two paths would
    let `$CLAUDE_PROJECT_DIR` steer an inventory whose caller already knew which
    project it meant.
    """
    layers, unreadable = settings_read.read_layers(home, project_root)

    enabled = settings_read.enabled_plugin_keys(layers)
    scanned = plugin_scan.scan(home, enabled)
    mcpjson_enabled = settings_read.mcpjson_enabled(layers)

    return {
        "plugins": scanned["plugins"],
        "unresolved": scanned["unresolved"],
        "marketplaces": scanned["marketplaces"],
        "installed_unreadable": scanned["installed_unreadable"],
        "settings_hooks": settings_read.settings_hooks(layers),
        "mcpjson_enabled": mcpjson_enabled,
        "project_mcp_servers": _project_mcp_servers(mcpjson_enabled, project_root),
        "user": plugin_scan.user_components(home),
        "settings_unreadable": tuple(unreadable),
    }


def _project_mcp_servers(enabled, project_root):
    """Source 6 — the project's `.mcp.json`, narrowed to what is switched on.

    Declaring a server is not enabling one. A `.mcp.json` is committed to the
    repository, so counting every entry would put a teammate's unapproved server
    in this machine's composition and move the digest on a `git pull` that changed
    no local behaviour.
    """
    if not project_root or not enabled:
        return ()

    declared = plugin_scan.mcp_names(project_root)
    if enabled == settings_read.ALL_MCPJSON:
        return declared

    # Only servers that are both declared and enabled. An enabled name with no
    # entry in the file is not substrate — nothing connects — and reporting it
    # would count a stale settings line as a component.
    return tuple(name for name in declared if name in enabled)


def canonical(inv):
    """Exactly the subset identity is computed over.

    Every field of the inventory is either here or excluded on purpose:

    - `marketplaces` — excluded. `marketplace_source` and `live_edited` already
      ride on each plugin, and a marketplace's repo URL changing for the same
      installed sha is provenance, not composition.
    - `name` / `marketplace` — excluded, both derivable from `key`.
    - `mcpjson_enabled` — excluded; the *resolved* server list is what loaded, and
      `"*"` versus the explicit list it resolves to is the same harness. Including
      the raw setting would also move the digest when a stale entry enables a
      server the project no longer declares, which connects nothing.
    - `cmd_basename` — excluded; `cmd_digest` covers the command exactly, and a
      basename is derived from it.
    - `flags`, `captured_at`, `env_hash`, `SCHEMA` — excluded. None of them is a
      property of the harness.
    - `on_disk` — **included.** A plugin whose directory has vanished contributes
      no components, and that is a real change to what loaded.
    """
    return {
        "plugins": [_canonical_plugin(p) for p in inv["plugins"]],
        "settings_hooks": _canonical_hooks(inv["settings_hooks"]),
        "project_mcp_servers": sorted(inv["project_mcp_servers"]),
        "user_skills": sorted(inv["user"]["skills"]),
        "user_agents": sorted(inv["user"]["agents"]),
        # Nothing is knowable about these beyond the key, but switching one on is
        # still a change to the harness and must not vanish from the identity.
        "unresolved": sorted(inv["unresolved"]),
    }


def _canonical_plugin(record):
    return {
        "key": record.get("key"),
        "version": record.get("version"),
        "scope": record.get("scope"),
        "sha": record.get("sha"),
        "sha_basis": record.get("sha_basis"),
        "on_disk": bool(record.get("on_disk")),
        "hooks": _canonical_hooks(record.get("hooks")),
        "skills": sorted(record.get("skills") or ()),
        "agents": sorted(record.get("agents") or ()),
        "mcp_servers": sorted(record.get("mcp_servers") or ()),
    }


def _canonical_hooks(hooks):
    """Hook records sorted into a stable order, reduced to what identifies them.

    Sorted here rather than at the source: `settings_read.settings_hooks` keeps
    declaration order because that is execution order, and a record should show
    the harness as declared. Only the digest needs order not to matter.

    `matcher` is normalised through `_sort_key` rather than by defaulting it,
    because `None` (fires on every tool) and `""` (fires on none) are different
    reaches and collapsing them would misreport a sensor.
    """
    reduced = [
        {
            "event": hook.get("event"),
            "matcher": hook.get("matcher"),
            "type": hook.get("type"),
            "timeout": hook.get("timeout"),
            "cmd_digest": hook.get("cmd_digest"),
            "scope": hook.get("scope"),
            "owner": hook.get("owner"),
        }
        for hook in hooks or ()
        if isinstance(hook, dict)
    ]
    return sorted(reduced, key=_sort_key)


def _sort_key(hook):
    return tuple(
        "\x00" if hook.get(field) is None else str(hook.get(field))
        for field in ("event", "matcher", "type", "cmd_digest", "scope", "owner")
    )


def components(inv):
    """Every component as a `{kind, event}` pair, for `taxonomy.profile`.

    A resolved plugin contributes a `manifest`, and each distinct marketplace it
    came from contributes one `marketplace`. Both are counted from the plugin keys
    rather than from `known_marketplaces.json`: a marketplace nothing was
    installed from is not part of the harness, and one whose entry never got
    written still is.

    An unresolved plugin contributes nothing. It is named in `unresolved` and
    flagged, but crediting it with a manifest would claim a component that is not
    installed.
    """
    out = []
    marketplaces = set()

    for plugin in inv["plugins"]:
        out.append({"kind": "manifest"})
        if plugin.get("marketplace"):
            marketplaces.add(plugin["marketplace"])
        for hook in plugin.get("hooks") or ():
            out.append({"kind": "hook", "event": hook.get("event")})
        for _skill in plugin.get("skills") or ():
            out.append({"kind": "skill"})
        for _agent in plugin.get("agents") or ():
            out.append({"kind": "agent"})
        for _server in plugin.get("mcp_servers") or ():
            out.append({"kind": "mcp_server"})

    out.extend({"kind": "marketplace"} for _name in marketplaces)

    for hook in inv["settings_hooks"]:
        out.append({"kind": "hook", "event": hook.get("event")})
    for _server in inv["project_mcp_servers"]:
        out.append({"kind": "mcp_server"})
    for _skill in inv["user"]["skills"]:
        out.append({"kind": "skill"})
    for _agent in inv["user"]["agents"]:
        out.append({"kind": "agent"})

    return out


def flags(inv):
    """Everything a report has to disclose about how complete this inventory is.

    Each kind marks a way the digest can be less trustworthy than it looks, and
    all of them are things a stable digest would otherwise imply away.
    """
    found = []

    for scope, reason in inv["settings_unreadable"]:
        # One typo must cost one layer, not the session — but the report has to be
        # able to say the inventory was partial.
        found.append(
            {"kind": "settings-unreadable", "detail": scope, "reason": reason}
        )

    if inv["installed_unreadable"]:
        # Every enabled plugin becomes unresolved when this file will not parse.
        # Unflagged, that reads as a harness with no plugins.
        found.append(
            {
                "kind": "installed-unreadable",
                "detail": "installed_plugins.json",
                "reason": inv["installed_unreadable"],
            }
        )

    for plugin in inv["plugins"]:
        if plugin.get("live_edited"):
            # A directory marketplace is a working copy: its contents move with
            # no version and no sha moving.
            found.append({"kind": "live-edited", "detail": plugin.get("key")})
        if plugin.get("sha_basis") == "version-only":
            # No sha and no readable directory: the digest cannot notice this
            # plugin changing, so a stable digest must not imply a stable harness.
            found.append({"kind": "weak-identity", "detail": plugin.get("key")})

    for key in inv["unresolved"]:
        # `unresolved` is the data; the flag is what a report surfaces. Both,
        # because a reader should not have to know to look.
        found.append({"kind": "plugin-unresolved", "detail": key})

    return found


def build(home, project_root, cfg=None, env_hash=None, model_basis=None, now=None):
    """The full composition record, digest included.

    `cfg` is loaded once when omitted, so `ff_hook_events` is read a single time
    rather than per component — this runs on the `SessionStart` path, and a config
    read per hook in the harness is a latency cost paid by the tool that exists to
    measure latency costs.

    `env_hash` is carried, never folded in, and stays `None` when absent: a
    consumer comparing two compositions must be able to refuse on a missing
    `env_hash`, and only `null` is unambiguous.

    `model_basis` is carried on exactly the same terms and for a reason ADR-007
    makes explicit: the comparison layer is obliged to refuse on a differing basis
    just as it refuses on a differing hash, and it cannot honour that from stored
    data unless the basis is stored. Two pins that hash alike are comparable only
    if they were resolved the same way, so a record carrying the hash without the
    basis presents that hash as more trustworthy than it is.

    The `None`/`"unknown"` distinction is load-bearing and preserved verbatim:
    `env_pin` returns `"unknown"` when it looked and could not resolve a model,
    while `None` here means no pin was supplied at all. Only the first is
    comparable against another `"unknown"`.
    """
    if cfg is None:
        cfg = hfit_config.load()
    ff_events = cfg.get("ff_hook_events") if isinstance(cfg, dict) else None

    inv = inventory(home, project_root)

    return {
        "schema": SCHEMA,
        "digest": digest(canonical(inv)),
        "env_hash": env_hash if env_hash else None,
        # Not `if model_basis else None` — an empty string is a caller bug and
        # should be visible as one, where an absent pin is a legitimate state.
        "model_basis": model_basis,
        "captured_at": hfit_time.iso(now),
        "profile": taxonomy.profile(components(inv), ff_events=ff_events),
        "plugins": [dict(p) for p in inv["plugins"]],
        "settings_hooks": [dict(h) for h in inv["settings_hooks"]],
        "project_mcp_servers": list(inv["project_mcp_servers"]),
        # The raw setting beside the resolved list, because `"*"` and an explicit
        # list that happen to resolve alike are the same harness but not the same
        # decision, and only the record can show which one a reader is looking at.
        "mcpjson_enabled": list(inv["mcpjson_enabled"]),
        "user": {
            "skills": list(inv["user"]["skills"]),
            "agents": list(inv["user"]["agents"]),
        },
        "unresolved": list(inv["unresolved"]),
        "marketplaces": dict(inv["marketplaces"]),
        "flags": flags(inv),
    }
