# Contract: episode record — output

## Binding

One file on disk, written only by `hooks/lib/ledger.py::record_episode`, whose only
caller is `hooks/lib/reconcile.py::reconcile`, itself invoked from
`hooks/scripts/reconcile_hook.py` (`PostToolUse:Bash` and `Stop`). No MCP server, no
network, no stdout.

```
<ledger root>/episodes.jsonl   # append-only, one JSON object per line, oldest first
```

The ledger root is `~/.claude/harness-fitness/projects/<project_slug>/` by default, or
`$CLAUDE_PROJECT_DIR/.harness-fitness/` when `ledger.in_repo` is true. `$HFIT_STATE_DIR`
overrides the user-level root. UTF-8, mode 0600 inside a 0700 directory, appended
atomically so a reader never sees a half-written line.

**Nothing here is written until `acknowledged.json` carries a fingerprint matching the
governing config (ADR-014).** `record_episode` checks consent before any path is
resolved as a directory, so an unacknowledged install leaves no trace — and `reconcile`
checks it again before running `bd`, so an unacknowledged install runs no subprocess
either.

## The unit

**An episode is one closed beads issue**, and the boundary is `closed_at` rather than
`status`. Nothing else in the system has a defensible boundary: `closed_at` is a hard
timestamp, `started_at` bounds the measurement window, and one `bd list --all --json`
yields the whole population. An issue with a `status` of closed and no `closed_at` is
refused (`no_closed_at`) rather than stored with an undefined window.

## The record

```json
{
  "issue_id": "Proj-abc",
  "closed_at": "2026-09-14T17:09:34Z",
  "started_at": "2026-09-14T15:02:11Z",
  "issue_type": "feature",
  "composition_digest": "b79079db273c",
  "env_hash": "085874cc33509de46dc870b7a5b4ba238cbad96a6e8c192e21631ce168357ba6",
  "model_basis": "observed",
  "verdict": "accepted",
  "verdict_basis": "structured",
  "verdict_reason": null,
  "lexicon_version": null,
  "capture_ok": true,
  "partial": false,
  "tokens_total": 148213,
  "tool_calls": 96,
  "schema": 1,
  "ts": "2026-09-14T17:09:40Z"
}
```

Seventeen fields. Fifteen are built by `reconcile._episode`; `schema` and `ts` are
stamped by `ledger.record_episode` at the moment of the write.

| field | meaning |
|---|---|
| `issue_id` | the beads id. The deduplication key, and the join back to the issue tracker. A record without one is refused (`no_issue_id`), never stored under a placeholder. |
| `closed_at` | ISO-8601 Z. The episode boundary, and the sort key for `latest_episodes`. Required. |
| `started_at` | ISO-8601 Z or `null`. The start of the window every OTEL-derived measure is computed over. |
| `issue_type` | `bd`'s own type, carried so the `issue_type_mix_shifted` confounder is detectable — a run of chores compared against a run of features is not a delta. |
| `composition_digest` | 12 hex, naming a `compositions/<digest12>.json`. The primitive every measure groups by. |
| `env_hash` | 64 hex, **resolved at close time** — see below. |
| `model_basis` | `observed` \| `declared` \| `unknown` \| `null`. How the model in that pin was resolved. |
| `verdict` | one of `verdict.VERDICTS`: `accepted`, `rejected`, `abandoned`, `superseded`, `partial`, `unstated`. |
| `verdict_basis` | one of `verdict.BASES`, in precedence order: `declared`, `structured`, `lexical`, `inferential`, `unstated`. |
| `verdict_reason` | for an `unstated` verdict, which kind of silence it was: `no_close_reason`, `bd_default`, `no_signal`, `ambiguous`. `null` otherwise. |
| `lexicon_version` | the stamped classifier version, currently `lexical-v1`, and **only** when `verdict_basis == "lexical"`. `null` on every other rung, because no other rung ran the lexicon. |
| `capture_ok` | whether an abacus capture exists for this episode at all (`abacus_schema` present). |
| `partial` | `abacus_partial is True`. Downgrades an `accepted` to `partial`; never launders a rejection. |
| `tokens_total` | `abacus_tokens_total`, or `null`. Never a substitute figure. |
| `tool_calls` | `abacus_tool_calls`, or `null`. |
| `schema` | record schema, currently `1`. Stamped by the ledger. |
| `ts` | when this **reading** was taken — not when anything happened. Stamped by the ledger; `$HFIT_NOW` freezes it. |

### The vocabularies are closed, and other layers bind to them

`verdict.VERDICTS`, `verdict.BASES` and `verdict.UNSTATED_REASONS` are declared tuples,
and a skill's instructions cover exactly their members. An undeclared value would reach
a report with nothing to say about it, so `verdict.classify` can only emit a member and
`beads_write._pairs` validates against the same tuples before writing outward.

Two members are deliberately absent from what a *human* may declare in `hfit_verdict`
(`verdict.DECLARABLE`), and both absences are load-bearing. `partial` is a statement
about the capture, derived from `abacus_partial` — declarable, it would let a hand-set
key claim an interrupted session that abacus recorded as clean, the one direction of
that flag nothing can check. `unstated` says nothing a missing key does not.

`inferential` ranks *below* `lexical` (ADR-013): a stored inferential verdict is a
backfill, not an upgrade, and it can never be written from a hook.

### `env_hash` is resolved per episode, never inherited

A model swap does not move the `composition_digest`, so nothing is appended and the
stored composition file keeps the `env_hash` it was first seen under. `reconcile` calls
`env_pin.build` for each episode instead (ADR-007). Reading the pin off a composition
record would make an episode claim an environment it did not run in, and the comparison
layer's refusal on a differing `env_hash` — the only thing separating *we changed a
plugin* from *we changed the model* — would be checking a fossil.

`model_basis` travels beside the hash and is never omitted, for the reason ADR-007
gives: two pins that hash alike are comparable only if they were resolved the same way,
so the comparison layer must refuse on a differing basis exactly as it refuses on a
differing hash, and that refusal has nothing to read if the basis was dropped at write
time. It is **not** folded into the hash — doing so would re-hash an unchanged
environment for every user who gains an OTEL log, splitting one comparable population
into two that each fall below `min_episodes_per_digest`.

### Unknown is never zero

`tokens_total` and `tool_calls` are `null` when abacus recorded no figure, and
`reconcile._count` refuses anything that is not an `int` — including a `bool`, which is
an `int` subclass, so a hand-set `true` would otherwise arrive as a token count of 1.
Beads metadata is hand-editable and Dolt-synced from other machines; this field can
hold anything.

A `0` invented here would be summed into the numerator of tokens per outcome and make
the harness look *most* efficient exactly where the measurement failed. The absence is
also why the measure layer must never turn an empty population into a number:
`ledger.episodes` presents both an absent ledger and an unreadable one as `[]`, and
`verdict.coverage([])` is `None`.

### What never appears here

- **No issue text.** Neither `title` nor `close_reason` is carried through. The ledger
  holds measurements, not a copy of the issue tracker, and the verdict already carries
  everything a measure needs from the text.
- **No prompt text**, ever — only lengths and hash prefixes, and those live in
  `events.jsonl`, not here.
- **No credential.** `env_pin` is the only module that opens the settings `env` block,
  and it takes the pinned fields by name; a base URL is sanitised of userinfo and query
  string *before* it is hashed.
- **No command string.** The `bd close` command line that triggered a reconcile is
  tokenised by `reconcile.is_close_command` and discarded.
- **No absolute path.**

## Append-only, on movement

`record_episode` appends only when the record **moved** against the last reading of the
same `issue_id`. Movement is any difference outside `ledger.EPISODE_VOLATILE_FIELDS`,
which is exactly `("ts", "schema")`, compared over the *union* of both key sets so that
removing a field counts too.

| status | meaning |
|---|---|
| `new` | no previous reading of this id |
| `updated` | something outside the volatile fields differs |
| `unchanged` | nothing appended; the previous record is returned |

Both exclusions are deliberate and neither is a convenience. `ts` is when the reading
was taken, so comparing it would append on every trigger — and reconcile hangs off a
`Stop`, which is every turn. `schema` describes the record's *shape*: comparing it would
append one record per episode on every upgrade, claiming movement in work that finished
weeks ago, when a reader can already see which shape a record has by reading it.

Everything else counts, **including a field this version does not declare**, which errs
toward appending. The two failure directions are not symmetric: an unnecessary append is
a visible extra line, a missed one is a reading nobody stored and nothing can detect
afterwards.

**A downgrade is appended too.** If someone deletes `hfit_verdict` and the episode reads
as `unstated` again, that is a movement and it is recorded. This layer records what was
observed; deciding that a stronger basis outranks a weaker one is `reconcile`'s job.
Refusing the downgrade would leave the ledger disagreeing with beads, which is the one
state no report could explain.

## Latest wins by file position, not by `ts`

`ledger.latest_episodes` keeps the **last** reading of each id in file order, then sorts
by `(closed_at, issue_id)`.

File position is the causal order of appends and is the only ordering that resolves
which reading is current. `$HFIT_NOW` freezes the clock for a whole run and a real clock
can step backwards, so two readings can carry the same or a decreasing timestamp — and a
timestamp sort would leave the winner undefined in exactly the case that matters most:
it could prefer a stale `unstated` over a verdict a human had just declared.

The output is ordered by close time rather than by append order because reconcile order
is the order sessions were *opened* in, not the order work *finished*. The sort is on the
ISO-8601 Z strings, which is chronological, and puts an unparseable value at one end
rather than raising.

## Refusals

`record_episode` returns `{"ok": false, "reason": …}` and writes nothing.

| reason | meaning |
|---|---|
| `not_acknowledged` | consent was never given (ADR-014) |
| `config_changed` | consent was given, then a governing key moved |
| `no_issue_id` | absent, or the record is not a dict |
| `no_closed_at` | no episode boundary, therefore no measurement window |
| `write_failed` | the append itself failed |

`not_acknowledged` and `config_changed` present as the same silence and have different
remedies, which is why they are two reasons and not one. `write_failed` is currently the
one reason outside the declared `ledger.EPISODE_REFUSALS` tuple; a caller must treat the
vocabulary as open until that is reconciled.

A refused *issue* is a skip, not a refused run: `reconcile` counts it under `skipped` and
carries on, because one malformed row in a Dolt-synced database must not stop a project's
measures from ever updating again.

## Consumers

- `hooks/lib/measures.py` — all four measures group these records by
  `(composition_digest, env_hash)`.
- `hooks/lib/verdict.py::coverage` — `verdict_coverage`, which gates tokens per outcome
  (ADR-008).
- `hooks/lib/beads_write.py` — projects a bounded subset outward as `hfit_*` keys; see
  `contracts/output/bd-metadata-write.md`.
- `hooks/scripts/fitness.py --json` → `contracts/output/fitness-report.md`.
- `skills/harness-outcome/SKILL.md`, via that CLI only. A skill never reads this file
  directly, so the layout stays free to change behind the report.

## SemVer

- **Contract version:** 1.0.0
- **Dated against:** `bd` 1.1.2 and abacus 0.3.x as observed on this machine, 2026-09.
- **Deprecation policy:** adding a key is a **minor** bump — and note that an undeclared
  key participates in the movement comparison, so a minor bump appends one record per
  open-ended episode on first sight. Removing or retyping a key is **major** and requires
  a `schema` increment: old records stay on disk and a consumer must be able to tell which
  shape it is holding. Adding a member to `VERDICTS`, `BASES` or `UNSTATED_REASONS` is
  **minor**; removing one is **major**, because a stored record can carry it. Changing
  `EPISODE_VOLATILE_FIELDS` is **major** regardless of the record's shape, because it
  silently repartitions every project's append history.

## SLA + telemetry

- **Latency:** inside the `PostToolUse:Bash` budget of 20s and the `Stop` budget of 15s,
  both of which are dominated by one `bd list --all --json`. The write itself is a read
  of `episodes.jsonl` and one append.
- **Durability:** an interrupted append can leave a truncated final line; every reader
  tolerates one and skips it rather than failing the whole log.
- **Availability:** a blocked write degrades to *nothing recorded*, not to a swallowed
  traceback — `write_failed` is returned and the next reconcile re-attempts, because
  reconciliation is discovery and missing a moment costs nothing.
- **Telemetry:** none. No upload, no endpoint, no network call of any kind.
