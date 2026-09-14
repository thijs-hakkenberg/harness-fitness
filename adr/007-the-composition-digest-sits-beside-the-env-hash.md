# ADR-007 — The composition digest sits beside the environment hash, never inside it

- **Status:** accepted
- **Date:** 2026-09-14
- **Governs:** `hooks/lib/composition.py`, `hooks/lib/env_pin.py`, and every comparison the report layer performs

## Context

Episodes are grouped by `(composition_digest, env_hash)`. Two facts have to be
kept apart inside that pair, and the temptation is to merge them because both are
"the configuration":

- **`composition_digest` says *what* is being compared** — which plugins, hooks,
  skills, agents and MCP servers were enabled.
- **`env_hash` says whether the comparison is *legitimate*** — the same five
  fields the rig pins: `model`, `effort`, `anthropic_base_url`, `cache_policy`,
  `max_retries`.

Folded into one identity, a model swap would present as a harness change. Someone
moves from Sonnet to Opus in the same week they add a linting hook, tokens per
outcome halves, and the report attributes the win to the hook. That is not a
rounding error in the measure; it is the measure answering a different question
than the one asked, with no signal that it has done so.

Kept separate, the pair supports a third option beyond "report a number" and
"report a wrong number": **refuse**.

Three sub-decisions follow, and the second was forced by measurement rather than
chosen from taste.

## Decision

### 1. The two identities are computed separately and carried together

`composition.build()` accepts an `env_hash` and stores it without folding it into
`digest(canonical(inv))`. It stays `null` when absent rather than defaulting,
because a consumer must be able to refuse on a *missing* pin, and only `null` is
unambiguous about that.

`env_pin.build()` computes the environment side and never opens the composition
side. `composition` reciprocally never reads the settings `env` block at all —
not filtered, never opened — so its no-leak property holds by construction rather
than by a filter list someone has to keep correct forever (see ADR-010 for the
same discipline applied to command strings).

**Comparing two compositions across a differing `env_hash` is refused, not
reported:** `{"comparable": false, "reason": "env_hash differs"}`.

### 2. The two hashes are different widths, and that is a constraint, not an inconsistency

`composition_digest` is **12 hex**. `env_hash` is **64 hex**.

- Twelve is a **storage contract**: it is the filename in
  `compositions/<digest12>.json`, which is content-addressed and never mutated so
  that an old digest stays resolvable. Widening it orphans every composition file
  already written.
- Sixty-four is **imposed from outside**: `rig/schemas/result.schema.json` types
  `env_hash` as `^[0-9a-f]{64}$`, so a truncated one fails `--rig-handoff`
  validation at step 8 — after every episode recorded up to that point has been
  pinned with the wrong width.

The asymmetry reads like an oversight, which is exactly why it is written down.
Neither width may be changed to "match" the other.

### 3. The model is not declarable, so a resolved model states its basis

This was measured on the machine this plugin was written on, and the obvious
precedence rule turned out to be wrong:

| source | value |
|---|---|
| `~/.claude/settings.json` → `model` | `"opus"` |
| `~/.claude/settings.json` → `env.ANTHROPIC_MODEL` | `"claude-fable-5-innovation"` |
| observed OTEL `api_request` events | `"claude-opus-5"` — **all 389 of them** |

No precedence rule over the two *declared* fields produces the observed answer.
The settings alias won, the environment variable was inert, and the resolved id
appears in neither file. The convention that an environment variable overrides a
settings file would have picked the one value that never ran.

So:

- An **observation supersedes every declaration**. Between declarations, the
  top-level `model` setting wins over `env.ANTHROPIC_MODEL` — the opposite of the
  usual convention, adopted because the usual convention was tested and found
  wrong here.
- A disk-resolved model is labelled **`declared`** and is never presented as the
  model that ran. `model_basis ∈ observed | declared | unknown` travels with
  every record, and both declarations are kept even when one is superseded, so a
  reader can see *which file misled them* rather than only that something did.
- An unresolvable model is `null` with `model_basis: "unknown"`. Not a default:
  a fabricated model id would let two genuinely unlike environments hash alike,
  which is the one thing this hash exists to prevent.

**`model_basis` is deliberately *not* in the hash, and that leaves an obligation
on the comparison layer.** Folding it in is tempting — it would make it
structurally impossible to compare a declared pin against an observed one. It is
refused because every user who gains an OTEL log would then re-hash an unchanged
environment, splitting one comparable population into two that each fall below
`min_episodes_per_digest` and returning `insufficient_n` for a reason no reader
could trace to anything they did. That is the same spurious-split failure
`composition.canonical` is written to avoid, arriving by a different route.

The protection therefore lives where the refusal machinery already exists:
**the comparison layer must refuse on a differing `model_basis` exactly as it
refuses on a differing `env_hash`.** This ADR is the record of that obligation,
because the code cannot enforce it locally. It is the same pattern as ADR-005's
`ff_hook_events`: a field that governs a number must travel with it.

## Consequences

**The `env` block is opened in exactly one module.** `env_pin` reads it, takes
three keys by name — `ANTHROPIC_BASE_URL`, `ENABLE_PROMPT_CACHING_1H`,
`CLAUDE_CODE_MAX_RETRIES` — plus `ANTHROPIC_MODEL`, and nothing else. This
matters because `~/.claude/settings.json` is where a live credential sits, and a
composition record is a file users are invited to read and attach to bug reports.

**A base URL is itself a credential carrier, so it is sanitised before hashing.**
A URL can hold userinfo (`https://user:pass@host`) or a query credential
(`?api_key=…`). Both are stripped, and stripped *before* the hash rather than
after, so rotating an embedded token does not move `env_hash` and refuse
comparison against yesterday's episodes. The endpoint decides comparability; the
credential does not.

**Absence is reported three different ways, on purpose.** Collapsing them would
each time state something false:

| field | absent means | recorded as |
|---|---|---|
| `cache_policy` | the default cache policy is in force — determinate | `"default"` |
| `max_retries` | a built-in default not knowable from disk; rig schema types it `integer\|null` | `None` |
| `claude_code_version`, `agent_sdk_version` | not observed; rig schema types these non-nullable strings | `"unknown"` |

`null` for `cache_policy` would claim we could not tell, which is the one thing
that is not true. A named default for `max_retries` would let a machine that
really uses 2 hash identically to one whose default is something else. A `null`
version would fail handoff validation, so the sentinel string is a stated absence
that still validates.

**The local interpreter is reported but not hashed.** `platform` and
`python_version` ride in the env block for the handoff and stay out of the hash:
the Python running a measurement hook is not part of the environment being
measured, and a `brew upgrade` must not split an episode population. Substrate
versions (`claude_code_version`, `agent_sdk_version`) *are* hashed, because a
Claude Code upgrade changes what a fixed configuration does — the rig folds its
probed versions in for the same reason.

**Neither hash covers its own schema version.** `composition.SCHEMA` and
`env_pin.SCHEMA` track record shape and are excluded, so a schema bump cannot
re-digest an unchanged harness. `env_pin`'s `_HASH_BASIS` constant *is* hashed,
because it moves only when the hashed field set changes — at which point the hash
genuinely means something different and should move — and it keeps a hash
produced here from colliding with one the rig computed over its own payload.
