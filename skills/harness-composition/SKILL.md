---
name: harness-composition
description: Reports what this project's Claude Code harness is composed of - the enabled plugins, hooks, skills, agents and MCP servers, their feedforward/feedback profile, and every recorded change to that composition
triggers:
  - /hfit:composition
---

## What This Skill Does

Answers one question: **what is the harness in this project made of right now, and when did it last change?**

It reports the recorded composition — a `digest` identifying the enabled harness, a `profile`
counting components by control type, and the change log of every moment that digest moved.

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
`episodes_seen`, `measures` or `flags` key at all — so reaching for one of those first turns
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

**5. Read every gap and pass it on.**

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
| `measures-unavailable` | Expected at this version. Say the measures are not implemented yet. Do **not** estimate one. |

**6. Report `flags` as-is.**

Conditions affecting the whole read. An empty list is the normal case and needs no comment.

## What Not To Do

- **Never fill in a `null`.** `episodes_seen` and `measures` are `null` at this version
  because the layers that produce them do not exist. A `null` paired with a gap is the honest
  answer; a plausible number in its place is a fabrication the reader cannot detect. This is
  the one rule that matters more than being helpful.
- **Never report `0` where the JSON says `null`.** Unknown is not zero. "We counted your
  closed issues and found none" is a claim about the user's work; "there is nothing to count
  yet" is the truth.
- **Never treat a missing key as empty.** On a refused read the keys are absent, not blank.
- **Never paraphrase a refusal into a softer one.** If the report declines to answer, the
  reader needs to know it declined, not that the answer happened to be small.
- **Do not read the ledger files directly.** The CLI is the contract; the file layout is free
  to change behind it.
