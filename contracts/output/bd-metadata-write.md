# Contract — beads metadata write (`hfit_*`)

**Version:** 1
**Producer:** `hooks/lib/beads_write.py`
**Consumer:** `bd` (the issue's `metadata` map), and any machine the database is
Dolt-synced to
**Governing ADRs:** ADR-007 (composition digest beside env hash), ADR-014 (consent
gates every write)

## What this is, and what it is not

The **project ledger is the store of record.** `episodes.jsonl` holds the full episode
and every measure derived from it. These metadata keys are a *bounded index*: enough
of the episode to make it findable and comparable from another machine, written onto
the closed issue so it travels with `bd`'s own Dolt sync rather than needing a sync of
its own.

That framing decides every rule below. This is a write into a data store this plugin
does not own, shared with a tool this plugin does not control, replicated to machines
this plugin cannot see.

## The namespace boundary

`abacus_*` has exactly one permitted constructor:
`Plugin.ClaudeCode.TaskCostTracker/hooks/lib/attribution.py`. Two tools writing one
namespace means whichever ran last wins and neither knows it lost.

So the producer **raises** on a key outside `hfit_`, rather than refusing:

- A refusal is a return value, and a return value can be ignored. An ignored refusal
  here is a silent overwrite of another tool's cost figures.
- An `abacus_` key is never a condition of the environment. It cannot arrive from a
  malformed database, a missing `bd` or a hostile issue id — only from a defect in
  this repository, which should behave like one.
- Raising is safe at the hook boundary: `hook_io.fail_open` catches it and exits 0,
  and `HFIT_DEBUG=1` re-raises for the developer who caused it.

The check runs over the **whole** key set before the subprocess. A per-key check would
permit a half-write where some keys landed, the caller saw an exception, and nothing
records which half succeeded.

## The key vocabulary

Bounded, and enforced as bounded: an undeclared key raises for the same reason a
misnamespaced one does. A per-event or per-tool key would grow metadata without limit
on an issue abacus is also writing to.

| key | type | source | available from |
|---|---|---|---|
| `hfit_schema` | int | this contract's version | 0.2.0 |
| `hfit_composition_digest` | str(12) | `composition.canonical` via the ledger | 0.2.0 |
| `hfit_env_hash` | str(64) | `env_pin.build` at close time | 0.2.0 |
| `hfit_model_basis` | str | `env_pin.MODEL_BASES` | 0.2.0 |
| `hfit_verdict` | str | `verdict.VERDICTS` | 0.2.0 |
| `hfit_verdict_basis` | str | `verdict.BASES` | 0.2.0 |
| `hfit_capture_ok` | bool | `verdict.classify` | 0.2.0 |
| `hfit_tokens_basis` | str | M1 | 0.3.0 |
| `hfit_hard_touches` | int | M2 | 0.4.0 |
| `hfit_touch_kinds` | list | M2 | 0.4.0 |
| `hfit_soft_touches` | int | M2 | 0.4.0 |
| `hfit_autonomy_confidence` | str | M2 | 0.4.0 |
| `hfit_ff_catches` | int | M4 | 0.5.0 |
| `hfit_fb_catches` | int | M4 | 0.5.0 |
| `hfit_escapes` | int | M4 | 0.5.0 |

`hfit_model_basis` is a **fifteenth key, added to the plan's declared fourteen.**
ADR-007 obliges the comparison layer to refuse on a differing `model_basis` exactly as
it refuses on a differing `env_hash`. Shipping the hash to the cross-machine index
without the basis would leave that rule unenforceable precisely where comparison is
most likely to be cross-machine, and most likely to be comparing an observed model
against a declared one. abacus's own ceiling falsifier found no limit at 200 keys, so
fifteen is not a capacity question.

## Unknown is an absent key, never a zero

A measure with no implementation yet is **omitted**. So is a value that is `None`.

This is the ordinary "unknown is never zero" rule, but at this boundary it has teeth
it does not have in a local file. `hfit_hard_touches: 0` would not merely be a wrong
local number — it would replicate to every machine sharing the database as the most
flattering possible value, a fully autonomous episode, with nothing anywhere recording
that no one had measured it. An absent key is the only encoding that cannot be
mistaken for a measurement, and it is also the one a later release can fill in.

`hfit_schema` is the exception and is always written: a reader needs to know which
shape it is reading before it can interpret an absence as "not yet measured" rather
than "measured as nothing".

If nothing but the schema is knowable, **no command runs at all** — a `bd update`
carrying only `hfit_schema` would claim an index exists where none does.

## Value rendering

Measured on `bd` 1.1.2:

- `bd update <id> --set-metadata k=v`, repeated per key. Metadata **merges** rather
  than replaces, so one invocation carries every pair and `abacus_*` keys already on
  the issue are untouched.
- It works on a **closed** issue, which is what lets this run after `bd close` lands
  rather than having to race ahead of it.
- Values round-trip with their JSON types — an int stays an int.
- **A value containing whitespace reaches `bd` as two words and silently truncates.**
  A silently truncated value is the worst available outcome: neither an error nor the
  datum, and nothing downstream can tell.

So: bools render `true`/`false`; lists and dicts render as compact JSON
(`separators=(",", ":")`); and any remaining whitespace in a scalar collapses to
underscores.

Keys are emitted in sorted order. Not cosmetic — an argv that varies between runs
cannot be asserted on, and an un-assertable command line is one in which nobody
notices a new key appearing.

## Validation before writing

`hfit_verdict` is checked against `verdict.VERDICTS`. Beads metadata is hand-editable
and arrives from other machines, so a verdict this version does not recognise can
reach the producer; writing it back would launder an unrecognised string into the
index other machines read as authoritative. An unrecognised verdict is omitted, not
substituted.

No issue text is written. Neither the title nor the close reason nor the derived
`verdict_reason` is round-tripped into metadata — `bd` owns those fields, and a copy
in metadata is a stale duplicate of data one field away.

## Refusals

Returned as `{"ok": false, "reason": …, "keys_written": null}`.

| reason | meaning |
|---|---|
| `not_acknowledged` | consent has never been given (ADR-014) |
| `config_changed` | consent was given, then a governing key moved — separate because the remedy is a re-acknowledgement, where `not_acknowledged`'s is a first one |
| `writes_disabled` | `beads.write_metadata` is off |
| `no_issue_id` | the episode names no issue |
| `nothing_to_write` | every indexable value was `None` |
| `no_database` | the project does not use beads — benign |
| `bd_error` | `bd` ran and failed — a defect worth investigating |
| `bd_unavailable` | `bd` absent, not executable, or rc 127 |
| `timeout` | `bd` exceeded the read timeout |
| `unparseable` | (inherited vocabulary; not reachable on a write) |

`keys_written` is `null` on every refusal, never `0`. `bd update` is not observably
atomic across keys from this side, so after a failure the number written is genuinely
unknown — and `0` would be a stronger claim than the evidence supports.

`no_database` and `bd_error` are separate because one is a project that does not use
beads and the other is a defect. Reporting them alike would make the benign case
indistinguishable from the one worth investigating.

## The batch, and the two rules that bound it

A reconcile offers every episode that moved, and the first reconcile of a project with a
long closed backlog offers the whole backlog at once. That batch runs behind a hook with
a 15–20 s budget, so `write_episodes` bounds it two ways. Neither works without the
other.

**A cap** bounds the healthy case, where a write costs tens of milliseconds and twenty
are nothing: `MAX_EPISODES_PER_RUN = 20`, newest close first so the most recently useful
episodes are the ones indexed.

It is deliberately **not a config key.** The cap exists to keep a timeout honest, and a
bound with that job cannot be user-tunable without the timeout becoming a lie: raised to
500, every `Stop` would be a hook killed partway through, leaving exactly the partial
index the cap exists to prevent. It would also not be a *governing* key, so doing so
would not even cost a re-acknowledgement to make it visible.

**Halting on an environmental failure** bounds the pathological case, which the cap alone
does not — twenty timeouts at the read timeout each exceed a `Stop` budget by an order of
magnitude however small the cap is. Every reason inherited from `beads_read.REASONS` is a
property of the *project* (no database, no `bd`, a hung one), so once one write fails that
way every remaining write is already known to fail. A per-episode refusal (`no_issue_id`,
`nothing_to_write`) is a property of one record and does **not** halt — otherwise a single
malformed row arriving over Dolt sync would stop a project's index from ever updating
again.

### The known residual

**Episodes past the cap are not written and not retried.** A later reconcile finds their
ledger records unchanged and does not offer them again, so they stay unindexed in beads
while remaining complete in `episodes.jsonl`.

This is stated rather than fixed because the ledger is the store of record and this is an
index: an unindexed episode is invisible from another machine and fully present on this
one. A retry queue would be a second piece of state to keep consistent with the ledger,
for a case that arises once per project. `write_episodes` returns `capped: true` so a
caller can tell, and a batch's counts distinguish the two shapes of incompleteness:

| `ok` | `reason` | meaning |
|---|---|---|
| `false` | a gate reason | nothing ran; **every** count is `null`, `capped` included — `false` would read as "nothing was dropped" about a run that never happened |
| `true` | `null` | the whole batch was attempted |
| `true` | an environmental reason | the run happened and stopped; the counts are real, because the number written before a halt is genuinely observed |

`ok` therefore says *the counts can be believed*, not that every write succeeded.

## Ordering and safety

- **Consent is checked before the subprocess.** An unacknowledged install must not
  execute a process in the user's repository, let alone write to its database.
- **`shell=False` with an explicit argv.** An issue id reaches this module from a
  `bd close` command line parsed out of a hook payload, which is untrusted input. A
  shell metacharacter in an id must be an inert string, not a command.
- **The subprocess runs with `cwd` set to the project.** `bd` resolves its database by
  walking up from the working directory, so a write from the wrong directory lands in
  whichever database happens to sit above this process.
- **`beads.write_metadata: false` means no `bd` process at all**, not a suppressed
  argument. Someone who does not want a measurement tool writing to their issue
  tracker must be able to say so and have it mean that. The ledger is unaffected —
  this whole index is optional.
