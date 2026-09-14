# Contract: fitness report — output

## Binding

Stdout of one command. No MCP server, no network, no file written.

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/fitness.py" --json
```

`--json` is **required** at 0.1.0. A default human format would be a second output
contract to keep in step with this one, and every consumer at this version is a skill
that calls `json.loads` on stdout.

The project reported on is `$CLAUDE_PROJECT_DIR`, falling back to the process cwd. It
is echoed back in `project`, because the single most likely first-run confusion is a
report about a different directory than the reader thinks they are in.

**This command reads. It never writes** — not a state directory, not a session file,
not even before consent. A reporting command that creates state has changed the thing
it reports on, and before acknowledgement it would be creating that state in defiance
of ADR-014. Asserted by comparing the whole state tree across a call, and separately
by asserting the tree does not exist at all on an unacknowledged run.

**It does not read stdin.** Under a skill, stdin is a pipe that is never closed, so a
surface that reads it hangs until the hook timeout and looks to the user like a broken
session.

## A successful read

Exactly these ten keys, in this order. `ok` is first, deliberately.

```json
{
  "ok": true,
  "schema": 1,
  "plugin_version": "0.1.0",
  "project": "/Users/you/projects/repos/Something",
  "current": {
    "digest": "b79079db273c",
    "profile": {
      "ff_inf": 1, "ff_comp": 0, "fb_comp": 1, "fb_inf": 0,
      "both_inf": 0, "substrate": 0, "packaging": 3, "unknown": 0
    },
    "env_hash": "085874cc…357ba6",
    "model_basis": "declared",
    "captured_at": "2026-09-14T17:09:34Z"
  },
  "changes": [
    {"ts": "2026-09-14T17:09:34Z", "schema": 1, "from": null, "to": "2d53cbf2de0b",
     "env_hash": "0858…7ba6", "model_basis": "declared", "kind": "baseline",
     "diff_basis": "baseline", "added": [], "removed": [], "changed": [],
     "profile_delta": {}}
  ],
  "episodes_seen": null,
  "measures": null,
  "gaps": [
    {"kind": "measures-unavailable",
     "reason": "The four measures are not implemented at this version. …"}
  ],
  "flags": []
}
```

| key | at 0.1.0 |
|---|---|
| `ok` | `true` on a successful read. **Read this first.** |
| `schema` | report schema, `1`. |
| `plugin_version` | from the plugin manifest, or the string `"unknown"` — never a fabricated `0.0.0`. |
| `project` | the directory whose ledger was read. |
| `current` | the composition in force, or `null` with a gap. |
| `changes` | the change log verbatim, oldest first. See `composition-record.md`. |
| `episodes_seen` | **`null`.** No episode store exists until 0.2.0. |
| `measures` | **`null`.** The measure layer arrives in 0.3.0. |
| `gaps[]` | `{kind, reason}` — a kind for a skill to branch on, a sentence for a person to act on. |
| `flags[]` | conditions affecting the whole read. Empty here. |

`plugin_version` is not decoration. A report gets pasted into an issue and read weeks
later, and without it the reader cannot tell whether a `null` measure means "not
implemented at that version" or "unavailable on that machine" — and at 0.1.0 almost
every measure is the former.

### Why `null` and not `0` or `{}`

- `episodes_seen: 0` would assert that we looked at the user's closed issues and
  found none. That is a claim about their work. There is no episode store to look in.
- `measures: {}` would say the measure layer ran and produced nothing.
- `profile: {}` on an unreadable record would describe a harness with no components.

Unknown is never zero, and every `null` is paired with a `gaps[]` entry that says why.
A `kind` with an empty `reason` is a contract violation — it forces the skill to
invent an explanation, which is the failure this whole surface exists to prevent.

### Both halves of the pin

`current` carries `model_basis` beside `env_hash`, never the hash alone. ADR-007
obliges the comparison layer to refuse across a differing basis exactly as it refuses
across a differing hash; a report showing only the hash invites the reader to make by
eye the comparison the tool would refuse. `model_basis ∈ observed | declared |
unknown | null` — and a `declared` model is never presented as the model that ran.

The two hashes have different widths on purpose and must not be "fixed" to match:
`env_hash` is 64 hex (pinned by the rig's `result.schema.json`), `digest` is 12
(the `compositions/<digest12>.json` filename contract).

## A failed read

**Six keys. No `current`, no `changes`, no `episodes_seen`, no `measures`, no
`flags`.**

```json
{
  "ok": false,
  "schema": 1,
  "plugin_version": "0.1.0",
  "project": "/Users/you/projects/repos/Something",
  "reason": "not_acknowledged",
  "gaps": [{"kind": "not-acknowledged", "reason": "Nothing has been recorded yet: …"}]
}
```

`reason` is the machine-readable form (`not_acknowledged`, `config_changed`); the
gap's `reason` is the sentence a person reads, and it names the command to run.

### Absence is the encoding

This is the one shape a caller cannot misread, and it is worth stating why nothing
weaker would do. `null` is already spoken for: at 0.1.0 it is the *legitimate* value
of `measures` on a **successful** read. A failed read that also said `measures: null`
would be indistinguishable from a successful one, and a skill would report "no
measures available" where the honest answer is "consent was never given".

`changes` belongs in the same list for the same reason: an empty change log is a real
and legitimate state of a successful read, so emitting `[]` on a failure would assert
"this project's harness has never moved" when we never looked.

**So a consumer must branch on `ok` before touching any other key**, and must treat a
missing key as "not read" rather than as "empty".

## Gap kinds at 0.1.0

| kind | `ok` | means |
|---|---|---|
| `not-acknowledged` | `false` | consent has never been given. The reason names `acknowledge.py --accept`. |
| `config-changed` | `false` | a governing config key moved since acknowledgement; recording has stopped until re-acknowledged. |
| `no-composition-recorded` | `true` | acknowledged, but no session has started in this project yet. Not a failure. |
| `unresolved-composition` | `true` | the digest is known from the change log but its record file could not be read. `digest` is kept; everything from the record is `null`. |
| `measures-unavailable` | `true` | always present at this version. |

`not-acknowledged` is the highest-value message in the plugin. Without it the
first-run experience is `/hfit:composition` printing nothing, which reads as a broken
plugin rather than as a decision the user has not yet made.

Kinds are additive across versions: `unstated-verdict`, `no-declared-failure-modes`,
`otel-unavailable`, `env-hash-mixed` and `composition-unstable` arrive with the
measures they qualify. **A consumer must tolerate an unrecognised kind** — printing
its `reason` is always correct — rather than switching exhaustively.

## Exit codes and stderr

| | |
|---|---|
| `0` | a report was printed — including every `ok: false` case above. A refused read is a successful *report*. |
| non-zero | the arguments were not understood. `argparse` names the offending flag on stderr; stdout stays empty. |

Being noisy here is deliberate and is the opposite of the hook discipline. A hook must
be silent because its stdout is a protocol; this is a human at a terminal, and
silently ignoring `--sinse` would print a full report the reader takes as an answer to
the question they thought they asked.

**Stdout is one JSON object and nothing else** — no log line, no banner, no partial
traceback. A skill parses it.

## Consumers

- `skills/harness-composition/SKILL.md` (`/hfit:composition`) — and from later
  versions `/hfit:fitness` and `/hfit:compare`. Each must read `ok` first and report
  `unavailable` rather than inventing a number; `/hfit:compare` must print the
  `env_hash differs` refusal and `insufficient_n` verbatim rather than paraphrasing
  them away.
- A human pasting the output into an issue. This is why `plugin_version` and `project`
  are in the envelope rather than inferred from context.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** adding a key, or a new `gaps[]` kind, is a **minor** bump —
  consumers tolerate unknown kinds by contract. Removing a key, retyping one, or
  changing what `ok` gates is **major** and requires a `schema` increment. Turning a
  documented `null` into a number is **minor** (it is the promised direction of
  travel); turning a number into `null` is **major**.
- Populating `measures` in 0.3.0 is a minor bump under this policy. The `null`s here
  are placeholders whose shape is already fixed: a measure will be an object with its
  own value, basis and refusal sentinel, never a bare number.

## SLA + telemetry

- **Latency:** interactive. Two file reads and a JSONL parse; no subprocess, no `bd`,
  no OTEL scan on this path at 0.1.0.
- **Availability:** total. Every documented failure exits 0 with a report; an
  unreadable ledger degrades to `ok: true` with `no-composition-recorded`.
- **Side effects:** none. Asserted, not assumed.
- **Telemetry:** none. No upload, no endpoint, no network call of any kind.
