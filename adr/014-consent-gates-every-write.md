# ADR-014 — Consent gates every write, and the gate is checked before a path is resolved

- **Status:** accepted
- **Date:** 2026-09-14
- **Governs:** `hooks/lib/consent.py`, `hooks/lib/ledger.py`, `hooks/scripts/acknowledge.py`, and every script that could write
- **Related:** [ADR-010](010-hook-commands-are-hashed-never-stored.md) hook-commands-are-hashed-never-stored, [ADR-011](011-claude-code-is-the-only-permitted-llm.md) claude-code-is-the-only-permitted-llm

## Context

Installing this plugin gives it a `SessionStart` hook that reads your settings
files, enumerates your installed plugins, inspects your hook configuration and
writes what it found to disk. Every one of those artefacts is defensible in
isolation and none of them is what a user consented to by typing
`/plugin install`.

The default in this space is to start collecting and offer an opt-out. That is the
wrong default for this tool, for a reason specific to it: **the first record is the
sensitive one.** A composition record is an inventory of what someone has installed
and configured — a fingerprint of their working environment. There is no version of
"delete it afterwards" that undoes having written it, and no version of a
first-run notice that helps, because a hook runs before anyone reads anything.

Consent also has to be *scoped*, which is the part that is easy to get wrong. A
user who accepts recording composition to `~/.claude/harness-fitness/` has not
thereby accepted:

- writing the ledger into their git repository (`ledger.in_repo`), where it may be
  committed and pushed;
- writing `hfit_*` metadata onto beads issues (`beads.write_metadata`), which
  Dolt-syncs to other machines;
- reading their OTEL event log (`otel.enabled`), which contains every session;
- running inference (`inference.enabled`), which spends their tokens
  ([ADR-011](011-claude-code-is-the-only-permitted-llm.md));
- retaining distilled events for a different period (`events_retention_days`).

Each of those changes *what leaves the original scope*. A consent that survived
them would be consent to something the user never saw.

## Decision

**Nothing is written until `acknowledged.json` holds a fingerprint matching the
current governing config.** No composition record, no `changes.jsonl` line, no
directory. `consent.require_consent(cfg)` returns `(allowed, reason)` and every
write path calls it first.

**The fingerprint covers exactly the governing keys.**
`hfit_config._GOVERNING_KEYS` is `("ledger.in_repo", "beads.write_metadata",
"otel.enabled", "inference.enabled", "events_retention_days")` — the five that
decide *what is collected and where it goes*. `governing(cfg)` flattens them to a
dotted scalar-only dict, canonicalised and hashed. Changing one stops recording
until the user accepts again; changing a threshold or a display option does not
re-ask, because a gate that fires on `mad_threshold` is a gate users learn to
dismiss.

**The check runs before any path is resolved as a side effect.** Not merely before
the write — before the directory is computed. Resolving a project slug and creating
its parent is itself a trace: an empty
`~/.claude/harness-fitness/projects/some-repo/` tells a reader which projects were
opened, which is information the user has not agreed to record. `ledger.py` checks
consent as its first statement for that reason.

**The two refusals are distinguished, because they need different remedies.**
`not_acknowledged` means the user has never been asked. `config_changed` means they
consented to something else. A single "denied" would leave the second case looking
like a broken install.

**Consent is never implied by the CLI.** `acknowledge.py` uses an argparse mutually
exclusive group with `required=True` over `--accept` / `--show` / `--revoke`, so a
bare invocation exits non-zero from argparse itself rather than defaulting to the
agreeable branch. `--show` reports where files would go, what is never written, and
that nothing has been — and writes nothing to produce that report.

**Revocation stops future writes and deletes nothing.** Deleting the user's data on
withdrawal would destroy the record they may want to inspect. `--revoke` is a stop,
not an erase; the files are theirs and `rm` is well documented.

## Consequences

**A fresh install measures nothing, and that has to be visible.** A user who
installs and never acknowledges sees `{"ok": false, "reason": "not_acknowledged"}`
from `fitness.py` — which is why the report's failure encoding matters: on a failed
read there is **no** `measures`, `episodes_seen`, `current`, `changes` or `flags`
key at all. `null` is already taken as the legitimate value of a *successful* read
at 0.1.0, so absence of the key is the only encoding that cannot be mistaken for a
result. The README puts "nothing is recorded until you acknowledge" in the install
flow rather than in a footnote.

**`fitness.py` does not read stdin and creates nothing to answer a question.**
Reporting on an unacknowledged install must not be the thing that produces the
first artefact. The refusal path is the one most first-time readers hit, so
`tests/unit/test_skills.py` runs each skill's documented command unacknowledged and
requires exit 0 with parseable JSON — the path a user meets first is the path least
likely to be covered by a fixture.

**"Nothing was written" is asserted by existence, not by listing files.** A
`files_under(...) == []` assertion passes for a leaked `mkdir`, and an empty
directory is a trace. Where the claim is that nothing was written before consent,
the tests assert `not os.path.exists(...)`; `entries_under()` exists for the
weaker "nothing new appeared" claim, and `files_under()` keeps its file-only
semantics deliberately so the two are not confused.

**Adding a governing key is a decision that re-asks every existing user.** That is
the correct cost and it should not be dodged by adding a collection-widening
setting outside `_GOVERNING_KEYS`. The test suite binds to the tuple, so the
question is forced at review rather than discovered by a user whose data started
going somewhere new.

**The gate is what makes the privacy statement in the README a fact rather than a
promise.** Together with [ADR-010](010-hook-commands-are-hashed-never-stored.md)
(commands hashed, never stored) and
[ADR-011](011-claude-code-is-the-only-permitted-llm.md) (no HTTP client, no
provider SDK, no API key), the claim "nothing leaves the machine and nothing is
written until you say so" is enforced in three places and tested in each. For a
tool whose entire function is measurement, that precision is the difference between
adoptable and creepy.
