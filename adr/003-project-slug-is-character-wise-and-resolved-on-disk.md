# ADR-003 — The project slug is character-wise, and the transcript directory is resolved on disk

- **Status:** accepted
- **Date:** 2026-09-14
- **Supersedes:** the wording in the implementation plan ("non-alphanumeric runs → single hyphen")

## Context

Claude Code stores session transcripts under `~/.claude/projects/<slug>/`, where
`<slug>` is derived from the project's absolute path. This plugin needs the same
transform twice: to name its own per-project state directory, and to locate the
transcript directory for the autonomy measure (M2), whose interrupt signal is a
transcript literal.

The transform was measured across all 99 real project directories on the
development machine. Every one is consistent with a single character-wise rule:
each non-alphanumeric character becomes `-`, so
`/Users/hakketh/projects/repos/App.DigitalTwin` becomes
`-Users-hakketh-projects-repos-App-DigitalTwin`.

What the evidence does **not** settle is whether *runs* of non-alphanumeric
characters collapse. No real repository path contains two adjacent
non-alphanumerics, so `/tmp/a_-b` could plausibly produce either `-tmp-a--b`
(character-wise) or `-tmp-a-b` (collapsing), and no observation on disk
discriminates between them. An attempt to force the question by listing a
directory with an adjacent-separator path found no such directory.

The plan assumed collapsing. That assumption is unsupported.

## Decision

Two functions rather than one.

`project_slug(cwd)` implements the **character-wise** rule with no collapsing. It
is the simpler rule, it is consistent with all 99 observations, and it is what
names this plugin's own state directory — where being self-consistent is all that
matters, because nothing else reads it.

`transcript_dir_for(cwd)` does **not** construct a path and return it. It builds
both candidate slugs, tests each against the filesystem, returns the first that
exists, and returns `None` when neither does.

## Consequences

An unresolved edge case degrades to `None` plus a reason
(`transcript-unavailable`) rather than to a confidently-wrong path. That matters
because the failure mode of guessing here is silent and directional: a
nonexistent transcript directory reads as a session with no human interruptions,
which inflates `no_touch_rate`. Absence of evidence of a touch would have been
recorded as evidence of autonomy — exactly the error the "unknown is never zero"
rule exists to prevent.

The cost is one extra `os.path.isdir` call on a path that will almost always hit
on the first candidate, and a second candidate that will, on current evidence,
never be needed.

If Claude Code's transform is ever observed to collapse runs, only
`project_slug` changes; `transcript_dir_for` already handles both and needs no
edit. Existing state directories would keep their old names, which is harmless —
they are content this plugin wrote for itself.
