# ADR-008 — Verdict coverage is a headline number, and below threshold it refuses tokens per outcome

- **Status:** accepted
- **Date:** 2026-09-14
- **Governs:** `hooks/lib/verdict.py`, `hooks/lib/measures.py`, and the `outcome` sentinel in every report carrying M1
- **Related:** [ADR-013](013-the-inferential-verdict-is-a-backfill-tool.md) the-inferential-verdict-is-a-backfill-tool

## Context

Tokens per outcome is the measure the user asked for first, and its denominator is
a count of episodes whose verdict is `accepted`:

```
tokens_per_outcome = Σ tokens over SURVIVING ÷ |PASSING|
```

The denominator is therefore only as good as the verdicts, and **the verdicts are
poor today.** Measured on this machine with `bd` 1.1.2: of thirteen closed issues,
twelve carry a `close_reason` — and ten of those are the literal string `"Closed"`,
bd's default. Two closes in thirteen state an outcome.

`"Closed"` is the dangerous case, and it is worth naming precisely why. It is not
missing data. It is a string that *looks* like data, arrives from the tool that
owns episode boundaries, and means nothing at all. Read as a verdict, it is
plausible to classify either way:

- Counted as `accepted`, ten unjudged closes join the denominator, and tokens per
  outcome falls by roughly a factor of six. A harness change measured across that
  boundary would show an improvement caused entirely by how issues were closed.
- Counted as `rejected`, the same ten leave the denominator while their tokens stay
  in the numerator, and the number rises instead. Equally wrong, opposite sign.

Either choice produces a plausible number from an unknowable input, which is the
precise failure this plugin exists not to commit. And it fails invisibly: a
denominator of two versus a denominator of twelve are both integers, and nothing in
the arithmetic marks one as unearned.

There is a further, structural point. Verdict coverage is not a defect to be fixed
once. It is a **property of a human habit** — whether someone types
`bd close --reason "accepted: …"`. It will vary between projects, between weeks,
and between people, which means it varies exactly where a comparison is drawn. It
has to be a number the reader sees, not a precondition the tool assumes.

## Decision

**`verdict_coverage` is computed, published as a headline field, and gates M1.**

```
verdict_coverage = |episodes with a verdict other than "unstated"| ÷ |episodes|
```

Below `min_verdict_coverage` (`hfit_config.DEFAULTS`, 0.6) the measure layer
returns

```json
{"outcome": "verdict_coverage_too_low", "tokens_per_outcome": null}
```

— a refusal, not a degraded estimate. `verdict_coverage_too_low` is one of the four
`outcome` sentinels beside `ok`, `capture_incomplete` and `no_passing_runs`, so a
consumer that switches on `outcome` handles it without special-casing.

**`"Closed"` and an absent reason both map to `unstated`.** They are the same
epistemic state — nobody said whether this worked — and `verdict.py` collapses them
deliberately rather than preserving a distinction that would invite one of them to
be treated as a signal.

**A verdict always carries the basis it was derived from**:
`declared | structured | lexical | inferential | unstated`. Coverage counts the
first four. A report that quotes a tokens-per-outcome figure carries the basis mix
beside it, because a coverage of 0.8 made of `structured` verdicts and one made of
`lexical` guesses are not the same evidence.

**Coverage is reported even when it passes.** The gate is not the only reason the
number exists; a reader comparing two compositions needs to know that one was
measured at 0.95 coverage and the other at 0.62.

## Consequences

**At 0.2.0, on real data, this measure will usually refuse.** That is the correct
behaviour and it should not be softened by lowering the default. A tool that
reports a confident number from two verdicts in thirteen is worse than one that
says it cannot tell — the first sends someone to change their harness on noise.

**The remedy is a habit, and the plugin's job is to make it cheap.**
`/hfit:outcome` writes `hfit_verdict` in one interaction, producing a `declared`
verdict, the highest rung on the ladder. The `accepted:` / `rejected:` /
`abandoned:` / `superseded:` prefix convention gets a `structured` verdict for
free from a close the user was doing anyway. Both are documented in the README as
the thing to do rather than buried as a tip, because W2 is a real limitation and
hiding it invites the misuse.

**`--infer` exists as a fallback and is deliberately awkward.** Backfilling
verdicts with a subagent is the one path that can raise coverage retroactively, and
[ADR-013](013-the-inferential-verdict-is-a-backfill-tool.md) confines it: opt-in,
never from a hook, `inference.enabled` false by default, re-asking for consent when
turned on, and stamped `inferential` so a coverage figure it produced is
distinguishable from one earned by a human close.

**The threshold is configurable, which means it is a comparability field.** Two
reports produced under different `min_verdict_coverage` values are not comparable
on whether M1 was refused. `min_verdict_coverage` is *not* one of
`hfit_config._GOVERNING_KEYS` — it changes what is reported, not what is written or
whether inference runs, so changing it does not re-ask for consent — and the
obligation lands on the report instead: the threshold travels with the refusal.

**A poor coverage figure is itself a finding about the harness.** A project where
nobody states outcomes has no feedback loop on whether its work succeeded, which is
a harness deficiency of exactly the kind this plugin was built to surface. The
refusal is not only a guard on the arithmetic; it is a measurement.
