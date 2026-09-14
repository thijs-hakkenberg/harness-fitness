# Contributing

Two rules are not negotiable. Everything else is a discussion.

## 1. Tests before implementation

Every change lands as red → green → refactor. The failing test comes first, it
fails for the right reason, and only then is the implementation written. A pull
request whose diff contains implementation without a test that would have failed
before it will be asked to reorder itself.

This is not ceremony. Most of what this plugin knows is *behavioural detail
someone already got wrong* — that `bd show --json` returns a single-element
array, that an OTLP attribute key is `session.id` and not `session_id`, that a
non-zero `bd` exit code is not an empty result set. None of that knowledge is
inherited as code; the tests are the only thing carrying it. If you delete a
test you have deleted the knowledge.

## 2. Never write outside the `hfit_*` namespace

This plugin reads beads metadata written by
[abacus](https://github.com/thijs-hakkenberg/abacus) and writes only keys
prefixed `hfit_`. The `abacus_*` namespace has exactly one legitimate
constructor and it is not in this repository. `hooks/lib/beads_write.py` asserts
the prefix and raises on `abacus_`; `tests/unit/test_beads_namespace.py`
inspects every recorded `bd` argv. Both are there to make an accidental
cross-write loud rather than silent.

The same applies to the ledger: nothing outside
`$HFIT_STATE_DIR` (default `~/.claude/harness-fitness/`) is written, and nothing
at all is written before the user has run `acknowledge.py --accept`.

## Running the suite

```bash
python3 -m pytest                        # unit + integration
HFIT_REAL_BD_TESTS=1 python3 -m pytest   # also the real-`bd` falsifiers
```

CI runs the suite on Python 3.9 and 3.12. The 3.9 leg is what keeps the
stdlib-only floor honest: hook code may import nothing that is not in the
standard library of Python 3.9, because that is what ships on a stock macOS.
Test code may use `pytest`, `pytest-bdd`, `coverage` and `pyyaml`; hook code may
not. `pyyaml` is there for one reader — `tests/unit/test_spec_conformance.py`
parses `spec.manifest.yaml` — and the import lives inside a fixture rather than at
module scope, so a missing PyYAML skips the manifest tests and leaves the
dangling-ADR-reference guard running. An always-running test asserts the CI
workflow installs it, because a guard that silently disables itself in CI is worth
nothing there.

## Commit messages

Conventional prefixes: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
One logical change per commit.

## Design decisions

A decision that constrains future code goes in `adr/` as a numbered record, not
in a comment and not in the README. If your pull request argues with an existing
ADR, say which number — that is faster for both of us than rediscovering the
argument.
