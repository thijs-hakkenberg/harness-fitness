# ADR-004 — There is no `PreToolUse` hook, and no `SubagentStop`, `Notification` or `PermissionRequest` hook

- **Status:** accepted
- **Date:** 2026-09-14
- **Governs:** `hooks/hooks.json`, and every future proposal to add an event to it
- **Related:** [ADR-006](006-the-hot-path-is-computational-only.md) hot-path-is-computational-only, [ADR-005](005-a-pretooluse-block-is-a-feedforward-catch.md) a-pretooluse-block-is-a-feedforward-catch

## Context

Six of Claude Code's hook events are claimed by this plugin. Four are available
and deliberately refused. `PreToolUse` is the one that needs the argument, because
it is the obvious place to put a measurement plugin and the wrong one.

**`PreToolUse` is the only event whose response can stop a tool call.** That makes
it the most powerful hook available and the most expensive: it runs before *every*
matching tool use, so its latency is paid on every edit, every bash command, every
read, for the entire life of the session. A hook that takes 80 ms and fires two
hundred times in a session has spent sixteen seconds of the user's wall clock.

Three facts make paying that price pointless here:

- **abacus already owns this hot path.** Its edit gate is a `PreToolUse` hook that
  refuses an `Edit` when no beads task is claimed. Two blocking hooks on the same
  event means two ways for the session to stall and an ordering question — which
  refusal does the user see first? — that neither plugin can answer.
- **Every block is already reported.** The measurement this plugin wants from the
  hot path is *which controls fired and what they stopped*. `tool_decision` and
  `hook_execution_complete` are emitted to the OTEL log for every block, by every
  hook, whoever owns it. Observing a `PreToolUse` hook does not require being one.
- **A blocking hook is a new way to break a session.** `hook_io.fail_open` makes a
  crash harmless for the events this plugin does claim, because a `SessionStart`
  hook that exits 0 saying nothing costs a measurement and nothing else. On
  `PreToolUse` the same failure has a second, worse mode: a hook that hangs holds
  a tool call for its full timeout, and the user experiences a measurement tool as
  a slow editor.

`SubagentStop`, `Notification` and `PermissionRequest` are refused for a
narrower reason. Each would add a genuine signal — subagent boundaries, permission
prompts — and none of them is worth the cost stated in
[ADR-006](006-the-hot-path-is-computational-only.md): every hook this plugin
registers enlarges the composition it exists to measure. The same three signals
reach the event log without a hook. `PermissionRequest` is the tempting one, since
[ADR-005](005-a-pretooluse-block-is-a-feedforward-catch.md) classifies a denial
there as a feedforward catch — but M4 counts those catches from
`hook_execution_complete`, not by being the hook that made them.

## Decision

`hooks/hooks.json` claims **`SessionStart`, `UserPromptSubmit`, `PostToolUse`,
`PreCompact`, `Stop`, `SessionEnd`** and nothing else. `PreToolUse`,
`SubagentStop`, `Notification` and `PermissionRequest` are refused.

**No hook in this plugin can influence whether a tool runs.** The `PostToolUse`
entry is a `Bash` matcher that tokenises a command looking for `bd close`; it
returns no decision and never could, because it runs after the fact.

**The refusal is asserted, not documented.**
`tests/unit/test_hooks_manifest.py::TestTheRefusals` parametrises over a `REFUSED`
tuple and fails if any of the four appears in the manifest, and
`test_no_event_outside_the_claimed_set_is_wired` fails on a fifth event nobody
budgeted. A refusal that lives only in prose is reversed by the first person who
finds a blocking hook convenient; one that fails a test is reversed by someone who
has read this file and decided to.

## Consequences

**Per-tool-call prevention is out of scope, permanently.** This plugin cannot warn
a user before a bad edit, and there is no version of it that can without
reopening this decision. It is an instrument, not a control. Anyone wanting a
preventive control should write it as a separate plugin — where it will appear in
this plugin's own composition profile as an `ff_comp` component, which is exactly
the right outcome.

**Adding a fifth event is a decision with a cost to state.** Not a forbidden one:
`SessionEnd` earns its 45-second budget by doing the OTEL distillation that keeps
every other hook cheap. But a proposal to claim a new event has to say what the
signal is worth against the latency it adds *and* against the line it adds to
every composition profile this plugin prints — and it has to edit `BUDGETS` in
`test_hooks_manifest.py`, so it cannot happen quietly.

**Two blocking-hook signals are read second-hand, and the join is imperfect.**
Because this plugin never sees a block directly, M4 attributes catches from
`hook_execution_complete`, where `hook_source` is always `merged` and `decision`
always `null` — so a block cannot be traced to the plugin that made it (risk W3).
The aggregate feedforward and feedback counts are unaffected; per-sensor credit is
withheld with `attribution: "ambiguous"`. That is a real loss, accepted here as
the price of not being on the hot path.
