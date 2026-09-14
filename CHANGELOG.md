# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Contract-level versioning is tracked separately in each file under `contracts/`:
a contract can change shape without the plugin's version moving, and the
contract file records its own history so a consumer can tell what it may rely on.

## [Unreleased]

### Added

- Repository skeleton: MIT licence, plugin and marketplace manifests, coverage
  and pytest configuration, CI on Python 3.9 and 3.12.
- Test harness: isolation fixtures giving every test a temporary `HOME` and a
  PATH-prefixed stub `bd`/`git` that records its argv, plus `otel_log`,
  `transcript`, `settings_tree` and `frozen_now`. Without these, no later red
  phase can be written honestly — a test that reads the real `~/.claude` is
  measuring the developer's machine, not the code.
- Foundation modules, each written against a failing test: `hfit_time` (a
  freezable clock), `_security` (path refusal and one-way command reduction —
  [ADR-010](adr/)), `state_store` (atomic, mode-restricted writes and
  lossy-tolerant reads), `hfit_config` (defaults, deep merge and the governing
  subset), `consent` (nothing is written until a fingerprint matches —
  [ADR-014](adr/)) and `hook_io` (the fail-open hook protocol).
- [ADR-011](adr/011-claude-code-is-the-only-permitted-llm.md): Claude Code is
  the only LLM this plugin will ever use — no HTTP client, no provider SDK, no
  endpoint, no API key, not even as a fallback. Where a subagent cannot be
  dispatched, inference is unavailable and the verdict stays `unstated`.
  Asserted by `test_no_inference_dependency.py`, which parses every module under
  `hooks/` with `ast`; a bring-your-own-LLM path would have sent issue titles and
  commit diffs off the machine, breaking the one promise that makes a
  measurement tool adoptable.
- [ADR-013](adr/013-the-inferential-verdict-is-a-backfill-tool.md): the
  `inferential` verdict ranks *below* `lexical`, is reachable only from an
  interactive `/hfit:outcome --infer`, and exists to backfill issues closed
  before the `accepted:` habit — not to supply the priority-1 measure with a
  denominator.
- [ADR-003](adr/003-project-slug-is-character-wise-and-resolved-on-disk.md):
  the project slug is character-wise and the transcript directory is resolved
  against the filesystem, because whether Claude Code collapses runs of
  separators is not determinable from any observable path. Guessing would have
  made a missing transcript directory read as a session with no human
  interventions, inflating autonomy.

[Unreleased]: https://github.com/thijs-hakkenberg/harness-fitness/commits/main
