# harness-fitness

Measures what your Claude Code harness is made of, and how well it performs.

> **Status: pre-release (0.1.0-dev).** The composition inventory lands in 0.1.0.
> Until then this repository is a specification with a growing test suite. The
> sections below are placeholders that fill in as each release ships.

## What it is

An **Agent** is a **Model** plus a **Harness** — the hooks, guides, sensors,
skills, agents and MCP servers wrapped around the model. When you add a plugin
you change the harness, and nothing today tells you whether you made it better.

This plugin records two things per project, continuously, from real work:

1. **What the harness was composed of** — a canonicalised, hashed inventory of
   the *enabled* components, classified as feedforward guides or feedback
   sensors, computational or inferential.
2. **How well it performed** — tokens per outcome, autonomy, sensor coverage,
   and how much was caught before the fact versus after.

Change the harness, and the `composition_digest` moves. Group the measures by
digest and the before/after is readable.

## What it refuses to tell you

Deliberately, and on every report:

- It is a **detector, not a confirmer.** Task difficulty is uncontrolled, so a
  delta between two compositions is a signal worth investigating, never a
  controlled result. `--rig-handoff` exists to move a suspicious signal to a
  controlled A/B rig.
- **Unknown is never zero.** Every measure returns `null` plus a reason rather
  than `0`, `100%` or `true`. Absence of evidence of a human correction is not
  evidence of autonomy.
- Comparing two compositions under **different models or effort levels is
  refused**, not reported.

## Privacy and data

This plugin reads your settings files, enumerates your installed plugins and
inspects your hook configuration. So, precisely:

- **Hook and MCP command strings are hashed, never stored.** Only a basename and
  a digest are recorded. Those strings contain absolute paths and sometimes
  credentials.
- **Prompt text is never persisted** — only a length and a hash prefix.
- **Nothing leaves the machine.** There is no upload, no endpoint, no telemetry
  of its own. The plugin ships no HTTP client, no provider SDK and no API key,
  and a test asserts it ([ADR-011](adr/011-claude-code-is-the-only-permitted-llm.md)).
- **No LLM call happens because you installed this.** Every measure is
  computational. The one optional exception is `/hfit:outcome --infer`, which
  dispatches a Claude Code subagent using *your* session — never a provider of
  its own. It is off by default, and turning it on re-asks for your consent.
- Every artefact is a local file you can read, diff and delete.
- **Nothing is written at all** until you run `acknowledge.py --accept`.

## Requirements

- Claude Code
- [`bd`](https://github.com/steveyegge/beads) — the episode boundary
- [abacus](https://github.com/thijs-hakkenberg/abacus) *(optional)* — token and
  commit attribution per episode
- An OTEL log exporter *(optional)* — the autonomy and feedforward measures

## Install

*Documented in 0.1.0.*

## Development

```bash
python3 -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Two rules: tests before implementation,
and never write outside the `hfit_*` namespace.

## Licence

MIT — see [LICENSE](LICENSE).
