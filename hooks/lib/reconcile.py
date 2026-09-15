"""Turn closed beads issues into episodes. Idempotent, and that is the design.

`reconcile` reads the whole closed population and offers every issue to the ledger,
which appends only where a record moved. There is no session state to keep in step
with, so running it twice costs nothing and *missing* the moment of a `bd close`
costs nothing either — which is what lets it hang off whichever hooks are convenient
rather than off one it must never miss. Discovery, not tracking.

Three things it must get right, each of which is a way of being wrong that a caller
could not detect:

**The environment pin is resolved now, not inherited.** A model swap does not move the
`composition_digest`, so nothing is appended and the stored composition keeps the
`env_hash` it was first seen under. Reading that value onto an episode would make it
claim an environment it did not run in, and the comparison layer's refusal on a
differing `env_hash` — the only thing separating "we changed a plugin" from "we
changed the model" — would be checking a fossil. So `env_pin.build` is called per
episode (ADR-007).

**`abacus_models` is the observed model, and it outranks both declarations.** Measured
on this machine the two declared sources disagreed with each other and with reality,
so where an observation exists it is used. It is comma-joined and may name several
models; a mixed episode is passed through whole rather than reduced to one, because
picking would pool it with that model's single-model population and move the delta
silently, where passing it through gives it a population of its own that is small
enough to be refused as `insufficient_n`. Loudly wrong beats quietly wrong.

**A failed `bd` read is not an empty population.** `beads_read` already refuses to
answer `[]` for it, and this layer must not undo that by reporting `0` episodes: zero
is a real claim about the project, indistinguishable from the truth for a new one. So
every count is `None` on a failed read.

Reads only. Writing the `hfit_*` pointer keys back to beads is a separate module, so
that this can run on a `Stop` without putting a `bd` write on the path of every turn.
"""

import os

import beads_read
import consent
import env_pin
import hfit_config
import ledger
import verdict as verdict_lib


# No `SCHEMA` here. `reconcile` writes no record of its own — `ledger.EPISODE_SCHEMA`
# versions the episode shape and is stamped where the write happens. A second,
# unbound version number would be a field nobody bumps and a reader would trust.

# What `reconcile` can report. `ledger.EPISODE_REFUSALS` is absent because a refused
# *issue* is a skip, not a refused run — one malformed row in a Dolt-synced database
# must not stop a project's measures from ever updating again.
REFUSALS = ("not_acknowledged", "config_changed") + beads_read.REASONS

_COUNTS = ("new", "updated", "unchanged", "skipped")

# There is no `isinstance(issue, dict)` guard in the loop below, and that is a
# decision rather than an omission. `beads_read.list_issues` already drops every
# record that is not a dict carrying an id, so a guard here could not be reached by
# any input `bd` can produce — and an unreachable guard is untestable, which means it
# rots without anything going red. The property is asserted at the boundary that owns
# it instead: `test_a_non_dict_in_the_issue_list_does_not_raise` here and
# `test_an_issue_with_no_id_is_dropped_rather_than_carried` in `test_beads_read.py`.
# Loosening that filter turns both red at once, which is the coupling we want.


def reconcile(cwd, now=None, cfg=None):
    """Reconcile every closed issue in `cwd` into the episode ledger.

    `now` is a **datetime**, matching `hfit_time.iso`, which returns `None` for
    anything else — an ISO string passed here would store `ts: null` on every episode
    and nothing would fail.

    Returns `{"ok", "reason", "episodes", "new", "updated", "unchanged", "skipped"}`.
    Counts are `None` rather than absent on a refusal: this is a library, and
    `dict.get()` cannot tell an absent key from a null one in process. They are never
    `0`, because zero is an answer.
    """
    cfg = hfit_config.load() if cfg is None else cfg

    # Before the subprocess, not after it. Running `bd` first would mean an
    # unacknowledged install still executed a process in the user's repository on
    # every trigger, whatever the return value said (ADR-014).
    allowed, reason = consent.require_consent(cfg)
    if not allowed:
        return _refusal(reason)

    read = beads_read.closed_issues(cwd)
    if not read["ok"]:
        return _refusal(read["reason"])

    # Resolved once: it is a property of the project at reconcile time, not of the
    # issue, and `current_digest` re-reads `changes.jsonl` on every call.
    digest = ledger.current_digest(cwd, cfg)
    home = os.path.expanduser("~")

    counts = dict((key, 0) for key in _COUNTS)
    for issue in read["issues"] or []:
        outcome = ledger.record_episode(
            cwd, _episode(home, cwd, issue, digest), cfg=cfg, now=now
        )
        if outcome["ok"]:
            counts[outcome["status"]] += 1
        else:
            counts["skipped"] += 1

    result = {
        "ok": True,
        "reason": None,
        "episodes": counts["new"] + counts["updated"] + counts["unchanged"],
    }
    result.update(counts)
    return result


def _refusal(reason):
    """No counts, because none of them are known. Unknown is never zero."""
    result = {"ok": False, "reason": reason, "episodes": None}
    result.update(dict((key, None) for key in _COUNTS))
    return result


def _episode(home, cwd, issue, digest):
    """One episode record, built from the issue and the environment it closed in.

    Neither the title nor the close reason is carried through. The ledger holds
    measurements, not a copy of the issue tracker, and the verdict already carries
    everything a measure needs from the text.
    """
    meta = issue.get("metadata")
    meta = meta if isinstance(meta, dict) else {}

    reading = verdict_lib.classify(issue)
    pin = env_pin.build(home, cwd, observed=_observed(meta))

    return {
        "issue_id": issue.get("id"),
        "closed_at": issue.get("closed_at"),
        # The window every OTEL-derived measure is computed over.
        "started_at": issue.get("started_at"),
        # Carried so the `issue_type_mix_shifted` confounder is detectable — a delta
        # over a run of chores compared against a run of features is not a delta.
        "issue_type": issue.get("issue_type"),
        "composition_digest": digest,
        "env_hash": pin.get("env_hash"),
        "model_basis": pin.get("model_basis"),
        "verdict": reading["verdict"],
        "verdict_basis": reading["basis"],
        "verdict_reason": reading["reason"],
        "lexicon_version": reading["lexicon_version"],
        "capture_ok": reading["capture_ok"],
        "partial": reading["partial"],
        "tokens_total": _count(meta.get("abacus_tokens_total")),
        "tool_calls": _count(meta.get("abacus_tool_calls")),
    }


def _observed(meta):
    """The models abacus recorded for this episode, or `None` if it recorded none.

    `None` rather than `{"model": None}` says "nothing was offered" instead of "we
    looked and could not tell". The two are **indistinguishable in behaviour** —
    `env_pin._model` reads the key through `_text`, so a null value and an absent dict
    both fall through to the declarations, and `_versions` treats both as empty. So no
    test asserts the difference and mutating one into the other survives the suite.
    That is recorded here rather than left for a reader to discover, because the
    tempting repair is to write a test for it, and there is no behaviour to assert.
    """
    models = meta.get("abacus_models")
    if isinstance(models, str) and models.strip():
        # Passed through as abacus joined it. Splitting on the comma and choosing
        # would be the under-split failure: a mixed-model episode hashing as though
        # it ran on one of them.
        return {"model": models.strip()}
    return None


def _count(value):
    """A figure abacus recorded, or `None` — never a substitute for one.

    Metadata is hand-editable and Dolt-synced from other machines, so this field can
    hold anything. A `0` invented here would be summed into the numerator of tokens
    per outcome and make the harness look most efficient exactly where the
    measurement failed, and a string would fail far from the issue that caused it.

    `bool` is excluded explicitly because it is an `int` subclass, so a hand-set
    `true` would otherwise arrive as a token count of 1.
    """
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None
