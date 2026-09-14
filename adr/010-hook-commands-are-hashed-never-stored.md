# ADR-010 — Hook and MCP command strings are hashed, never stored

- **Status:** accepted
- **Date:** 2026-09-14
- **Governs:** `hooks/lib/settings_read.py` (the enforcement boundary), `hooks/lib/plugin_scan.py`, `hooks/lib/composition.py`, and every record written under `~/.claude/harness-fitness/`
- **Related:** [ADR-007](007-the-composition-digest-sits-beside-the-env-hash.md) the-composition-digest-sits-beside-the-env-hash, [ADR-014](014-consent-gates-every-write.md) consent-gates-every-write

## Context

The composition digest has to react when a hook changes. If two harnesses differ
only in what a `PostToolUse` hook runs, they are different harnesses, and a digest
that cannot tell them apart merges two populations of episodes and reports the
mixture as though it described one thing.

So the command is part of the identity. But the command is also, as measured on
this machine, one of the worst strings in the settings file to keep:

```
"command": "bash /Users/hakketh/.claude/hooks/auto-test.sh"
```

Two problems in one line. It carries an **absolute path containing a username** —
so a composition record becomes personally identifying, and a record shared to
compare two harnesses leaks a home directory layout. And a command is an arbitrary
shell string, so it can carry **anything a shell command can carry**: an inline
`--token`, an `Authorization: Bearer …` header in a `curl`, an environment
assignment prefixing the binary. MCP server definitions are worse, because passing
a credential as an argument is a normal way to configure one.

This is not hypothetical adjacent to this repo. `~/.claude/settings.json` on this
machine holds a live Databricks personal access token in
`env.ANTHROPIC_AUTH_TOKEN`. The `env` block is a different field, and composition
never opens it — but the demonstration stands: a settings file is a place
credentials live, and any module reading one is handling secrets whether or not it
means to.

The tempting design is a filter: read commands, redact what looks sensitive before
writing. That fails on the only case that matters. A filter is a list of patterns
someone has to keep correct forever against an adversary that is not adversarial —
just a colleague configuring a new tool with a differently-shaped key. The first
credential format the list does not know about is written verbatim into a file the
user was told was safe to share.

## Decision

**A raw command string never crosses out of `settings_read`.** The module reduces
every hook and MCP command to two fields at the boundary:

- `cmd_basename` — the final path segment, e.g. `auto-test.sh`. Human-legible, so a
  reader can recognise their own hook in a profile.
- `cmd_digest` — a sha256 prefix over the **whole** command string. One-way, and
  fully sensitive to any change in the command, so the digest moves when the hook's
  behaviour moves.

`hook_entries()` is the single implementation, shared with `plugin_scan` rather
than reimplemented there, so the reduction cannot be correct in one caller and
wrong in the other. `composition.py` consumes the reduced form and has no path to
the raw string at all.

**Reduction happens at the earliest possible point** — at the read boundary, not
before the write. The difference matters: with the reduction at the boundary, a raw
command is never in a data structure that a future feature could serialise by
accident. There is nothing downstream to be careful with.

**No absolute path reaches any record.** Install paths and marketplace
`installLocation` values are dropped rather than trusted to be uninteresting.
`cmd_basename` is a basename precisely so the directory cannot travel with it.

**A hook command is untrusted data read off disk.** `plugin_scan` resolves
`${CLAUDE_PLUGIN_ROOT}` when computing a plugin's content digest, but opens a file
only when its realpath is under that plugin's directory — so a manifest naming
`/etc/passwd`, or a multi-gigabyte binary, cannot steer a read.

**Asserted, and asserted loudly.** `tests/unit/test_settings_read.py` plants a
credential-shaped literal in a hook command and asserts it appears nowhere in the
output; `tests/unit/test_security.py` sweeps every written artefact. The planted
literal is assembled from parts (`"dapi" + "0123456789abcdef" * 2`) because a
credential-shaped string committed to a public repo is a liability regardless of
provenance — a scanner matches the shape, not the intent.

## Consequences

**A composition record cannot tell you what your hook does.** `cmd_basename` plus
a digest identifies it and detects a change; reconstructing the command requires
reading the settings file, which the owner can do and a recipient cannot. That is
the intended asymmetry.

**Two hooks with the same basename and different commands are distinguishable, and
two with different basenames and the same command are not confusable.** The digest
covers the whole string, so `auto-test.sh` before and after an argument change are
two components. This is what makes the composition digest sensitive to a hook edit
at all, which is the point of hashing rather than dropping.

**A cosmetic edit moves the digest.** Adding a comment or reordering a flag in a
hook command produces a new composition, splitting a population of episodes across
two digests. With `min_episodes_per_digest` at 5, that can turn ten comparable
episodes into two uncomparable groups. The alternative — normalising commands
before hashing — means deciding which differences are cosmetic, and a normaliser
that gets it wrong merges two genuinely different harnesses. Over-splitting fails
loudly as `insufficient_n`; under-splitting fails silently as a wrong number, so
the digest errs toward splitting ([ADR-007](007-the-composition-digest-sits-beside-the-env-hash.md)).

**This is the property that makes the privacy claim in the README true.** "Hook and
MCP command strings are hashed, never stored" is not a promise about care taken; it
is a statement about what the code can do, enforced at one boundary and tested by
planting a secret. That distinction is the whole reason for stating it precisely:
a measurement tool whose privacy claim is an intention is indistinguishable from
one that does not mean it.
