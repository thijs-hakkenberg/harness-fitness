"""Which axis of the steering loop each harness component sits on.

Two axes, from `Workshop.HarnessEngineering/build-methods-plugins-agent-sdk.md`
§03. **Feedforward** steers before the work; **feedback** observes after it.
**Computational** is deterministic; **inferential** needs a model's judgement.
Data and packaging sit on neither and are counted separately rather than forced
into a bucket they do not belong in.

The table is static. Classification never consults a model: a profile whose
counts depended on an LLM's opinion of what a hook is would not reproduce, and
the composition digest it feeds would move on its own.

Four rules are load-bearing.

**`PreToolUse` is feedforward, against the source table** (ADR-005). The workshop
files it under FB·COMP because it validates and blocks. But a deny lands *before*
the action does, so it prevents rather than detects. Filing it as feedback makes
M4 report abacus's edit gate as a feedback control, which is the opposite of what
it is. The event list lives in `hfit_config.ff_hook_events`, so the judgement is
inspectable and reversible without editing this module — and `basis` says
`adr-005` whenever the answer here differs from the table, so a reader can see
exactly where the divergence is.

**A sub-agent's direction is not determinable.** The workshop says "FF + FB · INF
— direction depends on the role": generators steer, evaluators sense. Nothing on
disk distinguishes them, so a sub-agent counts as `both_inf`. Defaulting it to
`ff_inf` would file every review agent among the guides, and the under-count
would be invisible.

**An unrecognised kind or event is `unknown`.** A future Claude Code release will
add a hook event. Defaulting it to feedback would inflate the sensor count of
every harness that adopts it, and a sensor count that grows on its own is worse
than one that admits a gap.

**A profile always carries every bucket.** `profile_delta` subtracts two of them
and a report tabulates them; a key that appeared only when non-zero would make
both a special case.
"""

import hfit_config

# The order a report prints. `unknown` last, and present rather than hidden: a
# component the table cannot place is still part of the harness.
BUCKETS = (
    "ff_comp",
    "ff_inf",
    "fb_comp",
    "fb_inf",
    "both_inf",
    "substrate",
    "packaging",
    "unknown",
)

_LABELS = {
    "ff_comp": "FF · COMP",
    "ff_inf": "FF · INF",
    "fb_comp": "FB · COMP",
    "fb_inf": "FB · INF",
    "both_inf": "FF+FB · INF",
    "substrate": "SUBSTRATE",
    "packaging": "PACKAGING",
    "unknown": "unknown",
}

# §03, transcribed. `(direction, mechanism)` per component kind; `hook` is absent
# because its direction depends on the event and is resolved below.
_KINDS = {
    "skill": ("ff", "inf"),
    "command": ("ff", "inf"),
    "agent": ("both", "inf"),
    "mcp_server": (None, "substrate"),
    "manifest": (None, "packaging"),
    "marketplace": (None, "packaging"),
}

# The five hook events the workshop table names, with the direction it gives
# them. Used only to decide whether a classification agrees with the table or
# diverges from it — the direction actually returned comes from config.
_TABLE_EVENTS = {
    "SessionStart": "ff",
    "UserPromptSubmit": "ff",
    "PreToolUse": "fb",
    "PostToolUse": "fb",
    "Stop": "fb",
}

# Events Claude Code emits that the table does not mention. Listed so that an
# event this plugin knows about is classified, and one it does not is flagged.
# Only events observed to exist belong here: adding a plausible-sounding name
# would classify a typo as a working sensor.
_OTHER_EVENTS = (
    "SubagentStop",
    "PreCompact",
    "SessionEnd",
    "Notification",
    "PermissionRequest",
)

KNOWN_HOOK_EVENTS = tuple(sorted(set(_TABLE_EVENTS) | set(_OTHER_EVENTS)))

_DEFAULT_FF_EVENTS = tuple(hfit_config.DEFAULTS["ff_hook_events"])


def label(bucket):
    """A bucket rendered for a human — `"FB · COMP"`, not `"fb_comp"`."""
    return _LABELS.get(bucket, "unknown")


def classify(kind, event=None, ff_events=None):
    """Place one component: `{direction, mechanism, bucket, basis}`.

    `kind` is one of `_KINDS` or `"hook"`. `event` applies to hooks only.

    `ff_events` overrides the feedforward event list; when omitted the packaged
    default is used. A caller that has already loaded config should pass
    `cfg["ff_hook_events"]`, because reading config once per component would put
    a file read on the `SessionStart` path for every hook in the harness.
    """
    if kind == "hook":
        direction, basis = _hook_direction(event, ff_events)
        if direction is None:
            return _unknown("unrecognised-event")
        return _result(direction, "comp", basis)

    axes = _KINDS.get(kind)
    if axes is None:
        return _unknown("unrecognised-kind")

    direction, mechanism = axes
    if direction is None:
        # Substrate and packaging are their own buckets: an MCP server is what
        # both guides and sensors are built on, and forcing it onto the FF/FB
        # axis would double-count the thing it supports.
        return _result(direction, mechanism, "table")

    # A sub-agent is genuinely both, and saying so is the honest answer rather
    # than a hedge — a consumer splitting guides from sensors needs to see that
    # this one component cannot be split.
    basis = "table-ambiguous" if direction == "both" else "table"
    return _result(direction, mechanism, basis)


def _hook_direction(event, ff_events):
    """`(direction, basis)` for a hook event, or `(None, None)` if unrecognised."""
    if not isinstance(event, str) or not event:
        return None, None

    if ff_events is None:
        ff_events = _DEFAULT_FF_EVENTS

    if event in ff_events:
        direction = "ff"
    elif event in _TABLE_EVENTS or event in _OTHER_EVENTS:
        direction = "fb"
    else:
        return None, None

    # `table` when this agrees with §03, `adr-005` when it does not — including
    # every event §03 never mentioned, whose direction is this module's judgement
    # rather than the workshop's.
    basis = "table" if _TABLE_EVENTS.get(event) == direction else "adr-005"
    return direction, basis


def _result(direction, mechanism, basis):
    return {
        "direction": direction if direction is not None else mechanism,
        "mechanism": mechanism,
        "bucket": _bucket(direction, mechanism),
        "basis": basis,
    }


def _bucket(direction, mechanism):
    if direction is None:
        return mechanism
    bucket = "%s_%s" % (direction, mechanism)
    return bucket if bucket in _LABELS else "unknown"


def _unknown(basis):
    return {
        "direction": "unknown",
        "mechanism": "unknown",
        "bucket": "unknown",
        "basis": basis,
    }


def profile(components, ff_events=None):
    """Count components into buckets — every bucket present, zero-filled.

    `components` is an iterable of `{"kind": ..., "event": ...}`. A malformed
    entry counts as `unknown` rather than being skipped: a component the taxonomy
    cannot place is still part of the harness, and dropping it would under-report
    the composition it is meant to describe.
    """
    counts = dict.fromkeys(BUCKETS, 0)

    for component in components or ():
        if not isinstance(component, dict):
            counts["unknown"] += 1
            continue
        got = classify(
            component.get("kind"),
            event=component.get("event"),
            ff_events=ff_events,
        )
        counts[got["bucket"]] += 1

    return counts


def profile_delta(before, after):
    """Buckets that moved between two profiles, signed; `{}` when nothing did.

    Only non-zero entries appear, so a report can render the whole delta as the
    sentence "B added two FB·COMP sensors". A bucket missing from either side
    reads as zero — a profile written by an older version will not have a bucket
    added later, and the delta must degrade to a number rather than take down the
    report that was explaining the change.

    A bucket this version does not know is compared anyway, in `BUCKETS` order
    first and then alphabetically. A profile written by a *newer* version can
    carry one, and dropping it would hide a real composition change behind a
    version skew.
    """
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}

    unknown_buckets = sorted(k for k in set(before) | set(after) if k not in _LABELS)

    delta = {}
    for bucket in BUCKETS + tuple(unknown_buckets):
        moved = _count(after, bucket) - _count(before, bucket)
        if moved:
            delta[bucket] = moved
    return delta


def _count(profile_map, bucket):
    value = profile_map.get(bucket, 0)
    return value if isinstance(value, int) else 0
