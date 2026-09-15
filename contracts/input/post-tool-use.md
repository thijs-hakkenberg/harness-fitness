# Contract: `PostToolUse` hook event — input

## Binding

No MCP server (there is none in this plugin). Claude Code hook event, JSON on stdin.

- **Event:** `PostToolUse`
- **Matcher:** `Bash` — and the matcher is the whole reason this event is affordable.
  Without it the hook would be woken by every `Read`, `Edit` and `Grep` in the session.
- **Script:** `hooks/scripts/reconcile_hook.py`
- **Timeout:** 20s (`hooks/hooks.json`) — the only hook here that may run a subprocess
  on a hot path, budgeted at `beads_read.TIMEOUT_SECONDS` plus room for the writes.
- **Entrypoint:** `main()` under `hook_io.fail_open`
- **Output:** none. No `additionalContext`, no permission decision, empty stdout, exit 0.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "hook_event_name": {
      "const": "PostToolUse",
      "description": "Present, and deliberately not branched on. The script decides what to do from the presence of tool_input.command instead — see 'Why the branch is not on the event name'."
    },
    "tool_name": {
      "type": "string",
      "description": "Expected to be Bash, guaranteed by the matcher, and not re-checked. A redundant gate here would cost a code path that the matcher already makes unreachable in production, and the failure it would prevent is a redundant idempotent reconcile."
    },
    "tool_input": {
      "type": "object",
      "properties": {
        "command": {
          "type": "string",
          "description": "The shell command that just ran. Tokenised with shlex and tested for a `bd close`; never regex-matched. Not stored anywhere, in any form."
        }
      },
      "description": "The one field this hook reads. An absent or non-dict tool_input means 'not a tool event'; a present one with an unusable command means 'a tool event that did not close anything'. The two are different answers."
    },
    "tool_response": {
      "description": "Never read. Whether the command succeeded is irrelevant: a failed `bd close` leaves no closed issue, so the reconcile finds nothing and appends nothing. Branching on it would add a way to be wrong in exchange for nothing."
    },
    "cwd": {
      "type": "string",
      "description": "Which project's ledger and beads database to reconcile. Authoritative over $CLAUDE_PROJECT_DIR and the process cwd, for the reason given in session-start.md."
    },
    "session_id": {
      "type": "string",
      "description": "Not read. An episode is a property of an issue, not of the session that happened to close it."
    },
    "transcript_path": {
      "type": "string",
      "description": "Present and never opened by this hook. Untrusted; any consumer must refuse it unless its realpath is under ~/.claude."
    }
  },
  "required": []
}
```

### Why the trigger is a tokenised predicate and not a substring

`PostToolUse:Bash` fires for every shell call a session makes. The literal text
`bd close` appears in an echo, in a commit message, in a heredoc and in a comment, and
each of those would otherwise put a `bd list` subprocess behind a command that changed
nothing about the backlog.

`reconcile.is_close_command` therefore tokenises with `shlex` and looks for the token
pair. Tokenising also buys the two shapes a real close actually arrives in — flags after
the subcommand, and a close chained behind the tests that justified it — without a second
pattern. An unbalanced quote returns `False` rather than raising: a hand-typed command
with one is not a reason for a hook to fail.

### Why the branch is not on the event name

One script serves this event and `Stop`, and what separates them is whether there is a
command to inspect. That question is asked of `tool_input.command` rather than of
`hook_event_name`, because the two ways of misreading the event name fail differently:

- Read as "reconcile", an absent or renamed event name puts a `bd list` behind **every**
  shell call — precisely the cost the predicate above exists to avoid.
- Read as "do nothing", the plugin silently stops recording episodes, with no artefact
  anywhere saying so.

Keying on the field the branch actually needs avoids both, and survives Claude Code
renaming an event.

### Why a false positive is cheap and a false negative is cheaper

Reconciliation is idempotent discovery, not tracking. It reads the whole closed
population, and the ledger appends only where a record moved. So a spurious trigger costs
one `bd list` and appends nothing, and a missed trigger costs nothing at all — the `Stop`
hook, or the next real close, picks it up. Neither error can produce a wrong number,
which is what allows the predicate to be conservative.

## Why this hook emits nothing

A `PostToolUse` response can inject context and can influence what happens next. This one
does neither. The plugin's purpose is to report what the harness costs per outcome, and a
hook that adds to that cost on every Bash call would corrupt the number it exists to
print (ADR-006). It also claims no ability to affect a tool call: that hot path belongs to
abacus and to no second blocking hook (ADR-004).

## Consent

Nothing is written, and **no `bd` process runs at all**, until `acknowledged.json` carries
a fingerprint matching the governing config (ADR-014). Both `reconcile` and
`beads_write.write_episodes` check the gate before their first subprocess, so an
unacknowledged install does not execute a process in the user's repository — a stronger
property than not writing a file, and the one that matters when the repository is not the
user's own.

## What it writes

Two artefacts, in two calls:

- `episodes.jsonl` (`contracts/output/episode-record.md`) — the store of record, appended
  by `ledger.record_episode` only where a record moved.
- The `hfit_*` index keys (`contracts/output/bd-metadata-write.md`) — written by
  `beads_write.write_episodes` for the episodes that moved, and for those only. In the
  steady state that list is empty and no `bd update` runs.

The read and the write are separate calls because `reconcile` is read-only, which is what
makes it safe to hang off a `Stop` on every turn. It reports what moved so the index can
follow without re-reading and diffing a ledger that was just written.

## SemVer

- **Contract version:** 1.0.0
- **Dated against:** Claude Code as observed on this machine, 2026-09.
- **Deprecation policy:** Reading `tool_response`, `session_id` or `transcript_path` is a
  **minor** bump. Widening the matcher beyond `Bash`, or emitting any response field, is a
  **major** bump; the latter also requires reversing ADR-006.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 20000` — the `hooks.json` timeout, a budget rather
  than an expectation. The overwhelmingly common path is a `shlex.split` and an exit: no
  subprocess, no file opened. On a real close the cost is one `bd list` bounded by
  `beads_read.TIMEOUT_SECONDS` plus at most `beads_write.MAX_EPISODES_PER_RUN` writes,
  each of which halts the run on an environmental failure rather than paying that timeout
  again.
- **Availability:** best-effort. Every failure path exits 0 with empty stdout and empty
  stderr. A project with no beads database is a real state, not an error, and is reported
  as `no_database` rather than as an empty population.
- **Telemetry:** none. This plugin emits no telemetry of its own and makes no network call
  of any kind.
