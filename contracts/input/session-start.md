# Contract: `SessionStart` hook event — input

## Binding

No MCP server (there is none in this plugin). Claude Code hook event, JSON on stdin.

- **Event:** `SessionStart`
- **Matcher:** none — every session
- **Script:** `hooks/scripts/snapshot_composition.py`
- **Timeout:** 15s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.fail_open`
- **Output:** none. See "Why this hook emits nothing" below — it is the load-bearing
  property of the whole contract.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": {
      "type": "string",
      "description": "Read but not used by this hook. A composition is a property of the machine and the project, not of a session, and keying it by session would make changes.jsonl a session log."
    },
    "hook_event_name": { "const": "SessionStart" },
    "source": {
      "type": "string",
      "enum": ["startup", "resume", "compact", "clear"],
      "description": "Not read, and deliberately not branched on — see below."
    },
    "cwd": {
      "type": "string",
      "description": "Which project this session belongs to. Authoritative over $CLAUDE_PROJECT_DIR and over the process cwd (see below)."
    },
    "transcript_path": {
      "type": "string",
      "description": "Present in the payload and deliberately never opened by this hook. It arrives from an untrusted source; any consumer must refuse it unless its realpath is under ~/.claude."
    }
  },
  "required": []
}
```

Every field is optional in practice. A payload that is absent, empty or not JSON at
all is survived: the hook falls back to `$CLAUDE_PROJECT_DIR` and then to the
process cwd, and records what it can.

### Why `cwd` beats both alternatives

The ledger is per-project, so getting the project wrong does not lose data — it
splits one project's history across two directories, where each half silently falls
below `min_episodes_per_digest` and every comparison returns `insufficient_n` for a
reason no reader can trace back to a mis-resolved path.

The payload's `cwd` is the session's own statement of where it is. A `SessionStart`
hook's *process* cwd is not a documented guarantee, and `$CLAUDE_PROJECT_DIR` is an
environment variable that a caller which already knows which project it means must
not have its decision overridden by. Asserted by
`test_the_project_comes_from_the_payload_not_the_process`.

### Why `source` does not gate anything

All four sources are handled identically, which is a stronger invariant than
branching on them. A composition file is content-addressed, and `changes.jsonl`
gains a record **only when the digest moves**, so re-recording an unchanged harness
on a `resume` or a `compact` is already a no-op by construction. Branching would
add a second code path that could disagree with the first, in exchange for nothing.

An unrecognised or absent `source` therefore needs no special case.

## Why this hook emits nothing

A `SessionStart` response may carry `additionalContext`, which is injected into the
context window of **every session, forever**. This hook emits none.

That is a measurement property, not a stylistic one. The plugin's purpose is to
report what the harness costs per outcome; a line item it adds to that cost on every
session start would corrupt the number it exists to print (ADR-006). Everything the
hook learns goes to a file, read on demand through `/hfit:composition`.

`tests/integration/test_snapshot_composition.py` asserts empty stdout on every path,
including the failure paths, because "emits nothing" is only enforceable by
assertion — there is no schema that can require silence.

## Consent

Nothing is written until `acknowledged.json` carries a fingerprint matching the
governing config (ADR-014). The check runs **before any path is resolved as a
directory**, so an unacknowledged install leaves no trace on the filesystem at all —
observable by looking at the disk rather than at a return value.

A changed governing key invalidates the acknowledgement and writing stops again.

## What it writes

`contracts/output/composition-record.md` and the `changes.jsonl` entry described
there. Two properties of that write are visible from here:

- The stored `env_hash` is **first-sighting provenance only**. Composition files are
  never rewritten, so a later session under a different model produces the same
  digest, appends nothing, and leaves the original pin in place. An episode must
  resolve its own `env_hash` at close time and must never read one off a composition
  record (ADR-007).
- **Both halves of the pin are written, never just the hash.** `model_basis` travels
  beside `env_hash` in the composition record and in the `changes.jsonl` entry,
  because the comparison layer is obliged to refuse on a differing basis and cannot
  do so from a field that was dropped at write time. Nothing on this path reads an
  OTEL log, so the basis this hook records is `declared` at best and `unknown` where
  no model resolves.
- A blocked write degrades to *nothing recorded*, not to a swallowed traceback. Every
  dependency in the hook's body is total — `state_store` returns `False` and
  `hfit_time` falls back rather than raising — so `fail_open` is the last line of
  defence here and not the mechanism. The next run reads the absent baseline and
  re-attempts.

## SemVer

- **Contract version:** 1.0.0
- **Dated against:** Claude Code as observed on this machine, 2026-09.
- **Deprecation policy:** Reading a field this contract currently declares unread
  (`session_id`, `source`, `transcript_path`) is a **minor** bump. Emitting any
  `additionalContext` would be a **major** bump and requires reversing ADR-006 first.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 15000` — the `hooks.json` timeout, which is a
  budget rather than an expectation. Measured path: eight disk reads, no subprocess,
  no `bd`, no OTEL, no network. Asserted under 5s end-to-end as a subprocess by
  `test_it_finishes_far_inside_its_timeout`; the real figure is an order of magnitude
  below that.
- **Availability:** best-effort, and every failure path exits 0 with empty stdout and
  empty stderr. An absent settings tree is a real state (a fresh machine), not an
  error, and records the empty harness.
- **Telemetry:** none. This plugin emits no telemetry of its own and makes no network
  call of any kind.
