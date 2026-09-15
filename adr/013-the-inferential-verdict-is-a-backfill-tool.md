# ADR-013 — The inferential verdict is a backfill tool, not a measurement path

- **Status:** accepted
- **Date:** 2026-09-14
- **Related:** [ADR-008](008-verdict-coverage-gates-tokens-per-outcome.md) verdict-coverage-gates-tokens-per-outcome, [ADR-011](011-claude-code-is-the-only-permitted-llm.md)

## Context

The verdict ladder has five rungs: `declared`, `structured`, `lexical`,
`inferential`, `unstated`. The fourth is an LLM judge, and it needs justifying
against a real objection.

**The objection.** An LLM reading a closed issue's title, description and
commits cannot know whether the outcome was *accepted*. Acceptance is a fact
about what happened afterwards — was the change reverted, did it need three
follow-up fixes, did the user quietly redo it by hand. A judge given only the
issue text will nonetheless return a confident-looking verdict, which is
precisely the failure the "unknown is never zero" rule exists to prevent. An
`inferential` verdict is *weaker* evidence than a `lexical` one, not stronger,
despite costing more.

**The case for it anyway.** Measured on this machine: 10 of 12 closed issues
have `close_reason` equal to the literal string `"Closed"`, bd's default.
Verdict coverage is therefore around 17%, well under the 0.6 gate, so M1 —
the priority-1 measure — returns `verdict_coverage_too_low` and keeps returning
it until roughly forty historical verdicts are declared by hand. Nobody
annotates forty closed issues retrospectively. A judge reading the issue *plus
its commit diffs* is a plausible way to bootstrap history that would otherwise
stay permanently unmeasurable.

## Decision

Keep the rung, and constrain it so it cannot be mistaken for measurement.

1. **Ranked below `lexical`.** A stated reason, however terse, beats a guess.
2. **Backfill only.** Its purpose is historical issues closed before the
   `accepted:` habit existed. It is not part of the ongoing path.
3. **Interactive only.** Never from a hook, never from `fitness.py`, never
   unattended. The sole entry point is `/hfit:outcome --infer`.
4. **Off by default and consent-governed.** `inference.enabled` is `false` in
   `DEFAULTS` and is one of the five keys in `_GOVERNING_KEYS`, so enabling it
   re-asks for consent rather than quietly taking effect. An LLM call must never
   happen because someone installed a plugin.
5. **Excluded from the headline** unless the caller opts in for that run, and
   always reported with its basis attached so a reader can discount it.
6. **Claude Code only** — see ADR-011.

## Consequences

The fastest route to usable verdict coverage remains a human habit — the
`accepted:` prefix on `bd close --reason`, or the two-second `/hfit:outcome`
interaction. That is stated on every report, per W2, and this ADR does not
pretend inference changes it.

What inference buys is a one-off migration, clearly labelled as the weakest
basis on the ladder. What the constraints buy is that a backfill convenience
can never quietly become the way the priority-1 measure gets its denominator.

Deferred to step 8 (1.0.0), last in the build order — which is also the honest
ordering, since steps 3 through 7 may raise coverage enough through habit that
the backfill is never needed.
