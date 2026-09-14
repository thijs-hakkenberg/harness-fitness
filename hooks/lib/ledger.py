"""Where a composition becomes history.

`composition.build()` produces an identity for the harness as it stands right now.
That identity is only useful across time, and time is what this module adds: a
content-addressed file per composition ever seen, and an append-only log of the
moments the identity moved.

Two properties carry the design, and both exist for a reader who arrives months
later with a question about episodes recorded long ago:

**A composition file is written once and never rewritten.** The digest names the
file's contents, and episodes reference that digest by name. Re-writing
`aaaaaaaaaaaa.json` because the same harness was seen again would retroactively
change what every one of those episodes says it ran under — silently, and in a way
no later report could detect.

**A transition is appended only when the digest actually moves.** This module is
driven from a `SessionStart` hook, so a record per invocation would turn
`changes.jsonl` into a session log. The question it exists to answer — *what
changed between A and B* — would then have to be reconstructed by de-duplicating a
log whose duplicates are indistinguishable from genuine re-entries.

Consent gates every write (ADR-014), and it is checked before any path is even
resolved as a directory. Nothing here creates a directory as a side effect of
resolving a path, which is what makes "an unacknowledged install writes nothing"
assertable by looking at the filesystem rather than at a return value.
"""

import os

import consent
import hfit_config
import hfit_time
import state_store
import taxonomy

# Bumped when a change record's shape moves. Composition records carry
# `composition.SCHEMA` and are stored verbatim; this one describes `changes.jsonl`.
SCHEMA = 1

# Used only when `ledger.in_repo` is on. Named with a leading dot and listed in
# `.gitignore`, because a repository that gains an unignored measurement directory
# on first run has been written to without being asked.
IN_REPO_DIRNAME = ".harness-fitness"

_COMPOSITIONS = "compositions"
_CHANGES = "changes.jsonl"

# The plugin attributes a change record reports movement in. `version` and `sha`
# are the substance; `sha_basis` is here because a plugin dropping from `git` to
# `version-only` means the digest has *stopped being able* to notice it changing,
# which is a change in what the instrument can see and belongs in the log.
_PLUGIN_ATTRS = ("version", "sha", "sha_basis", "on_disk")


def _cfg(cfg):
    return hfit_config.load() if cfg is None else cfg


def root(cwd, cfg=None):
    """The ledger directory for one project. Resolved only — never created.

    `cwd` is passed in rather than read from `$CLAUDE_PROJECT_DIR`, matching
    `composition.inventory`: a caller that already knows which project it means
    must not have that decision overridden by an environment variable.
    """
    cfg = _cfg(cfg)
    settings = cfg.get("ledger") if isinstance(cfg, dict) else None
    if isinstance(settings, dict) and settings.get("in_repo") and cwd:
        return os.path.join(cwd, IN_REPO_DIRNAME)
    return state_store.project_dir(cwd)


def path(cwd, *parts, **kwargs):
    """A path inside the project ledger. Creates nothing."""
    return os.path.join(root(cwd, kwargs.get("cfg")), *parts)


def composition_path(cwd, digest, cfg=None):
    """`compositions/<digest>.json` — the filename *is* the identity.

    Which is why `composition.DIGEST_LEN` is a storage contract rather than a
    display choice: widening it orphans every file already written (ADR-007).
    """
    return os.path.join(root(cwd, cfg), _COMPOSITIONS, "%s.json" % digest)


def changes_path(cwd, cfg=None):
    return os.path.join(root(cwd, cfg), _CHANGES)


def read_composition(cwd, digest, cfg=None):
    """The stored record for `digest`, or `None` if it is not resolvable.

    `None` covers both "never seen" and "unreadable", deliberately: a caller
    cannot act differently on the two, and a diff computed against a partially
    recovered record would be worse than a diff declared unavailable.
    """
    if not digest:
        return None
    return state_store.read_json(composition_path(cwd, digest, cfg))


def changes(cwd, cfg=None):
    """Every readable transition, oldest first."""
    return state_store.read_jsonl(changes_path(cwd, cfg))


def last_change(cwd, cfg=None):
    """The most recent transition carrying a digest, or `None`.

    Scans backwards for a usable `to` rather than taking the final line. A
    *truncated* append is already dropped upstream by `state_store.read_jsonl`;
    what this scan guards is the case that survives that filter — a line that is
    valid JSON and a dict but carries no usable `to`, which is what a newer version
    appending a record kind we do not know about would leave behind.

    Either way the failure it prevents is the same: reading the ledger as empty
    appends a second baseline, putting a fabricated "first sighting" in the middle
    of the history and losing the `from` side of a real transition.
    """
    for record in reversed(changes(cwd, cfg)):
        if isinstance(record, dict) and isinstance(record.get("to"), str):
            return record
    return None


def current_digest(cwd, cfg=None):
    """The digest this project was last observed running under, or `None`."""
    record = last_change(cwd, cfg)
    return record.get("to") if record else None


def record_composition(cwd, record, cfg=None, now=None):
    """Store a composition and log a transition if the digest moved.

    Returns `{"ok", "reason", "status", "digest", "change"}`. `status` is `"new"`
    the first time a digest is stored and `"known"` afterwards; `change` is the
    appended record, or `None` when the digest held still.

    The consent check runs before anything else touches the filesystem, so a
    refusal is observable as an absent state directory rather than only as a
    return value.
    """
    cfg = _cfg(cfg)

    allowed, reason = consent.require_consent(cfg)
    if not allowed:
        return _refusal(reason)

    if not isinstance(record, dict) or not record.get("digest"):
        # Refused rather than stored under a placeholder. A composition with no
        # digest has no identity, and an episode pointing at a placeholder would
        # be grouped with every other undigested record as though they were one
        # harness.
        return _refusal("no_digest")

    digest = record["digest"]
    target = composition_path(cwd, digest, cfg)
    status = "known" if os.path.exists(target) else "new"
    if status == "new":
        state_store.write_json_atomic(target, record)

    previous = current_digest(cwd, cfg)
    if previous == digest:
        return {
            "ok": True,
            "reason": None,
            "status": status,
            "digest": digest,
            "change": None,
        }

    change = _change(cwd, previous, record, cfg, now)
    state_store.append_jsonl(changes_path(cwd, cfg), change)

    return {
        "ok": True,
        "reason": None,
        "status": status,
        "digest": digest,
        "change": change,
    }


def _refusal(reason):
    return {"ok": False, "reason": reason, "status": None, "digest": None, "change": None}


def _change(cwd, previous, record, cfg, now):
    """One `changes.jsonl` entry.

    Three shapes, and the third is the one worth being careful about:

    - **baseline** — a first sighting. Empty diffs, because a first sighting is not
      a change. Listing forty components as `added` would place a fabricated
      steering event at the head of every project's history.
    - **records** — both sides resolvable, so the diff is real.
    - **unavailable** — the previous composition file is gone. Every diff field is
      `null`, never `[]`: an empty list asserts the digest moved while nothing
      about the harness did, which cannot happen. Unknown is never zero.
    """
    entry = {
        "ts": hfit_time.iso(now),
        "schema": SCHEMA,
        "from": previous,
        "to": record["digest"],
        # Carried so a reader of `changes.jsonl` alone can tell whether the two
        # sides were comparable at all, without resolving both compositions
        # (ADR-007).
        "env_hash": record.get("env_hash"),
        "kind": "baseline" if previous is None else "transition",
    }

    if previous is None:
        entry.update(
            {
                "diff_basis": "baseline",
                "added": [],
                "removed": [],
                "changed": [],
                "profile_delta": {},
            }
        )
        return entry

    before = read_composition(cwd, previous, cfg)
    if not isinstance(before, dict):
        entry.update(
            {
                "diff_basis": "unavailable",
                "added": None,
                "removed": None,
                "changed": None,
                "profile_delta": None,
            }
        )
        return entry

    added, removed, changed = _diff(_components(before), _components(record))
    entry.update(
        {
            "diff_basis": "records",
            "added": added,
            "removed": removed,
            "changed": changed,
            # Computed here rather than at read time: the *previous* profile is
            # only reliably in hand at the moment of transition.
            "profile_delta": taxonomy.profile_delta(
                before.get("profile") or {}, record.get("profile") or {}
            ),
        }
    )
    return entry


def _components(record):
    """A composition record flattened to `{id: attrs}`.

    Plugin-owned hooks, skills, agents and MCP servers deliberately do **not** get
    their own ids: they roll up into the plugin, whose version and sha already move
    when they do. Listed separately, a routine plugin upgrade would emit a
    forty-line `added` block and bury the one line a reader came for.
    """
    out = {}

    for entry in record.get("plugins") or ():
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        out["plugin:%s" % entry["key"]] = {
            attr: entry.get(attr) for attr in _PLUGIN_ATTRS
        }

    for key in record.get("unresolved") or ():
        # A separate id from `plugin:<key>`, so a plugin that becomes unresolved
        # reports as one component leaving and another arriving. That is what
        # happened: the plugin's components stopped loading, and an enabled-but-
        # uninstallable entry took their place.
        out["unresolved:%s" % key] = {}

    for entry in record.get("settings_hooks") or ():
        if isinstance(entry, dict):
            out[_hook_id(entry)] = {}

    for name in record.get("project_mcp_servers") or ():
        out["mcp:%s" % name] = {}

    user = record.get("user") if isinstance(record.get("user"), dict) else {}
    for name in user.get("skills") or ():
        out["skill:%s" % name] = {}
    for name in user.get("agents") or ():
        out["agent:%s" % name] = {}

    return out


def _hook_id(entry):
    """`hook:<scope>:<owner>:<event>:<matcher>:<cmd_digest>`.

    Every field is part of the identity because changing any one of them changes
    what the hook reaches. `None` renders as `-` and `""` renders as nothing, which
    keeps the two apart: an absent matcher fires on every tool, an empty one fires
    on none, and a diff that showed them as the same component would report a
    sensor with total reach and a sensor with no reach interchangeably.
    """
    return "hook:%s" % ":".join(
        "-" if entry.get(field) is None else str(entry.get(field))
        for field in ("scope", "owner", "event", "matcher", "cmd_digest")
    )


def _diff(before, after):
    """`(added, removed, changed)` — ids sorted, `changed` reporting only movement.

    A `changed` entry carries only the attributes that actually moved, so a report
    can say "abacus 1.0.0 → 1.1.0" rather than restating four fields of which three
    are identical.
    """
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))

    changed = []
    for component in sorted(set(before) & set(after)):
        moved = [
            attr
            for attr in _PLUGIN_ATTRS
            if before[component].get(attr) != after[component].get(attr)
        ]
        if moved:
            changed.append(
                {
                    "id": component,
                    "from": {attr: before[component].get(attr) for attr in moved},
                    "to": {attr: after[component].get(attr) for attr in moved},
                }
            )

    return added, removed, changed
