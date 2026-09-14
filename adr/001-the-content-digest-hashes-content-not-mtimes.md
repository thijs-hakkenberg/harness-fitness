# ADR-001 — The plugin content digest hashes file content, not sizes and mtimes

- **Status:** accepted
- **Date:** 2026-09-14
- **Supersedes:** the wording in the implementation plan ("a `content_digest` over `hooks.json` and sorted `skills`/`agents` sizes-and-mtimes")

## Context

A plugin's identity in the composition digest is its `gitCommitSha`. A plugin
installed from a `"source": "directory"` marketplace — the local development
case, and the case this plugin itself is installed under — often has no sha at
all. Without a fallback, live-editing a local plugin never moves the composition
digest, which defeats the measurement for exactly the plugin its author is
iterating on.

The plan specified that fallback as a digest over sizes and mtimes. Both halves
of that turn out to be wrong in the same direction.

**Mtimes move when the harness does not.** A fresh clone rewrites every mtime.
So does `touch`, a `git checkout` between branches with identical content, and
some editors' save-and-restore. Each would report a composition change where no
behaviour changed.

The cost of a false composition change is not a cosmetic wrong label. Episodes
are grouped by `(composition_digest, env_hash)`, and a group below
`min_episodes_per_digest` (default 5) is refused with `insufficient_n`. A
spurious digest split therefore takes one measurable population of ten episodes
and produces two unmeasurable populations of five and five. The measure does not
become wrong; it becomes unavailable, and for a reason no one reading the report
would be able to attribute to a `git clone`.

**Sizes move only sometimes.** A one-character edit to a `SKILL.md` — inverting
an instruction, say — leaves the file the same length. A size-based digest would
sit still across it.

There is a second gap in the plan's file list, independent of the hashing
method. It covers `hooks.json`, `skills/` and `agents/` — the *declarations*. But
a directory plugin under development is edited in its **scripts**, not its
declarations. `hooks.json` names `${CLAUDE_PLUGIN_ROOT}/hooks/scripts/gate.py`
once and then never changes again while the script behind it is rewritten daily.
Under the plan's list, this plugin's own digest would have stayed fixed across
the entire step 2–8 build.

## Decision

The fallback digest hashes **file content**, over the declaration surface *plus*
the hook scripts those declarations name:

- `.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`
- every `skills/*/SKILL.md` and every `agents/*.md`
- every path a hook command resolves to via `${CLAUDE_PLUGIN_ROOT}`, **only when
  the resolved realpath lands inside the plugin directory**

The relative path is hashed alongside each file's bytes, so moving a skill counts
as a change even when its content is identical. The file list is sorted rather
than taken in directory order, because directory iteration order is not
guaranteed across filesystems and an unsorted walk would give one plugin two
digests on two machines.

`tests/unit/test_plugin_scan.py::TestContentDigest` asserts the invariance
directly: `os.utime` to a fixed epoch does not move the digest, while a changed
`SKILL.md` body, an added `hooks.json`, an added agent and a changed *referenced
script* each do.

## Consequences

The digest moves if and only if the plugin does. That is the property the whole
composition measure rests on, and it is now a test rather than an assumption.

**Reading files costs more than stat'ing them.** This runs on the `SessionStart`
hot path, so the read is bounded: at most 256 files and 4 MB in total, with the
budget shared across the whole digest and a `<truncated>` marker hashed in when
it is exhausted. A plugin large enough to hit either bound gets a digest over its
first 4 MB, which is still stable and still moves on an edit within that window.

**Files outside the plugin directory are never opened.** A hook command is data
read off disk, not something this module chose, and it can legitimately name
`/usr/bin/env`, `/etc/passwd` or a multi-gigabyte binary. The realpath check is
what keeps a scan from being steerable by the contents of a manifest, and it is
asserted rather than assumed. The residual limit is real and accepted: a plugin
whose behaviour lives in a script *outside* its own directory has a digest that
does not move when that script changes. Nothing observed on disk does this.

**An unreadable file contributes `<unreadable>` rather than nothing.** A file
whose state could not be determined is a different fact from a file that is
absent, and collapsing the two would let a permissions change look like a
deletion.

The `sha_basis` field remains the honest signal for all of this: `git` when a
commit sha exists, `content` when this digest was used, `version-only` when
neither was available. A consumer that needs to know how much to trust a
composition identity reads that field rather than guessing from the digest's
shape.
