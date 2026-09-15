# Contract: `Stop` hook event — input

## Binding

No MCP server (there is none in this plugin). Claude Code hook event, JSON on stdin.

- **Event:** `Stop`
- **Matcher:** none. A `Stop` has no tool to match on, and the work is unconditional.
- **Script:** `hooks/scripts/reconcile_hook.py` — the same script as `PostToolUse`, because
  the work is identical.
- **Timeout:** 15s (`hooks/hooks.json`) — tighter than the `PostToolUse` budget, because
  this one fires at the end of every turn rather than only after a close.
- **Entrypoint:** `main()` under `hook_io.fail_open`
- **Output:** none. No `additionalContext`, no `decision`, empty stdout, exit 0.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "hook_event_name": {
      "const": "Stop",
      "description": "Present, and deliberately not branched on — see post-tool-use.md, 'Why the branch is not on the event name'. This event is distinguished from a tool event by having no tool_input, which is the field the branch actually needs."
    },
    "stop_hook_active": {
      "type": "boolean",
      "description": "True when this Stop is a re-entry caused by a hook that blocked a previous one. Honoured — see 'Why stop_hook_active is honoured for cost and not for correctness'."
    },
    "cwd": {
      "type": "string",
      "description": "Which project's ledger and beads database to reconcile. Authoritative over $CLAUDE_PROJECT_DIR and the process cwd, for the reason given in session-start.md."
    },
    "session_id": {
      "type": "string",
      "description": "Not read. An episode belongs to an issue, not to the session that closed it — and a Stop reconciles issues closed by any session, including ones closed on another machine and arrived over Dolt sync."
    },
    "transcript_path": {
      "type": "string",
      "description": "Present and never opened by this hook. Untrusted; any consumer must refuse it unless its realpath is under ~/.claude."
    },
    "tool_input": {
      "description": "Absent on this event, and its absence is the signal. `_command` returns None, which is what selects the unconditional branch."
    }
  },
  "required": []
}
```

### Why there is a second trigger at all

`PostToolUse:Bash` catches a close as it happens, and that covers the common case. It does
not cover a close made by `bd` from another terminal, by an editor integration, or on
another machine and arrived over Dolt sync — and it misses any close whose command shape
the predicate does not recognise.

Because reconciliation is idempotent *discovery* rather than tracking, a second trigger
costs one `bd list` per turn and closes all of those gaps at once. Neither trigger is
authoritative; two exist so that a miss is unlikely rather than so that either is
sufficient.

### Why `stop_hook_active` is honoured for cost and not for correctness

The flag exists so a hook that blocks a stop cannot loop forever. This hook cannot cause
that loop: it emits nothing, and nothing it can emit would prevent a stop. Honouring the
flag is therefore purely about cost — a re-entrant `Stop` would pay for a second `bd list`
guaranteed to report every episode `unchanged`, since the first pass already appended
everything that moved.

### Why this event does not paginate its own work

A first reconcile in a project with a long closed backlog offers every closed issue at
once, and the writes that follow sit inside this 15s budget. `beads_write.write_episodes`
bounds them two ways: `MAX_EPISODES_PER_RUN` caps the healthy case, and any failure that is
a property of the *project* — no database, no `bd`, a hung one — halts the run rather than
paying `beads_read.TIMEOUT_SECONDS` again per remaining episode. The cap alone would not
be enough; twenty timeouts would exceed this budget by an order of magnitude.

Episodes beyond the cap are a known residual: a later reconcile reports them `unchanged`,
so nothing offers them again. `contracts/output/bd-metadata-write.md` records that
boundary.

## Why this hook emits nothing

A `Stop` response can block the stop and send the agent back to work. This one never does.
Blocking here would let a measurement plugin decide when a session is finished, which is a
category of authority the instrument must not hold — and the plugin's purpose is to report
what the harness costs, so a hook that lengthens turns would corrupt the number it exists
to print (ADR-006).

## Consent

Nothing is written, and **no `bd` process runs at all**, until `acknowledged.json` carries
a fingerprint matching the governing config (ADR-014). This matters more on this event than
on any other: `Stop` fires every turn, so an unacknowledged install would otherwise be
running a subprocess in the user's repository continuously without ever having been
allowed to.

## What it writes

The same two artefacts as `PostToolUse`, in the same two calls: `episodes.jsonl`
(`contracts/output/episode-record.md`) via `ledger.record_episode`, and the `hfit_*` index
keys (`contracts/output/bd-metadata-write.md`) via `beads_write.write_episodes` for the
episodes that moved.

In the steady state — the overwhelmingly common case for a per-turn hook — nothing moved,
`moved` is empty, and no `bd update` runs.

## SemVer

- **Contract version:** 1.0.0
- **Dated against:** Claude Code as observed on this machine, 2026-09.
- **Deprecation policy:** Reading `session_id` or `transcript_path` is a **minor** bump.
  Ignoring `stop_hook_active`, or emitting any response field, is a **major** bump; the
  latter also requires reversing ADR-006.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 15000` — the `hooks.json` timeout, a budget rather
  than an expectation. The steady-state cost is one `bd list` bounded by
  `beads_read.TIMEOUT_SECONDS` and no writes at all.
- **Availability:** best-effort. Every failure path exits 0 with empty stdout and empty
  stderr. A project with no beads database is a real state, not an error, and is reported
  as `no_database` rather than as an empty population.
- **Telemetry:** none. This plugin emits no telemetry of its own and makes no network call
  of any kind.
