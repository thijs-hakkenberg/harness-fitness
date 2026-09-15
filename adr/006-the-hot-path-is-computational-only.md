# ADR-006 — The hot path is computational only, and the instrument is the smallest thing in the profile it prints

- **Status:** accepted
- **Date:** 2026-09-14
- **Governs:** every script in `hooks/scripts/`, every module in `hooks/lib/`, and the `hooks.json` budgets
- **Related:** [ADR-004](004-there-is-no-pretooluse-hook.md) there-is-no-pretooluse-hook, [ADR-011](011-claude-code-is-the-only-permitted-llm.md) claude-code-is-the-only-permitted-llm, [ADR-013](013-the-inferential-verdict-is-a-backfill-tool.md) the-inferential-verdict-is-a-backfill-tool

## Context

This plugin measures what a harness costs. It is itself part of the harness. Every
resource it consumes — a token, a millisecond, a line in a context window — is
added to the quantity it reports, and reported as if it belonged to the work.

That is not a rounding error. Three of the four measures are ratios with the
user's own spend in the numerator:

- A `SessionStart` response may carry `additionalContext`, which is injected into
  the context window of **every** session. Twenty lines of harness summary at
  session start is a few hundred tokens, before caching, on every session for the
  life of the install. Tokens per outcome would then include the instrument's own
  narration of tokens per outcome. The measure would not merely be inflated; it
  would be inflated *by an amount proportional to how many sessions the user
  opened*, which is one of the things the measure is used to compare.
- An LLM call from a hook would spend tokens attributable to no episode, land in
  the `session_window` numerator, and widen `attribution_gap_pct` — the exact
  field published to expose unattributable spend.
- A hook that takes a second is a second of the user's wall clock, on an event
  they did not ask for, in service of a number they will read once a week.

And there is a fourth cost that is specific to this plugin and easy to miss: **a
component is a line in a composition profile.** The report prints
`{"ff_inf": 41, "ff_comp": 9, "fb_comp": 14, ...}`. Every hook, skill and agent
this plugin registers increments one of those buckets. A measurement tool with six
hooks and five skills makes the harness it is describing look eleven components
larger than the harness the user chose, and a `profile_delta` between two
compositions is read against that inflated baseline.

## Decision

**Nothing on the hot path is inferential.** No hook invokes a model, spawns an
agent, or reads a prompt for meaning. Classification is a static table
(`taxonomy.py`); the digest is a hash; the verdict ladder's `lexical` rung is a
word list. The one inferential path in the whole plugin is
`/hfit:outcome --infer`, which is a skill a human runs, off by default, and
governed by [ADR-013](013-the-inferential-verdict-is-a-backfill-tool.md).

**No hook emits `additionalContext`, or any other response field.** Every hook
exits 0 with empty stdout. Everything a hook learns goes to a file, read on
demand. `tests/integration/test_snapshot_composition.py` asserts empty stdout as a
first-class property rather than as an incidental observation, because emitting
nothing is a feature and the only way to keep a feature is to test it.

**`hooks/` imports the standard library and nothing else.** Not a portability
convenience — a dependency is a component, and a plugin that makes the user
install a package has changed their environment to measure it. The test suite may
use `pytest`, `pytest-bdd`, `coverage` and `pyyaml`; hook code may not use any of
them. Python 3.9.6 is the floor, and the CI matrix runs 3.9 so the floor is
honest rather than aspirational.

**The plugin keeps the smallest surface that answers the brief.** Six hooks, not
ten ([ADR-004](004-there-is-no-pretooluse-hook.md)). One CLI, not five. Where a
signal is available from the event log, the plugin reads the log rather than
registering a hook to produce it.

**The plugin's own contribution to the profile is published, not hidden.** The
composition record includes `harness-fitness` like any other plugin. A reader can
therefore subtract it. A measurement tool that omitted itself from its own
inventory would be lying in the most embarrassing way available to it.

## Consequences

**Reversing any part of this is a major version bump.** Adding
`additionalContext` to `SessionStart` changes the contract in
`contracts/input/session-start.md` and changes every historical
`tokens_per_outcome` it is compared against, so the ADR has to be reversed before
the code can be. The contract says so at its version table.

**Latency is a tested property, not an intention.** The `hooks.json` timeouts are
budgets rather than expectations — `SessionStart` gets 15 s for a job that should
take well under one, because eight disk reads on a cold page cache is not an
error. The integration tests assert the work completes, so a regression that turns
a 200 ms hook into a 3 s hook is caught rather than absorbed into the budget.

**Some measurements are simply given up.** Prompt semantics is the clearest case:
whether a human turn is a correction or a continuation is exactly the question an
LLM could answer, and answering it would cost tokens on the hot path. So M2
publishes a raw count plus a stamped `classifier: lexical-v1` and an
`autonomy_confidence` flag, and the honest three-valued answer stands in for a
better one that cannot be afforded.

### The library is total, so `fail_open` is a backstop and not the mechanism

Worth recording, because it is the kind of property that decays silently and then
gets rediscovered as a bug.

`hook_io.fail_open` turns any `Exception` into exit 0 with discarded stdout — the
guarantee that a measurement tool cannot break a session. It is tempting to read
that wrapper as *the* error strategy and write library code that raises freely,
trusting the net.

The library does the opposite, and should keep doing it. `state_store` returns
defaults on an unreadable, absent or malformed file. `hfit_time` returns a usable
timestamp whatever `$HFIT_NOW` contains. `hfit_config` falls back to `DEFAULTS`.
`consent.require_consent` returns `(False, reason)` rather than raising on a
missing acknowledgement. **On every input a hook can actually meet, these
functions return a value; `fail_open` never fires.**

Two things follow. First, the ordinary failure paths are *exercised* — a corrupt
ledger produces a tested return value, not an untested exception whose only
assertion is "the process exited 0". Second, when `fail_open` does swallow
something, it is by definition a case nobody anticipated, which is the only thing
a last line of defence should ever be catching. Under `$HFIT_DEBUG=1` it re-raises
for exactly that reason.

The failure mode this guards against: a library rewritten to raise on bad input
would still pass `test_fail_open.py`, because the hook would still exit 0. The
suite would stay green while every error path in the plugin became a silent
`SystemExit` with no test covering what it swallowed. Totality is the property
worth keeping; the wrapper is not a substitute for it.
