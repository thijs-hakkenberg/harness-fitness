"""ADR-011: nothing under ``hooks/`` may reach the network or an LLM provider.

This plugin's one inference capability is a Claude Code subagent — a markdown
file that Claude Code dispatches using the session's own model and credentials.
So no module here needs an HTTP client, a provider SDK, an endpoint or a key,
and the README's central promise ("nothing leaves the machine; there is no
upload, no endpoint, no telemetry of its own") is therefore literally true.

A promise nothing asserts is a promise that survives until the first contributor
who has not read the ADR. This is the assertion.

Imports are read with ``ast`` rather than grepped, because a docstring
mentioning "socket" is not an import and a test that cannot tell the difference
would be either noisy or quietly disabled.
"""

import ast
import os

import pytest

HOOKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "hooks",
)

# Top-level module names that would put this plugin on the network or in front of
# a model provider. `subprocess` is deliberately absent: reaching `bd` and `git`
# is how episodes and diffs are read, and both are local.
FORBIDDEN_ROOTS = frozenset(
    {
        "aiohttp",
        "anthropic",
        "boto3",
        "google",
        "http",
        "httplib",
        "httpx",
        "openai",
        "requests",
        "socket",
        "ssl",
        "telnetlib",
        "urllib3",
        "websocket",
        "websockets",
        "xmlrpc",
    }
)

# `urllib` has one pure-string submodule that is legitimate; the rest talk.
FORBIDDEN_SUBMODULES = frozenset(
    {
        "urllib.error",
        "urllib.request",
        "urllib.response",
        "urllib.robotparser",
    }
)


def _python_files():
    found = []
    for root, _dirs, files in os.walk(HOOKS_DIR):
        for name in sorted(files):
            if name.endswith(".py"):
                found.append(os.path.join(root, name))
    return sorted(found)


def _imported_names(path):
    """Every dotted module name imported by `path`, at any nesting depth."""
    with open(path) as fh:
        tree = ast.parse(fh.read(), filename=path)

    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import (level > 0) has no external module to check.
            if node.module and not node.level:
                names.append(node.module)
    return names


def _offending(path):
    bad = []
    for name in _imported_names(path):
        root = name.split(".")[0]
        if root in FORBIDDEN_ROOTS or name in FORBIDDEN_SUBMODULES:
            bad.append(name)
    return bad


class TestNoNetworkOrProviderImports:
    @pytest.mark.parametrize("path", _python_files(), ids=os.path.basename)
    def test_module_imports_nothing_that_leaves_the_machine(self, path):
        offending = _offending(path)
        assert offending == [], "%s imports %s — see ADR-011" % (
            os.path.relpath(path, HOOKS_DIR),
            ", ".join(offending),
        )

    def test_the_scan_actually_found_modules(self):
        # Without this, deleting hooks/ or breaking the walk would make every
        # test above pass by scanning nothing. A vacuous guard is worse than no
        # guard, because it reports success.
        assert len(_python_files()) >= 6

    def test_the_scan_would_catch_a_violation(self, tmp_path):
        # Proves the detector detects. Otherwise the suite above is consistent
        # with `_offending` always returning [].
        planted = tmp_path / "leaky.py"
        planted.write_text("import urllib.request\nfrom anthropic import Anthropic\n")
        assert sorted(_offending(str(planted))) == ["anthropic", "urllib.request"]

    def test_a_docstring_mentioning_a_forbidden_name_is_not_an_import(self, tmp_path):
        # `ast` over grep. A module explaining *why* it opens no socket must not
        # fail for saying so.
        prose = tmp_path / "honest.py"
        prose.write_text('"""Opens no socket and no ssl context."""\nimport os\n')
        assert _offending(str(prose)) == []


class TestInferenceIsOffAndGoverned:
    def test_inference_stays_off_until_consent_is_re_granted(self):
        # ADR-013 clause 4. If `inference.enabled` left the governing subset,
        # enabling an LLM call would stop re-asking — the one config change that
        # most needs to.
        import hfit_config

        assert hfit_config.DEFAULTS["inference"]["enabled"] is False
        assert "inference.enabled" in hfit_config.governing({})
