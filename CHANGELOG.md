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
- [ADR-003](adr/003-project-slug-is-character-wise-and-resolved-on-disk.md):
  the project slug is character-wise and the transcript directory is resolved
  against the filesystem, because whether Claude Code collapses runs of
  separators is not determinable from any observable path. Guessing would have
  made a missing transcript directory read as a session with no human
  interventions, inflating autonomy.

[Unreleased]: https://github.com/thijs-hakkenberg/harness-fitness/commits/main
