# ADR-011 — Claude Code is the only permitted LLM; there is no bring-your-own-LLM

- **Status:** accepted
- **Date:** 2026-09-14
- **Related:** [ADR-006](.) hot-path-is-computational-only, [ADR-013](013-the-inferential-verdict-is-a-backfill-tool.md), [ADR-014](.) consent-gates-every-write

## Context

This plugin has exactly one place where a language model is involved: the
`inferential` rung of the verdict ladder, reached only from
`/hfit:outcome --infer`, which dispatches the `agents/harness-judge.md`
subagent. Everything else that sounds inferential is not — `taxonomy.py`
*labels* other components as FF·INF or FB·INF, which is deterministic
classification; the soft-touch classifier is stamped `lexical-v1`; failure-mode
promotion is human.

A Claude Code subagent is a markdown file with frontmatter. Claude Code
dispatches it using the session's own model and credentials. The plugin
therefore needs no API client, no SDK dependency, no endpoint and no key in
order to have its one inference capability.

The question this ADR settles is whether a *fallback* provider is needed for the
cases where Claude Code cannot dispatch a subagent — `fitness.py --json` run
from CI or a plain terminal, or a deployment where subagents are unavailable.

## Decision

**Claude Code is the only LLM this plugin will ever use.** No HTTP client, no
provider SDK, no endpoint configuration, no API key read or stored — not as a
fallback, not behind a flag, not opt-in.

When Claude Code cannot dispatch a subagent, inference is simply unavailable:
the verdict stays `unstated` with a reason, exactly as it does when a
`close_reason` is missing. Refusal, not substitution.

`hooks/lib/verdict.py` may *record* an `inferential` basis; it may never
*produce* one. The only producer is an interactive skill invocation.

Enforced by `tests/unit/test_no_inference_dependency.py`, which parses every
module under `hooks/` with `ast` and fails on an import of a network or
model-provider surface. A prose constraint that no test asserts is a constraint
that survives exactly until the first contributor who has not read this file.

## Consequences

The README's central promise — *"nothing leaves the machine; there is no
upload, no endpoint, no telemetry of its own"* — stays literally true and
mechanically checkable. That sentence is what makes a measurement tool
adoptable rather than creepy, and it is worth more than any feature a provider
integration would enable.

A bring-your-own-LLM path would have cost three things: it sends issue titles
and commit diffs to a third party, breaking that promise; it adds key storage
to a plugin whose entire security posture is that it *hashes* secrets rather
than storing them (see ADR-010); and it buys, in exchange, a convenience for
backfilling historical verdicts. The trade is not close.

The cost of this decision is real but small: `fitness.py` run in CI can never
produce an inferential verdict. That is the correct behaviour anyway — an
unattended process should not be spending tokens to guess at outcomes.
