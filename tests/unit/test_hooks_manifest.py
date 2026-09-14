"""`hooks/hooks.json` — the wiring, read as data.

A manifest is the one artefact in a plugin that nothing else exercises. A test
suite can drive every script directly and stay entirely green while the manifest
names a script that does not exist, spells the interpreter in a way that fails on
another OS, or claims an event this plugin promised not to take. Each of those is
indistinguishable, from inside a session, from a plugin that does nothing at all.

So the manifest gets asserted as data, and every assertion below names the failure
it prevents rather than restating the schema.
"""

import json
import os
import re

import pytest

from conftest import REPO_ROOT

MANIFEST = REPO_ROOT / "hooks" / "hooks.json"

# Events this plugin refuses to claim. `PreToolUse` is the substantive one: abacus
# already owns the edit hot path with the only hook whose response can stop a tool
# call, and `tool_decision` plus `hook_execution_complete` already report every
# block, so a second blocking hook there buys nothing and costs latency on every
# edit (ADR-004). The other three are refused for the same reason — every hook this
# plugin adds enlarges the composition it exists to measure (ADR-006).
REFUSED = ("PreToolUse", "SubagentStop", "Notification", "PermissionRequest")

# The six events the plan claims, and the timeout each is budgeted. Listed here in
# full rather than grown one release at a time, so that wiring an event without
# deciding its budget fails instead of inheriting an unstated default.
BUDGETS = {
    "SessionStart": 15,
    "UserPromptSubmit": 5,
    "PostToolUse": 20,
    "PreCompact": 5,
    "Stop": 15,
    "SessionEnd": 45,
}

# `command -v python3 … && python3 <script> || python <script>`. A bare `python3` is
# absent on some Windows installs and a bare `python` on many Linux ones.
_PORTABLE = re.compile(
    r'^command -v python3 >/dev/null 2>&1 && python3 "(?P<primary>[^"]+)"'
    r'(?P<primary_args>[^|]*)\|\| python "(?P<fallback>[^"]+)"(?P<fallback_args>.*)$'
)

_SCRIPT_TOKEN = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/"


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def events(manifest):
    return manifest["hooks"]


def entries(events):
    """Every `(event, matcher, hook_entry)` in the manifest, flattened.

    Yielded rather than returned so a failure names the event it came from.
    """
    for event, groups in sorted(events.items()):
        for group in groups:
            for entry in group.get("hooks", []):
                yield event, group.get("matcher"), entry


def parsed(command):
    match = _PORTABLE.match(command)
    assert match, "not the portable interpreter idiom: %r" % command
    return match


class TestItIsWiredAtAll:
    def test_the_manifest_exists_and_is_json(self, manifest):
        # A malformed manifest is not a loud error: Claude Code loads the plugin and
        # simply registers nothing, so every measurement silently becomes empty.
        assert isinstance(manifest.get("hooks"), dict)

    def test_session_start_is_claimed(self, events):
        assert "SessionStart" in events

    def test_every_entry_is_a_command_hook(self, events):
        for event, _matcher, entry in entries(events):
            assert entry.get("type") == "command", event


class TestTheInterpreterIsPortable:
    def test_every_command_uses_the_portable_idiom(self, events):
        for event, _matcher, entry in entries(events):
            parsed(entry["command"])

    def test_both_branches_name_the_same_script(self, events):
        # The failure this catches is specific and survives every other test in the
        # repo: a copy-paste that leaves the `|| python` branch pointing at a
        # different script runs the wrong code on exactly the machines that have no
        # `python3` — and never on the developer's.
        for event, _matcher, entry in entries(events):
            match = parsed(entry["command"])
            assert match.group("primary") == match.group("fallback"), event
            assert match.group("primary_args").strip() == match.group(
                "fallback_args"
            ).strip(), event

    def test_every_script_is_resolved_through_the_plugin_root(self, events):
        # An absolute path here would work on this machine and nowhere else; a
        # relative one resolves against the session's cwd, which is the user's
        # project, not the plugin.
        for event, _matcher, entry in entries(events):
            assert _SCRIPT_TOKEN in parsed(entry["command"]).group("primary"), event

    def test_every_named_script_exists_on_disk(self, events):
        for event, _matcher, entry in entries(events):
            named = parsed(entry["command"]).group("primary")
            rel = named.replace("${CLAUDE_PLUGIN_ROOT}/", "")
            assert os.path.exists(os.path.join(str(REPO_ROOT), rel)), (event, named)


class TestTheBudgets:
    def test_every_entry_declares_a_timeout(self, events):
        # Without one the hook inherits a default the manifest does not state, so
        # the SLA in `spec.manifest.yaml` would describe a number nobody chose.
        for event, _matcher, entry in entries(events):
            assert isinstance(entry.get("timeout"), int), event

    def test_each_timeout_matches_the_planned_budget(self, events):
        for event, _matcher, entry in entries(events):
            assert event in BUDGETS, "unbudgeted event: %s" % event
            assert entry["timeout"] == BUDGETS[event], event


class TestTheRefusals:
    @pytest.mark.parametrize("event", REFUSED)
    def test_the_refused_events_are_absent(self, events, event):
        # ADR-004 asserted rather than merely documented. A refusal that lives only
        # in prose is reversed by the first person who finds a blocking hook
        # convenient.
        assert event not in events

    def test_no_event_outside_the_claimed_set_is_wired(self, events):
        assert set(events) <= set(BUDGETS)


class TestTheContracts:
    def test_every_claimed_event_has_an_input_contract(self, events):
        # The payload shape a hook reads is an interface with Claude Code, and the
        # only place its version can be dated against an observed `app.version`.
        for event in events:
            kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", event).lower()
            path = REPO_ROOT / "contracts" / "input" / ("%s.md" % kebab)
            assert path.exists(), "missing contracts/input/%s.md" % kebab
