"""The bounded cross-machine index, and the one namespace boundary that raises.

`episodes.jsonl` is the store of record. These keys are an *index*: enough of an
episode to make it findable and comparable from another machine, written onto the
closed issue so it travels with `bd`'s Dolt sync rather than needing a sync of its own
(`contracts/output/bd-metadata-write.md`).

That is a write into a data store this plugin does not own, shared with a tool it does
not control, replicated to machines it cannot see. Three consequences:

**An `abacus_` key raises rather than refusing.** A refusal is a return value and a
return value can be ignored; an ignored refusal here is a silent overwrite of another
tool's cost figures. And unlike every other error in this module, a misnamespaced key
cannot arrive from a malformed database or a hostile id — only from a defect in this
repository, which should behave like one. `hook_io.fail_open` catches it at the hook
boundary, so raising costs a caller nothing it did not already handle.

**An unmeasured value is an absent key, never a zero.** This is the usual rule, with
teeth it lacks in a local file: `hfit_hard_touches: 0` would replicate as the most
flattering possible value — a fully autonomous episode — with nothing recording that
nobody had measured it. Eight of the declared keys have no implementation until later
releases, and until then they must not appear.

**A whitespace-bearing value silently truncates.** Measured on bd 1.1.2: `bd` takes it
as two words and keeps the first. That is worse than an error, because nothing
downstream can tell. So every value is rendered as one whitespace-free token.
"""

import json
import subprocess

import beads_read
import consent
import env_pin
import hfit_config
import verdict as verdict_lib


# The index shape, versioned independently of `ledger.EPISODE_SCHEMA`: the ledger may
# gain a field this index does not carry, and a reader on another machine has only this
# number to go on.
SCHEMA = 1

# Ours, and only ours (§"The namespace boundary").
PREFIX = "hfit_"

# The bounded vocabulary. Enforced rather than intended — see `_check`. Sorted so the
# declaration order cannot drift from the emitted order.
KEYS = (
    "hfit_autonomy_confidence",
    "hfit_capture_ok",
    "hfit_composition_digest",
    "hfit_env_hash",
    "hfit_escapes",
    "hfit_fb_catches",
    "hfit_ff_catches",
    "hfit_hard_touches",
    "hfit_model_basis",
    "hfit_schema",
    "hfit_soft_touches",
    "hfit_tokens_basis",
    "hfit_touch_kinds",
    "hfit_verdict",
    "hfit_verdict_basis",
)

# What this module can report. `beads_read.REASONS` is reused whole rather than
# filtered: the environmental failures are identical because the subprocess discipline
# is, and a second, nearly-identical vocabulary would be one a caller had to branch on
# twice. `unparseable` is not reachable on a write and is carried anyway — a narrower
# tuple would have to be re-widened the moment a write starts reading output.
REFUSALS = (
    "not_acknowledged",
    "config_changed",
    "writes_disabled",
    "no_issue_id",
    "nothing_to_write",
) + beads_read.REASONS

# The episode fields this release can index, mapped to their keys. Absent from this
# table is how a not-yet-implemented measure stays out of the write: there is no entry
# to omit a value for, so no later edit can accidentally supply a default.
_FROM_EPISODE = (
    ("composition_digest", "hfit_composition_digest"),
    ("env_hash", "hfit_env_hash"),
    ("model_basis", "hfit_model_basis"),
    ("verdict", "hfit_verdict"),
    ("verdict_basis", "hfit_verdict_basis"),
    ("capture_ok", "hfit_capture_ok"),
)

# Validators for the fields whose values are a closed vocabulary. Metadata is
# hand-editable and arrives from other machines, so an unrecognised value can reach
# this layer; writing it back would launder it into the index other machines read as
# authoritative.
_VOCABULARIES = {
    "hfit_verdict": verdict_lib.VERDICTS,
    "hfit_verdict_basis": verdict_lib.BASES,
    "hfit_model_basis": env_pin.MODEL_BASES,
}


def write_episode(cwd, episode, cfg=None):
    """Index one episode onto its beads issue.

    Returns `{"ok", "reason", "keys_written"}`. `keys_written` is `None` on every
    refusal rather than `0`: `bd update` is not observably atomic across keys from this
    side, so after a failure the number written is genuinely unknown, and `0` would be
    a stronger claim than the evidence supports.
    """
    cfg = hfit_config.load() if cfg is None else cfg
    episode = episode if isinstance(episode, dict) else {}

    # Before the subprocess. An unacknowledged install must not run a process in the
    # user's repository, let alone write to its database (ADR-014).
    allowed, reason = consent.require_consent(cfg)
    if not allowed:
        return _refusal(reason)

    beads = cfg.get("beads")
    if not (beads if isinstance(beads, dict) else {}).get("write_metadata"):
        # No `bd` process at all, not a suppressed argument. The ledger is the store
        # of record, so this whole index is optional and saying no must mean it.
        return _refusal("writes_disabled")

    issue_id = episode.get("issue_id")
    if not isinstance(issue_id, str) or not issue_id.strip():
        # `bd update` with no id would either fail or, worse, match something else.
        return _refusal("no_issue_id")

    pairs = _pairs(episode)
    if not pairs:
        return _refusal("nothing_to_write")

    # `hfit_schema` only once there is something for it to describe. Alone it would
    # claim an index exists where none does.
    pairs["hfit_schema"] = SCHEMA

    return set_metadata(cwd, issue_id.strip(), pairs, cfg=cfg)


def set_metadata(cwd, issue_id, pairs, cfg=None):
    """Merge `pairs` into the issue's metadata in one `bd` invocation.

    One call, because `--set-metadata` merges: a process per key would put six
    subprocesses on a `Stop` hook for no gain, each an independent chance to
    half-write the index.

    Raises `ValueError` for any key outside `KEYS` — see the module docstring.
    """
    cfg = hfit_config.load() if cfg is None else cfg
    pairs = pairs if isinstance(pairs, dict) else {}

    # Over the whole set, before anything runs. A per-key check would permit a
    # half-write: some keys landed, the caller saw an exception, and nothing records
    # which half succeeded.
    _check(pairs)

    allowed, reason = consent.require_consent(cfg)
    if not allowed:
        return _refusal(reason)

    if not pairs:
        return _refusal("nothing_to_write")

    args = ["update", issue_id]
    for key in sorted(pairs):
        args += ["--set-metadata", "%s=%s" % (key, _token(pairs[key]))]

    reason = _run(args, cwd)
    if reason:
        return _refusal(reason)
    return {"ok": True, "reason": None, "keys_written": len(pairs)}


def _check(pairs):
    """Raise unless every key is one this module is allowed to write.

    Two separate messages, because the two mistakes have different repairs: an
    `abacus_` key means the caller has reached into another tool's namespace, and an
    unknown `hfit_` one means the vocabulary in `KEYS` and this contract need updating
    together.
    """
    for key in sorted(pairs):
        if not isinstance(key, str) or not key.startswith(PREFIX):
            raise ValueError(
                "beads_write may only write %s* keys, got %r. `abacus_*` belongs to "
                "abacus's attribution module and nothing else may construct it."
                % (PREFIX, key)
            )
        if key not in KEYS:
            raise ValueError(
                "%r is not a declared metadata key. The set is bounded on purpose — "
                "a per-event key would grow metadata without limit on an issue abacus "
                "also writes to. Add it to KEYS and to "
                "contracts/output/bd-metadata-write.md together." % key
            )


def _pairs(episode):
    """The indexable values this episode actually has.

    A `None` is dropped rather than written: `hfit_composition_digest=None` would
    round-trip as the string `"None"` and pool every such episode under a digest that
    does not exist. A value outside its declared vocabulary is dropped for the
    stronger reason — it would become authoritative on the next machine to read it.
    """
    pairs = {}
    for field, key in _FROM_EPISODE:
        value = episode.get(field)
        if value is None:
            continue
        vocabulary = _VOCABULARIES.get(key)
        if vocabulary is not None and value not in vocabulary:
            continue
        pairs[key] = value
    return pairs


def _token(value):
    """One argv token, guaranteed whitespace-free.

    `bd` takes a value containing a space as two words and keeps the first, silently.
    Bools render as JSON literals so they round-trip as bools rather than as the
    Python-cased strings `str()` would give.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, separators=(",", ":"), sort_keys=True)
    else:
        text = str(value)
    return "_".join(text.split())


def _run(args, cwd):
    """Run `bd`. Returns a refusal reason, or `None` on success.

    The discipline mirrors `beads_read._run`, deliberately rather than incidentally:
    `shell=False` with an explicit argv, because an issue id reaches this module from
    a `bd close` command line parsed out of a hook payload and a metacharacter in one
    must be an inert string.
    """
    try:
        proc = subprocess.run(
            [beads_read.BD] + list(args),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=beads_read.TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except (OSError, ValueError):
        return "bd_unavailable"

    if proc.returncode == 127:
        return "bd_unavailable"

    if proc.returncode != 0:
        stderr = proc.stderr or b""
        if not isinstance(stderr, str):
            stderr = stderr.decode("utf-8", "replace")
        if beads_read._NO_DB_MARKER in stderr.lower():
            # A project that does not use beads, which is benign. Reporting it as
            # `bd_error` would make it indistinguishable from a real defect.
            return "no_database"
        return "bd_error"

    return None


def _refusal(reason):
    """Nothing written, and the count unknown rather than zero."""
    return {"ok": False, "reason": reason, "keys_written": None}
