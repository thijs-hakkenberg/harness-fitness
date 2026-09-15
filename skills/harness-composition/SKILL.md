---
name: harness-composition
description: Reports what this project's Claude Code harness is composed of - the enabled plugins, hooks, skills, agents and MCP servers, their feedforward/feedback profile, and every recorded change to that composition
triggers:
  - /hfit:composition
---

## What This Skill Does

Answers one question: **what is the harness in this project made of right now, and when did it last change?**

It reports the recorded composition — a `digest` identifying the enabled harness, a `profile`
counting components by control type, and the change log of every moment that digest moved. It
also reports how much *evidence* exists to judge that harness by: how many episodes have been
reconciled, and what share of them state an outcome.

It does **not** report the four fitness measures. Those arrive in later versions, and this
skill's job is to say so rather than to estimate them.

You are reading a measurement tool. Every number below comes from the JSON; **never** supply
one that is not there.

## Execution

**1. Read the report.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/fitness.py" --json
```

Stdout is one JSON object. Parse it. Nothing else is written and nothing is sent anywhere.

**2. Check `ok` before you look at anything else.**

This is the first branch, always. A refused read carries **no** `current`, `changes`,
`episodes_seen`, `verdict_coverage`, `measures` or `flags` key at all — so reaching for one of those first turns
"you have not consented yet" into "nothing has been recorded", which sends the reader hunting
for a bug instead of making a decision.

If `ok` is `false`, report the `reason` from the single entry in `gaps`, verbatim or close to
it, and stop. Do not describe a composition, do not guess at a profile, and do not say the
plugin is broken — it is waiting.

**3. If `ok` is true, report `current`.**

- `digest` — the identity of this harness. Quote it as-is; it is 12 hex characters and is the
  key everything else groups by.
- `profile` — eight buckets, always all eight. `ff_inf` and `ff_comp` are **guides**: controls
  that steer before the work happens. `fb_comp` and `fb_inf` are **sensors**: controls that
  catch after. `both_inf`, `substrate`, `packaging` and `unknown` are the rest. Say what the
  numbers are; a sentence like "three sensors and one guide" is the useful form.
- `env_hash` and `model_basis` — report them **together or not at all**. A `model_basis` of
  `declared` means the model was read from settings, not observed running, so it is never
  presented as the model that ran. Two harnesses are comparable only if both halves agree.
- `captured_at` — when this composition was **first** seen, not when it was last confirmed.

If `current` is `null`, say so plainly and use the gap reason to explain why.

**4. Report `changes`, oldest first.**

Each entry is a moment the harness moved. `kind` is `baseline` for the first sighting and
`transition` afterwards. `added`, `removed` and `changed` name components as `<type>:<key>`;
`profile_delta` carries **only the buckets that moved**.

A `baseline` entry has empty diffs on purpose — a first sighting is not a change. Do not
narrate it as if forty components had just been installed.

If `diff_basis` is `unavailable`, every diff field is `null`: the digest moved but the previous
record is gone, so what changed is unknown. Say "unknown", never "nothing".

**5. Report the evidence: `episodes_seen` and `verdict_coverage`.**

An episode is one closed beads issue. `episodes_seen` counts the ones reconciled in *this*
project, and it has three meanings you must keep apart:

- a **number** — that many episodes are on record. `0` is a real and unremarkable answer: it
  means nothing has been reconciled here yet, which is what a fresh project looks like.
- **`null`** — the ledger could not be read. This is a fault on the machine, not a fact about
  the user's work, and the `episodes-unreadable` gap says so. Never round it to `0`.

`verdict_coverage` is the share of those episodes whose outcome is stated, and it is the number
to lead with once there are episodes at all, because every later measure depends on it:

- `coverage` — a fraction between 0 and 1, or `null`. Report it as a percentage.
- `n` and `stated` — the denominator and the numerator. Quote them beside the share; "3 of 8"
  is more use than "38%" on its own.
- `by_basis` — how each stated outcome was established: `declared` (a human said so through
  `/hfit:outcome`), `structured` (an `accepted:`-style prefix on `bd close --reason`),
  `lexical` (a computational guess from free text), `inferential` (an LLM backfill),
  `unstated` (nothing said). The mix matters: coverage built from `structured` prefixes is
  worth trusting, the same coverage built from `lexical` guesses is not, and only this field
  can tell them apart.
- `reason` — `no_episodes` when there was nothing to measure. `null` when the share is real.

When `coverage` is `null` and `n` is `0`, say there is nothing to measure yet. Do **not** say
coverage is 0% — that describes a project with a bad habit rather than one with no history.

When the whole `verdict_coverage` value is `null`, the ledger was unreadable. Say that.

**6. Read every gap and pass it on.**

`gaps` is a list of `{kind, reason}`. The `kind` is for you to branch on; the `reason` is a
sentence written for the user. Printing the `reason` is always the correct fallback, including
for a `kind` you do not recognise — later versions add kinds, and inventing an explanation for
an unfamiliar one is the single worst thing this skill can do.

| `kind` | what to say |
|---|---|
| `not-acknowledged` | Nothing has been recorded and nothing ever will be until the user consents. Give them the command in the `reason`. This is the whole first-run experience — get it right. |
| `config-changed` | A governing setting moved after they consented, so recording stopped on purpose. They need to re-acknowledge. Not a fault. |
| `no-composition-recorded` | Consent is in place, but no session has started in this project yet. Tell them to open a session here; it fills in by itself. |
| `unresolved-composition` | The digest is known from the change log but its record file could not be read. Report the digest and treat the profile and the pin as unavailable. |
| `no-episodes-recorded` | Not a fault. Nothing has been closed in this project since the plugin was acknowledged, so `episodes_seen` is `0` and there is nothing to measure. Give them the `bd close` example from the `reason`. |
| `episodes-unreadable` | A real fault on this machine: the ledger file exists but yielded nothing. `episodes_seen` and `verdict_coverage` are both `null`, and neither may be reported as `0`. Pass on the `reason`, which says what to check. |
| `unstated-verdict` | Most closed issues here do not state whether they succeeded. Report the percentage from the `reason` and the remedy in it. Say plainly that tokens per outcome stays unavailable until it rises — this is a habit worth changing, not a defect to work around. |
| `measures-unavailable` | Expected at this version. Say the measures are not implemented yet. Do **not** estimate one. |

**7. Report `flags` as-is.**

Conditions affecting the whole read. An empty list is the normal case and needs no comment.

## What Not To Do

- **Never fill in a `null`.** `measures` is `null` at this version because the layer that
  produces it does not exist; `episodes_seen` and `verdict_coverage` are `null` when the ledger
  could not be read. A `null` paired with a gap is the honest answer; a plausible number in its
  place is a fabrication the reader cannot detect. This is the one rule that matters more than
  being helpful. Do not invent, estimate or guess a figure the JSON does not carry.
- **Never report `0` where the JSON says `null`, and never `null` where it says `0`.** Unknown
  is not zero, and the two directions mislead differently. A `coverage` of `0.0` invented over
  no episodes accuses the user of a habit they have not had a chance to form; an
  `episodes_seen` of `null` reported as `0` hides a broken file behind a plausible count.
- **Never treat a missing key as empty.** On a refused read the keys are absent, not blank.
- **Never paraphrase a refusal into a softer one.** If the report declines to answer, the
  reader needs to know it declined, not that the answer happened to be small.
- **Do not read the ledger files directly.** The CLI is the contract; the file layout is free
  to change behind it.
