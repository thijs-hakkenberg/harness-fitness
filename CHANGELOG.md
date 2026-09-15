# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Contract-level versioning is tracked separately in each file under `contracts/`:
a contract can change shape without the plugin's version moving, and the
contract file records its own history so a consumer can tell what it may rely on.

## [Unreleased]

### Added

- [ADR-017](adr/017-mutation-testing-does-not-substitute-for-a-red-phase.md) —
  mutation testing does not substitute for a red phase. Records a lapse in 0.2.0's
  step 3, where thirty hook integration tests were written after their implementation
  and a mutation pass was treated as having discharged the missing red phase. The
  rebuttal is that a red phase also proves a test asserts the *intended* rather than
  the *implemented* behaviour, and a mutation pass cannot separate those: a test
  written from the spec and a test written from the code kill the same mutants.
  `CONTRIBUTING.md` §1 is unchanged — the rule was not false, the practice lapsed.

## [0.2.0] — 2026-09-15

The episode layer. 0.1.0 could say what the harness was composed of; it had no way
to say **what happened while that harness was in force**. This release supplies the
unit — an episode is one closed beads issue, because `closed_at` is the only
boundary in the system that is not a guess — and the thing abacus has never had: an
**outcome**. abacus records lifecycle faithfully and never success, so
`tokens_per_outcome` had a numerator and no denominator.

The measures are still `null`. What moved is that two of the numbers they are gated
on are now real: `episodes_seen` and `verdict_coverage` are read off a ledger rather
than reported as not-implemented. That ordering is deliberate — the gate
([ADR-008](adr/008-verdict-coverage-gates-tokens-per-outcome.md)) has to be
measurable before the measure it gates is written, or the first thing anyone would
see is a number computed over a population nobody had checked.

The uncomfortable finding this release is shaped around: **on this machine 10 of 12
closed issues carry `bd`'s default `close_reason` of exactly `"Closed"`.** Verdict
coverage is not a threshold that will be met by better code. It is a habit, and
`/hfit:outcome` exists because the habit cannot be retroactive — the moment of the
close has already passed for almost every issue that matters.

### Added

- **`hooks/lib/beads_read.py`** — read-only access to the beads database, which is
  where the episode population comes from. Three wire shapes were measured against
  `bd` 1.1.2 on a real database rather than inferred: `bd list --all --json` prints a
  bare array with no `issues` wrapper, `bd show <id> --json` prints a
  single-element array rather than the object, and a project with no database exits
  **1 with empty stdout**. That last one is the reason the module exists in this
  shape: a reader mapping non-zero to "no issues" would report a project with a
  hundred closed episodes as a project with none, so every function returns
  `issues: None` on failure and **never `[]`**. `no_database` and `bd_error` are named
  apart because one is a benign sentence and the other is a defect, and a single
  reason covering both would be wrong half the time.
- **`hooks/lib/verdict.py`** — the verdict ladder
  `declared > structured > lexical > inferential > unstated`, which supplies the
  denominator abacus does not have: abacus records lifecycle and never outcome. Each
  answer carries the *basis* it was reached by, because the five ways of learning an
  outcome are not equally trustworthy and must not be averaged into one claim.
  `unstated` is the dominant real case — 10 of 12 closed issues measured here carry
  `bd`'s default `close_reason` of exactly `"Closed"` — and it is not a failure:
  counted as a rejection it reports a working harness as a failing one, counted as a
  success it puts unexamined work in the passing denominator, so it is excluded from
  both and published as a coverage shortfall via `verdict_coverage`
  ([ADR-008](adr/008-verdict-coverage-gates-tokens-per-outcome.md)). The lexicon
  refuses rather than guesses: text pointing both ways is `unstated` with
  `reason: "ambiguous"`, and negation is handled explicitly because `"not working"`
  contains `"working"` — the single most damaging error available here, since it
  moves an episode into the passing count. `inferential` is attempted *after* the
  lexical read ([ADR-013](adr/013-the-inferential-verdict-is-a-backfill-tool.md)), so
  switching inference on can never weaken the basis of an episode that already had a
  better one.
- **`episodes.jsonl`** (in `hooks/lib/ledger.py`) — the store of record for what
  happened, one reading per closed beads issue. `reconcile` runs from four triggers,
  so the same episode is offered repeatedly and the append rule is the whole design:
  **append only on movement**, the same rule `changes.jsonl` already uses. Both halves
  are load-bearing in opposite directions. Appending on every offer would turn the
  store of record into a session log whose duplicates are indistinguishable from
  genuine re-entries; skipping on a known issue id would freeze the first answer
  forever, and since an episode reconciled at `Stop` is usually `unstated` and
  `/hfit:outcome` exists so a human can state it afterwards, that would make
  `verdict_coverage` permanently unimprovable — defeating the only mitigation the
  coverage gate has. So a record is never rewritten, a second reading is appended
  beside the first, and the **last append wins**.

  Two details are not the obvious choice and are deliberate. Movement is compared
  over everything *outside* a small volatile set (`ts`, `schema`) rather than over a
  declared set of material fields: a later version adding a touch or catch count then
  moves the record without anyone remembering to register it, and the two failure
  directions are not symmetric — an unnecessary append is a visible extra line, while
  a missed one is a reading nobody stored and nothing can detect afterwards. And the
  winner is resolved by **file position, not by `ts`**: `$HFIT_NOW` freezes the clock
  for a whole run and a real clock can step backwards, so a timestamp sort would leave
  the winner undefined — and in the case that matters could prefer a stale `unstated`
  over the verdict a human had just declared. An episode with no `closed_at` is
  refused, matching `beads_read.closed_issues`: without it there is no window for a
  measure to cover.
- **`hooks/lib/reconcile.py`** — episode discovery, deliberately not episode
  *tracking*. One idempotent `reconcile(cwd)` reads the closed issues, classifies each
  one and offers it to the ledger, and it is safe to run as often as anything cares to
  run it. That is what makes missing the moment of a close cost nothing: a close made
  from another terminal, by an editor, or on another machine and arrived over Dolt sync
  is found on the next pass, with no dependency on a session's state having survived.
  `is_close_command` tokenises with `shlex` and **never regex-matches**, because the
  literal text `bd close` appears in echoes, commit messages and heredocs, and a regex
  would pay for a `bd list` on every one of them — worse, an unbalanced quote must
  never raise, so an odd shell line degrades to "not a close" rather than to a
  traceback on a hook. Counts are reported as `new`/`updated`/`unchanged`/`skipped`
  rather than as a single total, since "nothing moved" and "nothing was readable" are
  different facts. Refusals extend `beads_read`'s reasons with `not_acknowledged` and
  `config_changed`, kept apart for the same reason `no_database` and `bd_error` are:
  consent never given and consent invalidated by a config change need different
  remedies.
- **`hooks/lib/beads_write.py`** — a bounded `hfit_*` metadata index on the closed
  issue, so a verdict reached on one machine reaches the others by `bd`'s own Dolt
  sync. The ledger stays the store of record; this is an index, and
  `beads.write_metadata: false` means no `bd` process runs at all. Three behaviours
  were measured against `bd` rather than assumed: `--set-metadata` **merges** rather
  than replacing, it works on an already-closed issue, and **a value containing
  whitespace arrives as two words and is silently truncated** — so lists and dicts are
  written as compacted JSON and any remaining whitespace collapses to an underscore.
  An `abacus_*` or undeclared key **raises**; only an environmental failure refuses.
  That asymmetry is the point: a refusal is a value a caller can ignore, and an ignored
  one would silently overwrite another tool's cost figures
  ([ADR-010](adr/010-hook-commands-are-hashed-never-stored.md) is the neighbouring
  discipline). The prefix check runs over the **whole** key set before consent and
  before the subprocess, because a per-key check would permit a half-write. An
  unmeasured value is an absent key and never a `0`: `hfit_hard_touches: 0` would
  replicate as the most flattering reading available — a fully autonomous episode —
  with nothing recording that nobody had measured it.
- **`hooks/scripts/reconcile_hook.py`**, wired to `PostToolUse:Bash` and `Stop` — the
  two triggers that make discovery continuous. The matcher is what makes the event
  affordable and the `shlex` predicate is what makes it cheap; `Stop` is unconditional
  because a stop has no command to inspect, and it is the trigger that catches every
  close this session did not make. `stop_hook_active` is honoured for cost rather than
  correctness — this hook emits nothing and can never block a stop, so it cannot cause
  the loop that flag exists to break. Both emit no response field at all
  ([ADR-006](adr/006-the-hot-path-is-computational-only.md)): a plugin that measures a
  harness must not steer it.
- **`episodes_seen` and `verdict_coverage` are real numbers** in
  `fitness.py --json`, replacing two not-implemented placeholders. Both are
  **three-valued**, and the seam between the two zero-shaped cases is the whole of the
  design. Over an *absent* ledger `episodes_seen: 0` is a correct count — nothing has
  been reconciled here — while over a ledger that **exists and yields nothing** it is
  `null` with an `episodes-unreadable` gap, because the file is only ever created by an
  append that succeeded, so an empty read is a fault on this machine and not a fact
  about the user's work. The two halves of `verdict_coverage` disagree in the same
  place and are allowed to: `n: 0` is a count over an absent ledger while
  `coverage: null` refuses, since a ratio with no denominator is not `0.0` and a `0.0`
  would send someone looking for a habit problem they do not have. Below
  `min_verdict_coverage` the report now carries an `unstated-verdict` gap naming the
  actual percentage beside the required one, because "coverage is low" leaves a reader
  unable to tell a habit that is nearly there from one that has not started.
- **`/hfit:outcome`** (`hooks/scripts/outcome.py`,
  `skills/harness-outcome/SKILL.md`,
  [`contracts/output/outcome-declaration.md`](contracts/output/outcome-declaration.md))
  — the only surface that produces a `declared` verdict, the top rung of the ladder,
  for an issue already closed without one. It exists because verdict coverage cannot be
  repaired retroactively at the moment of the close: that moment has passed for almost
  every issue that matters. `--verdict` is narrowed to the four declarable values at
  the `argparse` boundary, so a mistyped one exits non-zero and names all four rather
  than recording a verdict nobody chose. The load-bearing property is a negative one no
  schema can carry — **the verdict comes from the user and is never the model's
  judgement of how the work went** — so the obligation is stated three times in the
  skill's prose: an `unstated` episode is honestly excluded from every measure, while an
  invented `accepted` silently corrupts all of them. `ok` and `recorded` are separate
  keys because the write and the ledger read-back are separate facts:
  `recorded: false` beside `ok: true` is an honest partial success the next `Stop` hook
  finishes, and a consumer must not send the user to redo work that is already stored.
  `beads.write_metadata` is checked here rather than inherited, because
  `beads_write.set_metadata` does not honour the toggle and the toggle has to mean no
  `bd` process at all.
- **`spec.manifest.yaml`** — the repo as one node in an enterprise-architecture
  graph: what it is, which artefact pillars describe it, which interfaces it
  offers, and what it depends on. As shipped here that is three inbound interfaces
  (`SessionStart`, `PostToolUse` and `Stop`) and six outbound ones — the composition
  record, the change log, the report CLI, the beads metadata index, the outcome
  declaration and the consent record. An `event:` key is what binds an inbound
  interface to its hook, so wiring a hook without declaring it fails a test, and so
  does declaring one that is not wired.
- **`tests/unit/test_spec_conformance.py`** — the assertions that keep the
  documentation and the software agreeing with each other. It does not check that a
  document is *right*; it checks that a reference resolves. Every ADR number cited
  anywhere in the repo has a file, every relative link to one resolves from the
  directory that links it, every interface names a script that exists and cites a
  contract that exists, every inbound `latency_p99_ms` equals its `hooks.json`
  timeout, and the three version sources agree. The gap in the ADR sequence is
  asserted **in both directions**, so writing a reserved number forces deleting the
  reservation rather than leaving a stale one behind.

### Fixed

- **Five ADRs were cited by shipped code, contracts and tests without existing.**
  `hooks/hooks.json` and `test_hooks_manifest.py` both pointed at `adr/004`; the
  contracts and the changelog pointed at `adr/006`, `008`, `010` and `014`. A public
  repo referring to a file that is not there is a worse artefact than one that says
  nothing, because the reference reads as a promise that the reasoning was written
  down. All five are now written:
  [ADR-004](adr/004-there-is-no-pretooluse-hook.md) (no `PreToolUse`, and no
  `SubagentStop`, `Notification` or `PermissionRequest`),
  [ADR-006](adr/006-the-hot-path-is-computational-only.md) (the hot path is
  computational only, and the instrument is the smallest thing in the profile it
  prints), [ADR-008](adr/008-verdict-coverage-gates-tokens-per-outcome.md) (verdict
  coverage is a headline number and below threshold it refuses tokens per outcome),
  [ADR-010](adr/010-hook-commands-are-hashed-never-stored.md) (hook and MCP command
  strings are hashed, never stored) and
  [ADR-014](adr/014-consent-gates-every-write.md) (consent gates every write, and
  the gate is checked before a path is resolved).
- **Nine placeholder ADR links** pointed at a directory instead of a file, so
  following one landed nowhere.
- **Three declared artefact pillars were untracked.** `features/`, `contexts/` and
  `adr/analysis/` are empty at 0.1.0, and git does not store empty directories — so
  they existed locally and would have been absent from a fresh clone, failing the
  new conformance test in the one environment where nobody is watching. Each now
  holds a tracked placeholder, and the emptiness assertion ignores dotfiles so the
  placeholder is not mistaken for content.
- **`pyyaml` is declared where it is installed.** It reads `spec.manifest.yaml` and
  is imported inside a fixture, not at module scope: a missing PyYAML skips the
  manifest tests and leaves the dangling-reference guard running. An
  always-running test asserts the CI workflow installs it, because a guard that
  quietly disables itself in CI is worth nothing there.

## [0.1.0] — 2026-09-14

First public release. It answers one of the two questions in the brief — **what is
this project's harness composed of, and when did it change?** — and refuses, in
writing, to answer the other one yet.

The primitive is the `composition_digest`: a canonicalised inventory of the
*enabled* harness, tagged with the feedforward/feedback × computational/inferential
taxonomy, sitting beside the environment pin. Nothing before this could group
episodes by what the harness actually was — composition leaked out only as token
attribution, and only for components that spent tokens, so a configured-but-unused
hook, skill or MCP server was invisible. With a digest, a composition change becomes
an identity you can diff and correlate rather than a thing you remember making.

The measures are deliberately absent, reported as `null` beside a gap that says so.
An estimate here would be indistinguishable from a measurement, and this is a tool
whose only value is that its numbers can be trusted.

### Added

- Repository skeleton: MIT licence, plugin and marketplace manifests, coverage
  and pytest configuration, CI on Python 3.9 and 3.12.
- Test harness: isolation fixtures giving every test a temporary `HOME` and a
  PATH-prefixed stub `bd`/`git` that records its argv, plus `otel_log`,
  `transcript`, `settings_tree` and `frozen_now`. Without these, no later red
  phase can be written honestly — a test that reads the real `~/.claude` is
  measuring the developer's machine, not the code.
- Foundation modules, each written against a failing test: `hfit_time` (a
  freezable clock), `_security` (path refusal and one-way command reduction —
  [ADR-010](adr/010-hook-commands-are-hashed-never-stored.md)), `state_store` (atomic, mode-restricted writes and
  lossy-tolerant reads), `hfit_config` (defaults, deep merge and the governing
  subset), `consent` (nothing is written until a fingerprint matches —
  [ADR-014](adr/014-consent-gates-every-write.md)) and `hook_io` (the fail-open hook protocol).
- [ADR-011](adr/011-claude-code-is-the-only-permitted-llm.md): Claude Code is
  the only LLM this plugin will ever use — no HTTP client, no provider SDK, no
  endpoint, no API key, not even as a fallback. Where a subagent cannot be
  dispatched, inference is unavailable and the verdict stays `unstated`.
  Asserted by `test_no_inference_dependency.py`, which parses every module under
  `hooks/` with `ast`; a bring-your-own-LLM path would have sent issue titles and
  commit diffs off the machine, breaking the one promise that makes a
  measurement tool adoptable.
- [ADR-013](adr/013-the-inferential-verdict-is-a-backfill-tool.md): the
  `inferential` verdict ranks *below* `lexical`, is reachable only from an
  interactive `/hfit:outcome --infer`, and exists to backfill issues closed
  before the `accepted:` habit — not to supply the priority-1 measure with a
  denominator.
- Composition inventory, first two modules: `settings_read` (the four settings
  layers, with the three merge rules Claude Code actually uses — mappings merge
  key-wise, hooks accumulate, lists replace — each asserted separately because
  getting one backwards would under-report the harness) and `plugin_scan` (what
  each *enabled* plugin contributes, read from its directory rather than trusted
  from its manifest).
- `taxonomy`: the FF/FB × COMP/INF classification, transcribed from the workshop
  component table and deterministic — no model is consulted, because a profile
  whose counts depended on an LLM's opinion of what a hook is would not
  reproduce. Rolls up to a `composition_profile` and a `profile_delta`, so a
  report can say "B added two FB·COMP sensors and removed one FF·INF guide"
  rather than printing two inventories side by side.
- [ADR-005](adr/005-a-pretooluse-block-is-a-feedforward-catch.md): `PreToolUse`
  and `PermissionRequest` blocks count as *feedforward* catches, against the
  source table. A deny happens before the action lands, so it prevents rather
  than detects; classified as feedback, a harness whose only control is an edit
  gate would report `feedforward_ratio = 0.0` — the opposite sign to the truth.
  The event list lives in config so the judgement is reversible, and `classify`
  reports `basis: "adr-005"` wherever its answer differs from the table.
- [ADR-001](adr/001-the-content-digest-hashes-content-not-mtimes.md): a plugin
  with no `gitCommitSha` falls back to a digest over file *content*, including
  the hook scripts its `hooks.json` names. Hashing mtimes instead would have
  reported a composition change on every fresh clone, splitting one measurable
  population of episodes into two that each fall below `insufficient_n`; hashing
  declarations alone would have left this plugin's own digest fixed across its
  entire build.
- [ADR-003](adr/003-project-slug-is-character-wise-and-resolved-on-disk.md):
  the project slug is character-wise and the transcript directory is resolved
  against the filesystem, because whether Claude Code collapses runs of
  separators is not determinable from any observable path. Guessing would have
  made a missing transcript directory read as a session with no human
  interventions, inflating autonomy.
- `composition`: the `composition_digest` — the primitive every measure groups
  by. Each field is either in the canonical form or excluded with the failure it
  avoids named beside it, because the digest has to be wrong in neither
  direction: missing a change merges two harnesses into one population and
  reports the mixture as if it described one thing, while moving spuriously
  splits ten episodes into two groups of five that each fall below
  `min_episodes_per_digest`. The `env` block is never opened — not filtered,
  never read — so no credential can reach a composition record even by accident.
- `env_pin`: the environment side of that pair, pinning the same five fields the
  rig pins so a signal found by this detector can be handed to the controlled
  confirmer and mean the same thing.
- [ADR-007](adr/007-the-composition-digest-sits-beside-the-env-hash.md): the
  composition digest sits *beside* `env_hash`, never inside it, so a model swap
  cannot read as a harness win — and a comparison across a differing `env_hash`
  is refused rather than reported. Records three findings the code cannot state
  on its own: the two hashes are deliberately different widths (12 is the
  `compositions/<digest12>.json` filename contract, 64 is imposed by the rig's
  result schema, and neither may be changed to match the other); **the model is
  not declarable** — measured, with `settings.model` saying `opus`,
  `env.ANTHROPIC_MODEL` saying something else entirely, and all 389 observed
  `api_request` events reporting a third value, so a disk-resolved model is
  marked `declared` and never presented as the model that ran; and that
  `model_basis` therefore has to travel *beside* the hash rather than inside it,
  leaving the comparison layer obliged to refuse on a mixed basis.

- `ledger`: where a composition becomes history — a content-addressed file per
  composition ever seen, plus an append-only log of the moments the identity
  moved, carrying `added`/`removed`/`changed` and the `profile_delta`. Three
  properties are load-bearing and each names the reader it protects: a composition
  file is **written once and never rewritten**, because episodes reference a digest
  by name and re-writing it would retroactively change what they say they ran
  under; a transition is appended **only when the digest moves**, because this runs
  on every `SessionStart` and a record per invocation would make `changes.jsonl` a
  session log whose duplicates are indistinguishable from real re-entries; and when
  the previous composition is unreadable every diff field is `null` rather than
  `[]`, because an empty list asserts the digest moved while nothing about the
  harness did — which cannot happen. Unknown is never zero.

- `snapshot_composition.py`: the `SessionStart` hook, and the only writer of
  `compositions/` and `changes.jsonl`. **It emits no `additionalContext`** — a
  `SessionStart` response may inject text into every session's context window, and
  a tool whose purpose is to measure what the harness costs must not become a line
  item in that cost ([ADR-006](adr/006-the-hot-path-is-computational-only.md)). Everything it learns goes to a file, read on
  demand. Driven as a real subprocess in every test, because an in-process call
  cannot catch the three failures that make a hook indistinguishable from a plugin
  that does nothing: an import error, a stdout-protocol violation, a non-zero exit.
  Two findings worth more than the code: the composition file's `env_hash` is
  **first-sighting provenance only** — the file is content-addressed and never
  rewritten, so a later session under a different model moves nothing and the stored
  pin keeps its original value, which means an episode must resolve its own
  `env_hash` at close time and must never read one off a composition record
  ([ADR-007](adr/007-the-composition-digest-sits-beside-the-env-hash.md)); and the library beneath this hook is **total** — `state_store`
  returns `False` and `hfit_time` falls back rather than raising — so `fail_open` is
  a last line of defence here and not the mechanism, and a blocked write degrades to
  *nothing recorded* rather than to a swallowed traceback. The second is asserted
  under `HFIT_DEBUG=1`, which would surface a swallowed exception if one existed.

- `fitness.py --json`: the one read surface, and the only thing a skill parses.
  Two properties are the contract rather than the implementation. **A failed read
  carries no result keys at all** — not `measures: {}`, not `measures: null`, not
  `episodes_seen: 0`, absent — because `null` is already spoken for: at this
  version it is the *legitimate* value of a successful read, so a failed read
  saying `null` would be indistinguishable from a successful one and a skill would
  report "no measures available" where the honest answer is "consent was never
  given". And **unknown is never zero**: every field that cannot be computed is
  `null` beside a `gaps[]` entry written in a sentence a person can act on, since
  `episodes_seen: 0` is a claim about the user's work rather than about the
  plugin's completeness. The surface also **writes nothing**, which is a
  measurement property and not tidiness — a reporting command that creates state
  has changed the thing it reports on, and before consent it would be creating
  that state in defiance of [ADR-014](adr/014-consent-gates-every-write.md). It does not read stdin, because under
  a skill stdin is a pipe that is never closed and a surface that reads it hangs
  the session; the tests close stdin rather than feeding it, so a regression there
  fails here.
- `contracts/output/fitness-report.md` and `contracts/output/composition-record.md`:
  the exact key set of a successful read and of a refused one, stated as ten keys
  and six rather than as a shape to infer. Written because the difference between
  them is the whole discipline — a consumer that cannot tell "absent" from "null"
  from "zero" has no way to be honest — and because the first-sighting semantics of
  a composition file's `env_hash` are invisible from the file itself.
- `/hfit:composition`, the first skill: what the harness in this project is made
  of, and when it last changed. Bound to the CLI's own `GAP_KINDS` tuple by
  `test_skills.py`, which requires an instruction for every kind the CLI can emit.
  That failure is the one worth a test, because nothing breaks when it happens: the
  model meets an unfamiliar `kind`, writes a plausible sentence about it, and the
  user reads an invented explanation from a tool whose entire purpose is to not
  invent. The suite extracts the fenced command blocks and **runs them**, since a
  skill's documented command is an execution path nothing else drives — every other
  test invokes the CLI by its own argv rather than by the string a session will
  execute.
- `acknowledge.py` and `contracts/output/consent-notice.md`: the only surface that
  can open or close the write gate, and until now it did not exist while three
  places already named it. `--accept` / `--show` / `--revoke` in a *required*
  group, so a bare invocation exits non-zero rather than agreeing to something — a
  script whose default action is "accept" has collected a keystroke, not a
  decision. `--show` writes nothing at all, asserted on the state root's
  *existence* rather than on a file listing, because a `makedirs` on the way to a
  read leaves a directory no file listing can see. The notice names the ledger
  root, says what is never written and that nothing leaves the machine
  ([ADR-011](adr/011-claude-code-is-the-only-permitted-llm.md)), and names the governing keys that would re-ask — bound to
  `hfit_config.governing()`, because a user re-prompted after editing a config
  file who cannot tell a deliberate re-ask from a bug will stop using the plugin.

### Fixed

- Two consent-gate assertions proved "nothing was written" from a file listing,
  which cannot see a leaked `mkdir`. Where the claim is that nothing exists before
  consent, the assertion is now on `os.path.exists`; where it is that nothing *new*
  appeared, `entries_under` counts directories too. `files_under` keeps its
  file-only semantics deliberately, and its docstring now says it cannot carry a
  "wrote nothing" claim alone — an empty directory under someone's `HOME` is a
  trace, and a promise that nothing is recorded before consent has to be provable
  against the filesystem rather than against a return value.
- `test_skills.py` asserted a *vocabulary* rather than an instruction. Its check
  that a skill forbids inventing a number was `"null" in text` plus `"never" in
  text` — and every skill here says "never" about half a dozen other things, so it
  passed for a document containing no such prohibition at all, and kept passing
  when `Never fill in a null` was flipped to `Always`. Found by mutation, not by
  the suite. The assertion is now on a negated verb *of fabrication* co-located on
  one line, which is as close to the real property as a lexical test can get, and
  unlike the original it fails when the instruction is deleted.
- `model_basis` was resolved at every session start and then discarded at write
  time. `env_pin` returns it, [ADR-007](adr/007-the-composition-digest-sits-beside-the-env-hash.md) obliges the comparison layer to
  refuse on a differing basis exactly as it refuses on a differing `env_hash`, and
  `composition.build` accepted only the hash — so the obligation was unhonourable
  from stored data. Found by designing the read surface rather than by a failing
  test, which is the uncomfortable part: every test was green because every test
  asserted the hash. A record carrying the hash without the basis is worse than one
  carrying neither, because it presents that hash as sufficient for a comparability
  decision it cannot support — two pins that hash alike are comparable only if they
  were resolved the same way. Now threaded through `composition.build`,
  `snapshot_composition.py` and `changes.jsonl`, with the `null`/`"unknown"`
  distinction preserved verbatim: `"unknown"` means the resolver looked and found no
  model, `null` means no pin was supplied, and only the first is comparable against
  another `"unknown"`.
- Six assertions in `test_env_pin.py` that compared two settings trees built
  before either was read. There is one `HOME` per test, so the second build
  overwrites the first — which made three "this moves" assertions fail as though
  the implementation were wrong, and, far worse, made three "this holds still"
  assertions pass **without comparing anything**. A hash whose guards against
  spurious movement are vacuous has no guards at all. The fixture's docstring now
  records the trap, since every later module compares two trees the same way.

[Unreleased]: https://github.com/thijs-hakkenberg/harness-fitness/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/thijs-hakkenberg/harness-fitness/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/thijs-hakkenberg/harness-fitness/releases/tag/v0.1.0
