# ADR-017 — Mutation testing does not substitute for a red phase

- **Status:** accepted
- **Date:** 2026-09-15

## Context

`CONTRIBUTING.md` opens by naming two rules as not negotiable, the first being that
every change lands as red → green → refactor. During step 3 of the build order — the
episode layer — that rule was broken once, deliberately, and the reasoning that
licensed it is worth recording because it is reusable and it is wrong.

**What happened.** `hooks/scripts/reconcile_hook.py` was written before its thirty
integration tests. Rather than delete the script and start over, a mutation pass was
run against it: guards inverted, comparisons flipped, filters removed, each mutant
checked to see whether the new tests caught it. Most did. Three survived, and each
survivor was a real repair — one guard was only reachable through a public entry
point the tests were not using, one was exercised solely through values already
constrained upstream, and one had no test supplying a realistic input shape. The
suite ended green with every guard demonstrably exercised, and the mutation pass was
treated as having discharged the missing red phase.

Three tests written *later* in the same step were genuinely mutation-driven: a
survivor, then a failing test, then the fix. Those are red phases and are not what
this record is about. A smaller instance of the same slippage:
`contracts/output/episode-record.md` was cited by two input contracts before the file
existed.

**Why the substitution looked sound.** Set out as an argument, so the step that fails
is visible rather than felt:

- *Claim*: a mutation pass discharges a missing red phase.
- *Grounds*: a killed mutant proves the test exercises the guard it targets, and
  proves it more thoroughly than one observed failure does.
- *Warrant*: the value of a red phase is proof that the test is capable of failing.

The grounds are true. The warrant is the load-bearing step, and it is false.

**Why it is false.** A red phase proves two things, not one. It proves the test *can*
fail — which a mutation pass does prove, and better. It also proves the test asserts
the **intended** behaviour rather than the **implemented** behaviour, and a mutation
pass cannot prove that at all: a test written from the specification and a test
written from the code under test kill exactly the same mutants. Mutation testing
measures a test against an implementation. A red phase measures it against an
intention. Once the implementation exists, only the first is still available, and the
second is the one the rule exists to protect.

The consequence is not hypothetical. A test written from the code ratifies whatever
the code does, including the parts that are wrong, and it does so while reporting
full coverage and a perfect mutation score. Nothing downstream distinguishes it from
a test that was written first.

## Decision

**A mutation pass does not discharge a missing red phase, and may not be offered as
though it does.** Where implementation has landed ahead of its tests, the remedy is
to write the tests from the contract or the specification rather than from the code,
and to say in the pull request that this is what happened. Where no contract exists
to write from, one is written first — which is the cheaper half of the work anyway.

`CONTRIBUTING.md` §1 is **left unchanged**. Its operative sentence governs incoming
pull requests and was not falsified by this lapse; the sentence that was violated is
the present-tense description of practice beside it. Softening either would trade a
true rule for a hedged one in order to make a past failure read better. The rule
stands, and this record stands beside it.

Mutation testing keeps every use it already had, and they are not small ones. It is
how this repo establishes that a guard exists rather than inferring it from a green
suite; it is how an equivalent mutant gets separated from a genuine test gap, the
first being a claim to document and the second a test to write; and it remains the
right first move whenever a test's strength is in doubt. What it stops being is a way
to claim a red phase that did not happen.

**This binds hardest on step 4.** M1's only oracle is the parity suite against the
rig's own expected vectors, and arithmetic is precisely where a test written from the
implementation is invisible — it passes, it kills mutants, and it silently ratifies
whichever formula got typed. So the parity suite is written from the rig's vectors
before `measures.py` exists, and nothing else in that step is implemented until it is
red for the right reason. That ordering was already in the build order for a
different reason ([ADR-008](008-verdict-coverage-gates-tokens-per-outcome.md) makes
the gate measurable before the measure it gates); this makes it a rule rather than a
preference.

## Consequences

The repo's own ADR set now contains an admission of a process failure, findable by
anyone evaluating whether the two rules in `CONTRIBUTING.md` are real. That is the
intended cost and not a reluctant one. A measurement tool whose README carries a
limitations section, and whose report returns `null` with a reason rather than
estimating what it cannot compute, is not made more credible by an unblemished
process record — it is made more credible by one that says where the process slipped
and what was concluded from it.

The thirty tests in question are **not** rewritten. They were driven against a real
subprocess, three genuine gaps in them were found and closed, and rewriting them from
the contract now would produce substantially the same assertions with a worse audit
trail: the implementation is already in the author's head and cannot be got back out
of it. What is owed instead is that the next comparable situation is handled by the
rule above, and step 4 is where that is first tested rather than asserted.

A reviewer now has something specific to ask for. Any pull request whose tests
postdate their implementation has to say so, which makes the ordering a declared fact
about a change rather than an assumption about its author. Silence on the point is no
longer neutral.
