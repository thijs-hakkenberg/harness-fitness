# harness-fitness

Measures what your Claude Code harness is made of, and how well it performs.

> **Status: 0.1.0.** The composition inventory ships: what your harness is made
> of, a digest identifying it, and a log of every change to it — readable with
> `/hfit:composition`. **The four measures are not implemented yet**, and the
> report says so rather than estimating them: `measures` is `null` beside a gap
> explaining why. Episodes and outcomes arrive in 0.2.0, tokens-per-outcome in
> 0.3.0. Sections below marked *(not yet)* fill in as each release ships.

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
- Python 3.9 or newer — stdlib only, no packages to install
- [`bd`](https://github.com/steveyegge/beads) *(from 0.2.0)* — the episode boundary
- [abacus](https://github.com/thijs-hakkenberg/abacus) *(optional, from 0.3.0)* —
  token and commit attribution per episode
- An OTEL log exporter *(optional, from 0.4.0)* — the autonomy and feedforward
  measures

At 0.1.0 nothing beyond Claude Code and Python is needed: the composition
inventory is read from your settings files and plugin directories.

## Install

The repository is its own single-plugin marketplace, so adding it is the whole
install story:

```
/plugin marketplace add thijs-hakkenberg/harness-fitness
/plugin install harness-fitness@harness-fitness
```

Restart Claude Code so the `SessionStart` hook is registered.

### Then acknowledge — nothing is recorded until you do

The plugin is checked out at `~/.claude/plugins/marketplaces/harness-fitness/`, so:

```bash
python3 ~/.claude/plugins/marketplaces/harness-fitness/hooks/scripts/acknowledge.py --show
```

That prints where files would go, what is never written, and confirms that
nothing has been. Read it, then:

```bash
python3 ~/.claude/plugins/marketplaces/harness-fitness/hooks/scripts/acknowledge.py --accept
```

Recording starts at your next session in a project. Withdraw at any time with
`--revoke`; it stops future writes and deletes nothing you already have.

Your acknowledgement is fingerprinted over the settings that govern *what* gets
recorded, so changing one of those stops recording until you accept again.
Changing a threshold or a display option does not re-ask.

### Then read it

Open a session in a project and run:

```
/hfit:composition
```

Which reports the digest identifying your harness, the eight-bucket
feedforward/feedback profile, and every recorded change to it. Or read the JSON
directly:

```bash
python3 ~/.claude/plugins/marketplaces/harness-fitness/hooks/scripts/fitness.py --json
```

Check `ok` before anything else. A refused read carries **no** result keys at all
— that absence is deliberate, because `null` is the legitimate value of a
*successful* read at this version.

### What gets written, and where

```
~/.claude/harness-fitness/
  acknowledged.json                    # your consent, and what it was scoped to
  projects/<project-slug>/
    compositions/<digest12>.json       # one per composition ever seen; immutable
    changes.jsonl                      # one line per moment the digest moved
```

Plain files, mode 0600 in a 0700 directory. Read them, diff them, delete them.
`ledger.in_repo: true` in `~/.claude/harness-fitness/config.json` relocates the
per-project files to `$CLAUDE_PROJECT_DIR/.harness-fitness/` if you want committed
history; `$HFIT_STATE_DIR` overrides the root entirely.

### Development install

To hack on it, register your working copy instead — a `directory` marketplace is
not copied anywhere, so edits are live:

```
/plugin marketplace add /path/to/Plugin.HarnessFitness
```

The scripts then run from your checkout rather than from
`~/.claude/plugins/marketplaces/`, so substitute the repo path in the commands
above.

## What runs

At 0.1.0 there is exactly **one** hook:

| event | script | budget | what it does |
|---|---|---|---|
| `SessionStart` | `snapshot_composition.py` | 15s | reads your settings layers and enabled plugin directories, computes the digest, writes the composition file if it is new, and appends to `changes.jsonl` if the digest moved |

It **emits no `additionalContext`** — a `SessionStart` response can inject text
into every session's context window, and a tool whose job is to measure what the
harness costs must not become a line item in that cost. Everything it learns goes
to a file, read on demand.

It also **fails open**: a corrupt payload, an unreadable ledger or a read-only
filesystem exits 0 with empty stdout. A measurement tool that can break your
session is not worth its measurements.

There is deliberately **no `PreToolUse` hook**, and no `SubagentStop`,
`Notification` or `PermissionRequest` hook. Each one this plugin adds enlarges the
harness it is trying to measure.

## Configuration

`~/.claude/harness-fitness/config.json`, all keys optional:

| key | default | effect |
|---|---|---|
| `ledger.in_repo` | `false` | write the per-project ledger to `$CLAUDE_PROJECT_DIR/.harness-fitness/` instead |
| `otel.enabled` | `true` | read the OTEL log at all *(from 0.4.0)* |
| `otel.tail_bytes` | `4194304` | how much of the log tail to scan |
| `beads.write_metadata` | `true` | write `hfit_*` keys onto closed beads issues *(from 0.2.0)* |
| `inference.enabled` | `false` | permit the opt-in inferential verdict *(from 1.0.0)* |
| `events_retention_days` | `30` | age-prune distilled events |
| `min_verdict_coverage` | `0.6` | below this, tokens-per-outcome is refused rather than reported |
| `min_episodes_per_digest` | `5` | below this, a comparison returns `insufficient_n` |
| `mad_min_n` / `mad_threshold` | `5` / `3.0` | outlier rule; `mad_min_n` tracks `min_episodes_per_digest` on purpose |
| `ff_hook_events` | see [ADR-005](adr/005-a-pretooluse-block-is-a-feedforward-catch.md) | which hook events count as feedforward catches |

The first five decide *what is collected and where it goes*, so changing one of
them re-asks for consent. The rest are thresholds and do not.

`$HFIT_STATE_DIR` overrides the state root entirely. `$HFIT_NOW` freezes the
clock, and `$HFIT_DEBUG=1` makes the hooks re-raise instead of failing open —
both exist for the test suite and are useful when something looks wrong.

## Known limitations

Stated up front, because a measurement tool that hides these invites exactly the
misuse it is built to prevent:

- **It is a detector, not a confirmer.** Task difficulty is uncontrolled. A delta
  between two compositions is worth investigating; it is not a controlled result,
  and quoting it as one is a misuse of the number.
- **Small N.** Real projects produce few episodes per composition, and a
  comparison below `min_episodes_per_digest` is refused rather than reported.
- **The four measures are not implemented at 0.1.0.** The report says so; it does
  not estimate them.
- **OTEL anonymises local plugins** — 329 of 428 observed `plugin_loaded` events
  report `plugin.name: "third-party"`. Disk is therefore the identity authority,
  and measurement happens at *composition* granularity. Per-component blame is out
  of reach, and this plugin does not promise it.
- **Verdict coverage will depend on a human habit** *(from 0.2.0)* — closing an
  issue with `--reason "accepted: …"`. Most issues today close with bd's default
  `"Closed"`, which reads as no verdict stated, not as success.

## Development

```bash
python3 -m pip install pytest pytest-bdd coverage pyyaml
python3 -m pytest
```

Those four are the whole test-time dependency set; the plugin itself imports only
the Python 3.9 standard library. Without `pyyaml` the suite still runs — the
`spec.manifest.yaml` checks skip and everything else reports.

See [CONTRIBUTING.md](CONTRIBUTING.md). Two rules: tests before implementation,
and never write outside the `hfit_*` namespace.

## Licence

MIT — see [LICENSE](LICENSE).
