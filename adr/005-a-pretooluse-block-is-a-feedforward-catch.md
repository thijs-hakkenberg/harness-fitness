# ADR-005 — A `PreToolUse` block is a feedforward catch, against the source table

- **Status:** accepted
- **Date:** 2026-09-14
- **Supersedes:** the classification of `PreToolUse` in `Workshop.HarnessEngineering/build-methods-plugins-agent-sdk.md` §03, for this plugin's purposes only

## Context

The workshop's component table is the specification `taxonomy.py` transcribes. It
files hooks as:

> **Hooks** · `hooks/hooks.json` · FB · COMP (+ FF) — **PreToolUse / PostToolUse /
> Stop** = deterministic sensors that validate & block. **SessionStart /
> UserPromptSubmit** inject context = feedforward.

Read as a description of *mechanism*, that is right: all three are hooks, they all
run deterministically, and all three can return a blocking decision.

Measure M4 does not ask about mechanism. It asks **whether an issue was caught
before it happened or after** — `feedforward_ratio = ff / (ff + fb)`, with
`escape_rate` as the invariant. On that question the three events differ
completely. A `PostToolUse` block observes a file that has already been written
and asks for it to be fixed. A `PreToolUse` deny means the write **never
happened**. The first is detection; the second is prevention.

The concrete cost of getting this wrong is measurable on this machine today.
abacus's edit gate is a `PreToolUse` hook: it refuses an `Edit` when no beads task
is claimed, so the edit does not occur and the human claims a task instead.
Classified as feedback, that gate would be counted among the sensors that catch
problems after the fact — and a harness whose *only* control was that gate would
report `feedforward_ratio = 0.0`, describing a purely preventive harness as purely
reactive. The number would not merely be imprecise; it would carry the opposite
sign to the truth.

`PermissionRequest` is the same case and the table does not mention it at all: a
permission prompt interposes before the action, so a denial there is prevention
too.

## Decision

For classification and for M4, the feedforward hook events are
**`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`**. Every
other known event is feedback.

**The list lives in configuration, not in code** — `hfit_config.DEFAULTS`
`ff_hook_events`, overridable per install and passed into
`taxonomy.classify(..., ff_events=...)`. Two reasons, and the second matters
more:

- A user who disagrees can change the partition without editing a module, and see
  the effect on their own report.
- A judgement recorded only in prose is unfalsifiable. Because the list is a
  config value a test can override,
  `test_taxonomy.py::test_the_feedforward_event_list_is_overridable` demonstrates
  that reversing this decision reverses the classification — so the decision is
  visibly a decision rather than an accident of implementation.

`classify` reports **`basis: "adr-005"`** whenever its answer differs from the
workshop table, and `basis: "table"` when the two agree. A reader inspecting a
composition record can therefore see exactly which classifications are this
plugin's judgement and which are inherited.

## Consequences

**Any comparison against the workshop's own numbers must account for this.** A
profile printed here and a profile counted by hand from §03 will disagree on
hooks, and the disagreement is intended. `basis` is what makes it traceable
rather than a discrepancy someone has to rediscover.

**`ff_hook_events` is a comparability field, and must be stamped into anything
that reports M4.** Two reports produced under different lists are not comparable
on `feedforward_ratio`, for the same reason two runs under different models are
not comparable on tokens per outcome. It is deliberately *not* one of
`hfit_config._GOVERNING_KEYS`: the consent fingerprint (ADR-014) governs what is
written and whether inference runs, and re-prompting a user for consent because
they reclassified a hook event would dilute a gate that exists for data handling.
The obligation this creates instead is on the reports — the list travels with the
number, so a `feedforward_ratio` can never be compared across two different
partitions without that being visible.

**An unrecognised event is `unknown`, not feedback.** The partition above is
closed over the events observed on Claude Code as measured; a future release will
add one. Defaulting a new event to the feedback side would inflate the sensor
count of every harness that adopted it, and a sensor count that grows without a
sensor being written is the failure this whole plugin exists to catch. `unknown`
plus a reason costs a classification; a silent default costs the measure's
credibility.

**A component's mechanism is unaffected.** A `PreToolUse` hook is still `comp` —
deterministic, no model involved. Only the direction moves, giving `ff_comp`.
That bucket is the one the workshop's "(+ FF)" parenthetical already gestures at;
this ADR states which events are in it.
