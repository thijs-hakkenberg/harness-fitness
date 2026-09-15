---
name: harness-outcome
description: Records the outcome a human declares for one closed beads issue - accepted, rejected, abandoned or superseded - as the top rung of the verdict ladder, so tokens per outcome has a denominator that was stated rather than guessed
triggers:
  - /hfit:outcome
---

## What This Skill Does

Writes down **the user's own verdict** on one closed beads issue.

Every other basis in this system is derived. `structured` reads an `accepted:`-style prefix off
`bd close --reason`. `lexical` guesses from free text. `inferential` is an LLM backfill and is
ranked *below* the guess on purpose. `declared` — the basis this skill produces — is the only one
that means a person said so.

Which makes the most important property of this skill a **negative** one:

> **The verdict comes from the user. It is never your judgement of how the work went.**

If you choose `accepted` because the code looked finished, the tests were green, or the session
ended tidily, you have turned the most trustworthy basis this plugin can print into an inference
wearing a declaration's badge — and nothing downstream can tell the two apart. The whole verdict
ladder exists so that a reader can discount a weak basis. A fabricated `declared` is undiscountable.

So: **if the user has not told you which of the four verdicts applies, ask them.** One short
question. Do not infer it, do not default to `accepted`, and do not proceed on the strength of
what you observed.

## Execution

**1. Establish the issue id and the verdict — from the user.**

The issue id is whichever closed beads issue they are talking about. If it is ambiguous, list the
recent closures with `bd list --all --json` and ask which one. The verdict is one of exactly four
values, and the user picks it:

| verdict | means |
|---|---|
| `accepted` | the work was done and it was what was wanted |
| `rejected` | the work was done and it was not what was wanted |
| `abandoned` | the work stopped and will not be finished |
| `superseded` | the work was overtaken by a different approach that replaced it |

`partial` and `unstated` are **not** declarable and the command refuses them at the boundary.
`partial` is derived from abacus's `abacus_partial` flag — a hand-set one would claim an
interrupted session that abacus recorded as clean, which is the one direction nothing can check.
`unstated` says nothing that a missing key does not already say.

**2. Write it.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/outcome.py" --issue ISSUE_ID --verdict accepted --json
```

Replace `ISSUE_ID` with the issue and `accepted` with the verdict the user chose. Stdout is one
JSON object. Nothing is sent anywhere; the write is a local ledger append plus a `bd update` on
that one issue.

**3. Check `ok` before you look at anything else.**

This is the first branch, always. A refused write carries **no** `keys_written`, `recorded` or
`reconcile_reason` key at all — so reaching for one of those first turns "you have not consented
yet" into "nothing was recorded", which sends the reader hunting for a bug instead of making a
decision. A missing key means *not attempted*, not *zero*.

If `ok` is `false`, print the `note` — it is a sentence written for the user and it names the
remedy — and stop. Do not retry, do not describe the write as partially done, and do not say the
plugin is broken.

| `reason` | what happened |
|---|---|
| `not_acknowledged` | consent has never been given. The `note` carries the `acknowledge.py --accept` command. This is the whole first-run experience — get it right. |
| `config_changed` | a governing setting moved after they consented, so writing stopped on purpose. They re-acknowledge. Not a fault. |
| `writes_disabled` | `beads.write_metadata` is off for this project, so the cross-machine index is switched off by choice. Say so plainly; the ledger is still the store of record. |
| `no_database` | there is no beads database here, so there is no issue to write to. |
| `bd_unavailable` | `bd` is not on the PATH. |
| `bd_error` | `bd` refused the update — most often an issue id that does not exist. Suggest `bd show` on the id. |
| `timeout` | `bd` did not answer in time. Nothing was half-applied: the keys travel in one invocation. Retrying is safe. |

A `reason` you do not recognise still carries a `note`. Print it verbatim. Later versions add
reasons, and writing a plausible sentence about an unfamiliar one is the single worst thing this
skill can do.

**4. If `ok` is true, report both halves — they are separate facts.**

- `ok: true` means the declaration reached beads. `basis` is `declared` and `keys_written` is how
  many metadata keys were set.
- `recorded` says whether the **episode ledger** has read it back yet, and `reconcile_reason`
  says why not when it has not.

`recorded: false` beside a successful write is **not a failure**. The declaration is stored; the
ledger reading lagged behind because `bd list` could not be run at that instant. The next `Stop`
hook finishes it. Report it as "stored, ledger catches up shortly" — never as an error, and never
by telling the user to declare it again. Sending them to redo work that is already done is worse
than saying nothing.

**5. Say what it unlocks, once.**

A declared verdict is what gives `tokens_per_outcome` a denominator. If the user has just crossed
a threshold's worth of episodes, `/hfit:composition` will show `verdict_coverage` moving. One
sentence is enough; this is not a place for a lecture on the measures.

## What Not To Do

- **Never decide the verdict yourself.** Not from green tests, not from a clean diff, not from the
  user sounding pleased. Ask. This is the one rule that matters more than being helpful, because
  a `declared` basis nobody declared is worse than no verdict at all — an `unstated` episode is
  honestly excluded from every measure, while an invented `accepted` silently corrupts all of them.
- **Never fill in a `null` or a missing key.** If `reconcile_reason` is `null` on a refusal it is
  absent, not blank. Do not invent, estimate or guess a value the JSON does not carry — including
  a `keys_written` count for a write that never ran.
- **Never report `0` where the JSON says a key is absent.** `keys_written: 0` would claim the
  write ran and wrote nothing. Nothing ran.
- **Never call `bd update --set-metadata` yourself.** The CLI validates the key namespace, the
  vocabulary and the argument boundary; a hand-rolled `bd` call bypasses all three and can write a
  key that becomes authoritative on the next machine to sync it.
- **Never declare a verdict on an issue that is still open.** An episode's boundary is
  `closed_at`. A verdict on open work is a prediction.
- **Do not read or edit the ledger files directly.** The CLI is the contract; the file layout is
  free to change behind it.
