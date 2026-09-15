# Contract: outcome declaration — output

## Binding

Stdout of one command. No MCP server, no network.

```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/outcome.py" \
    --issue <ISSUE_ID> --verdict <accepted|rejected|abandoned|superseded> [--json]
```

`--issue` and `--verdict` are **required**; `--json` is **optional**, and that is the one
place this contract diverges from `fitness-report.md`, deliberately. `fitness.py` makes
`--json` mandatory so there is exactly one output format to keep correct. Here there are
two consumers with genuinely different needs: a skill parses stdout, and a person types
this by hand at a terminal to record a verdict on work they just finished. Prose is the
default because the interactive case is the common one and a wall of JSON is a worse
answer to "did that land?" than a sentence. **The prose form is not specified here and
is not a contract** — it is free to change. Only `--json` is.

`--verdict` takes exactly the four members of `verdict.DECLARABLE`. `partial` and
`unstated` are refused by `argparse` before anything runs, and the refusal names all
four. Both are legitimate values of a *stored* record and neither is declarable:
`partial` is derived from abacus's `abacus_partial` flag, so a hand-set one would claim
an interrupted session that abacus recorded as clean — the one direction of that flag
nothing can check; and `unstated` says nothing a missing key does not already say.

The project is `$CLAUDE_PROJECT_DIR`, falling back to the process cwd, and it is echoed
in `project`. That echo matters more here than on the report: `bd` resolves *which
database* it reads and writes from its working directory, so a wrong answer would
declare a verdict on another project's issue — a wrong answer rather than an error.

**This command writes**, which is what separates it from every other read surface. Two
side effects, both bounded and both local: metadata on exactly one beads issue, and at
most one append to that project's episode ledger. Nothing is written before consent
(ADR-014), and nothing leaves the machine.

**It does not read stdin.** Under a skill, stdin is a pipe that is never closed, so a
surface that reads it hangs until the timeout and looks like a broken session.

## The verdict must not come from the model

The load-bearing property of this surface is a negative one, and no schema can carry it.
`declared` is the top rung of the ladder (ADR-013) precisely because it means a person
said so; every rung below it is derived. A skill that picks `accepted` because the work
looked finished turns the most trustworthy basis this system can print into an inference
wearing a declaration's badge, and nothing downstream can tell the two apart — an
`unstated` episode is honestly excluded from every measure, while an invented `accepted`
silently corrupts all of them.

`argparse` can enforce the vocabulary. It cannot enforce that a human chose the value, so
that obligation lives in `skills/harness-outcome/SKILL.md` as prose and is stated three
times there. A consumer of this contract inherits it.

## A successful declaration

Exactly these ten keys, in this order. `ok` is first, deliberately.

```json
{
  "ok": true,
  "schema": 1,
  "plugin_version": "0.2.0",
  "project": "/Users/you/projects/repos/Something",
  "issue_id": "Something-4k2",
  "verdict": "accepted",
  "basis": "declared",
  "keys_written": 3,
  "recorded": true,
  "reconcile_reason": null
}
```

| key | at 0.2.0 |
|---|---|
| `ok` | `true` once the declaration reached beads. **Read this first.** |
| `schema` | result schema, `1`. |
| `plugin_version` | from the plugin manifest, or the string `"unknown"` — never a fabricated `0.0.0`. |
| `project` | the directory whose beads database and ledger were used. |
| `issue_id` | echoed back verbatim, on both paths. |
| `verdict` | echoed back, one of the four declarable values. |
| `basis` | always `"declared"`. Present so a pasted result is self-describing rather than needing the command line beside it. |
| `keys_written` | how many `hfit_*` metadata keys were set — `3` at this version (`hfit_verdict`, `hfit_verdict_basis`, `hfit_schema`). |
| `recorded` | whether the **episode ledger** has read the declaration back yet. A separate fact; see below. |
| `reconcile_reason` | `null` when the read-back succeeded, otherwise the machine reason it did not. |

`hfit_verdict_basis` is strictly redundant on this machine — `verdict.classify` reaches
the `declared` rung on `hfit_verdict` alone — and is written anyway, because the record
is Dolt-synced and the next reader has only the keys to go on. `hfit_schema` travels
beside two real keys, which is the condition `bd-metadata-write.md` states for stamping
it at all.

### The write and the ledger are separate facts

This is the one place in the system where the two halves can come apart, so they are
reported as two keys rather than folded into one:

| `ok` | `recorded` | means |
|---|---|---|
| `true` | `true` | the declaration is on the issue **and** the ledger holds it. The complete case. |
| `true` | `false` | the declaration is on the issue; the read-back did not happen. `reconcile_reason` says why. **Not a failure.** |
| `false` | *absent* | nothing was written. There was nothing to read back. |

`recorded: false` beside `ok: true` is an honest partial success and a consumer **must
not** report it as an error or send the user to declare it again. The declaration is
stored where it will be found; the next `Stop` hook reconciles it. Sending someone to
redo work that is already done is worse than saying nothing.

`recorded` is answered from the **basis**, not from the verdict value and not from
whether the ledger moved. Not the value, because `verdict.classify` downgrades a declared
`accepted` to `partial` when `abacus_partial` is true while keeping the basis `declared`
— the declaration *was* read back, and a separate documented fact overrode what it said.
Not from movement, because the ledger is append-only *on movement*: re-declaring the same
verdict appends nothing, so a "did it move" test would report `false` over a ledger that
holds the record. And the basis test is gated on the reconcile having succeeded at all,
because a *prior* declaration already leaves `verdict_basis: "declared"` behind — without
that gate a second write landing while `bd list` fails would claim a read that never
happened.

## A refused declaration

**Eight keys. No `basis`, no `keys_written`, no `recorded`, no `reconcile_reason`.**

```json
{
  "ok": false,
  "schema": 1,
  "plugin_version": "0.2.0",
  "project": "/Users/you/projects/repos/Something",
  "issue_id": "Something-4k2",
  "verdict": "accepted",
  "reason": "not_acknowledged",
  "note": "Nothing was written: this install has not been acknowledged. Run …"
}
```

`reason` is the machine-readable form; `note` is one sentence written for a person, and it
names the remedy. `issue_id` and `verdict` stay on this path on purpose: a refusal a
person reads has to say what it declined to do, and a skill retrying has to know what to
retry.

`fitness.py` carries a `gaps[]` list because a report can be short of several things at
once. A single-purpose command has exactly one thing to say, so `note` is a single
string — and it is never empty. An unrecognised reason from a lower layer gets a sentence
naming the reason itself, because a consumer printing a blank note would invent an
explanation, which is the failure this whole surface exists to prevent.

### Absence is the encoding

Nothing weaker would do, and the reasoning is the same as `fitness-report.md`'s pointed at
different keys:

- `keys_written: 0` would claim the write ran and wrote nothing. Nothing ran.
- `recorded: false` would claim the ledger was read and found wanting. It was not read.
- `reconcile_reason: null` would claim a read-back that succeeded.

Every one of those values is legitimate on some **successful** path — `recorded: false`
and a non-null `reconcile_reason` are the documented partial success above — so reusing
any of them for a refusal would make the two indistinguishable. **A consumer must branch
on `ok` before touching any other key**, and must treat a missing key as *not attempted*
rather than as *zero*.

## Refusal reasons at 0.2.0

| `reason` | source | means |
|---|---|---|
| `not_acknowledged` | `consent` | consent has never been given. The note carries the `acknowledge.py --accept` command. This is the whole first-run experience. |
| `config_changed` | `consent` | a governing config key moved since acknowledgement, so writing stopped on purpose. Re-acknowledge. Not a fault. |
| `writes_disabled` | this surface | `beads.write_metadata` is false for this project. The cross-machine index is off by choice; the ledger is still the store of record. |
| `no_database` | `beads_read.REASONS` | there is no beads database here, so there is no issue to write to. |
| `bd_unavailable` | `beads_read.REASONS` | `bd` is not on the PATH. |
| `bd_error` | `beads_read.REASONS` | `bd` refused the update — most often an issue id that does not exist. |
| `timeout` | `beads_read.REASONS` | `bd` did not answer within `beads_read.TIMEOUT_SECONDS`. Nothing was half-applied: the keys travel in one invocation, so retrying is safe. |
| `nothing_to_write` | `beads_write.REFUSALS` | not reachable from this command, which always supplies three keys. Carries a note saying to report it. |

`writes_disabled` is checked **here** rather than inherited, because
`beads_write.set_metadata` does not honour `beads.write_metadata` — only `write_episode`
and `write_episodes` do. A surface built naively on `set_metadata` alone would write with
the index switched off, and the toggle has to mean no `bd` process at all rather than a
suppressed argument.

Consent is checked before that, and before `bd` starts. It is the **outer** gate, not a
condition on storing the result (ADR-014).

Reasons are additive across versions. **A consumer must tolerate an unrecognised one** —
printing its `note` is always correct — rather than switching exhaustively.

## Exit codes and stderr

| | |
|---|---|
| `0` | a result was printed — including every `ok: false` case above. A refused write is a successful *report*. |
| non-zero | the arguments were not understood: a missing `--issue`, or a `--verdict` outside the four. `argparse` names the offending value and all four it would have taken; stdout stays empty. |

A skill that branched on the exit code would read "you have not consented yet" as a broken
plugin, which is why the refusals are all `0`. The argparse path is the deliberate
exception: a mistyped verdict must not be silently absorbed, because the alternative is
recording a verdict nobody chose.

**Stdout is one JSON object and nothing else** under `--json` — no log line, no banner,
no partial traceback.

## Consumers

- `skills/harness-outcome/SKILL.md` (`/hfit:outcome`) — the only programmatic consumer.
  It must read `ok` first, print `note` verbatim on a refusal, and report
  `recorded: false` as "stored, ledger catches up shortly" rather than as an error.
- A human at a terminal, who gets the prose form. This is why `--json` is optional here
  and mandatory on `fitness.py`.
- Indirectly, `contracts/output/episode-record.md` and
  `contracts/output/bd-metadata-write.md`: this surface is one of the two producers of a
  `declared` verdict basis, and the only one a person drives directly.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** adding a key, or a new refusal `reason`, is a **minor** bump —
  consumers tolerate unknown reasons by contract. Removing a key, retyping one, or
  changing what `ok` gates is **major** and requires a `schema` increment. Moving a key
  from the successful path onto the refused path is **major**, because the absence *is*
  the encoding and a consumer reads it as "not attempted".
- Widening `--verdict` beyond `verdict.DECLARABLE` would be **major** even though it
  accepts more input, because it changes what a `declared` basis means downstream.
  Narrowing it is major for the ordinary reason.
- `keys_written` is expected to grow past `3` as more `hfit_*` keys become declarable.
  That is a value change, not a contract change, and a consumer must not assert on the
  number.

## SLA + telemetry

- **Latency:** interactive, and dominated by two `bd` invocations — one `bd update` for
  the write, then one `bd list` for the read-back — each bounded by
  `beads_read.TIMEOUT_SECONDS`. No OTEL scan, no whole-repo walk.
- **Availability:** total. Every documented failure exits 0 with a result, and a failed
  read-back degrades to `ok: true` with `recorded: false` rather than to a refusal.
- **Side effects:** exactly two, both local — `hfit_*` metadata on one beads issue, and
  at most one append to `episodes.jsonl` for that project. No `abacus_*` key is ever
  written; `beads_write` raises rather than refusing on that prefix. No issue text is
  read into any record.
- **Telemetry:** none. No upload, no endpoint, no network call of any kind.
