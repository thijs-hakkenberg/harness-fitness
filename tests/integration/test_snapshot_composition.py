"""The `SessionStart` hook, driven as a real subprocess.

In-process calls would not catch the three failures that make a hook
indistinguishable from a plugin that does nothing — an import error, a
stdout-protocol violation, a non-zero exit — so every test here runs the script
the way Claude Code runs it: `python3 <script>` with JSON on stdin.

Two assertions repeat throughout and both are about restraint rather than
function:

- **stdout is empty.** A `SessionStart` response may carry `additionalContext`,
  which is spent from the user's context window on every single session. A tool
  whose purpose is to measure the harness's cost must not be a line item in it
  (ADR-006). Emitting nothing is a feature, and the only way to keep it is to
  assert it.
- **stderr is empty and the exit code is 0**, including on malformed input and an
  unwritable state root. Claude Code surfaces hook stderr, so a traceback here is
  a measurement tool printing into a session the user did not ask it to speak in.
"""

import os

import pytest

import composition
import consent
import hfit_config
import ledger
import state_store
from conftest import files_under

SCRIPT = "snapshot_composition.py"

PLANTED_TOKEN = "dapi" + "0123456789abcdef" * 2

ONE_PLUGIN = {
    "abacus@abacus": {
        "version": "0.3.1",
        "gitCommitSha": "aaaa111",
        "marketplace": "abacus",
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": "/x/audit.py"}],
                }
            ]
        },
        "skills": ["audit"],
    },
    "other@abacus": {"version": "1.0.0", "gitCommitSha": "bbbb222"},
}

ENABLED_ONE = {"enabledPlugins": {"abacus@abacus": True}}
ENABLED_BOTH = {"enabledPlugins": {"abacus@abacus": True, "other@abacus": True}}


@pytest.fixture
def cfg():
    return hfit_config.load()


@pytest.fixture
def accepted(cfg):
    consent.record_acknowledgement(cfg)
    return cfg


def payload(cwd, session="sess-1", source="startup"):
    return {
        "session_id": session,
        "cwd": str(cwd),
        "hook_event_name": "SessionStart",
        "source": source,
    }


def _block_the_projects_dir():
    """Plant a regular file where the per-project directories have to be created.

    `state_root()/projects/<slug>/` can then never be made, so the first write
    raises `NotADirectoryError` — a real exception from the real write path, rather
    than a permission refusal that a differently-privileged CI runner might not
    honour. `acknowledged.json` sits above `projects/` and stays readable.
    """
    root = state_store.state_root()
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "projects"), "w") as fh:
        fh.write("not a directory\n")


def written(cwd, cfg=None):
    return files_under(ledger.root(str(cwd), cfg))


def changes(cwd, cfg=None):
    return ledger.changes(str(cwd), cfg)


class TestTheSessionStartContract:
    def test_it_exits_zero_and_emits_nothing_at_all(self, run_hook, settings_tree, accepted):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        result = run_hook(SCRIPT, payload(tree.project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_it_records_the_composition_and_a_baseline(
        self, run_hook, settings_tree, accepted
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        run_hook(SCRIPT, payload(tree.project))

        log = changes(tree.project, accepted)
        assert len(log) == 1
        assert log[0]["kind"] == "baseline"
        assert log[0]["from"] is None

        digest = log[0]["to"]
        assert written(tree.project, accepted) == [
            "changes.jsonl",
            "compositions/%s.json" % digest,
        ]

    def test_the_digest_matches_an_in_process_build(
        self, run_hook, settings_tree, accepted
    ):
        # The hook is not permitted to compute a *different* identity from the one
        # every other consumer computes. If it drifts, episodes group under a digest
        # no report can resolve.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        run_hook(SCRIPT, payload(tree.project))

        expected = composition.build(tree.home, tree.project)["digest"]
        assert ledger.current_digest(str(tree.project), accepted) == expected

    def test_the_stored_record_carries_the_profile_and_the_flags(
        self, run_hook, settings_tree, accepted
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        run_hook(SCRIPT, payload(tree.project))

        digest = ledger.current_digest(str(tree.project), accepted)
        record = ledger.read_composition(str(tree.project), digest, accepted)
        assert record["schema"] == composition.SCHEMA
        assert record["profile"]["fb_comp"] >= 1
        assert isinstance(record["flags"], list)

    def test_the_project_comes_from_the_payload_not_the_process(
        self, run_hook, settings_tree, accepted, tmp_path
    ):
        # A `SessionStart` hook's process cwd is not a documented guarantee, and a
        # ledger written under the wrong project silently splits one project's
        # history across two directories.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        run_hook(SCRIPT, payload(tree.project), cwd=elsewhere)

        assert written(tree.project, accepted) != []
        assert written(elsewhere, accepted) == []


class TestTheConsentGate:
    def test_an_unacknowledged_install_writes_nothing(
        self, run_hook, settings_tree, cfg
    ):
        # Asserted on the filesystem rather than on a return value: the hook has no
        # return value a user can see, so "wrote nothing" is the only observable
        # form of the promise the README makes (ADR-014).
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        result = run_hook(SCRIPT, payload(tree.project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert files_under(state_store.state_root()) == []

    def test_a_changed_governing_config_stops_writing_again(
        self, run_hook, settings_tree, accepted, tmp_path
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        run_hook(SCRIPT, payload(tree.project))
        before = written(tree.project, accepted)
        # Guards the rest of this test against passing vacuously: if the first run
        # wrote nothing, "the second run wrote nothing new" is true for the wrong
        # reason and the consent invalidation is never exercised at all.
        assert before != []

        state_store.write_json_atomic(
            hfit_config.config_path(), {"ledger": {"in_repo": True}}
        )

        run_hook(SCRIPT, payload(tree.project))

        # The old ledger is untouched and no new one appears in the repo.
        assert written(tree.project, accepted) == before
        assert files_under(tmp_path / "project" / ledger.IN_REPO_DIRNAME) == []


class TestMovement:
    def test_a_second_session_with_the_same_harness_appends_nothing(
        self, run_hook, settings_tree, accepted
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        run_hook(SCRIPT, payload(tree.project, session="sess-1"))
        run_hook(SCRIPT, payload(tree.project, session="sess-2"))

        # One record per session would make `changes.jsonl` a session log, and the
        # question it exists to answer — what changed between A and B — would have
        # to be reconstructed by de-duplicating it.
        assert len(changes(tree.project, accepted)) == 1

    def test_enabling_a_plugin_appends_a_transition_naming_it(
        self, run_hook, settings_tree, accepted
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        run_hook(SCRIPT, payload(tree.project, session="sess-1"))
        first = ledger.current_digest(str(tree.project), accepted)

        settings_tree(user=ENABLED_BOTH, plugins=ONE_PLUGIN)
        run_hook(SCRIPT, payload(tree.project, session="sess-2"))

        log = changes(tree.project, accepted)
        assert len(log) == 2
        assert log[1]["kind"] == "transition"
        assert log[1]["from"] == first
        assert log[1]["added"] == ["plugin:other@abacus"]
        assert log[1]["diff_basis"] == "records"

    def test_a_changed_environment_moves_nothing_in_the_ledger(
        self, run_hook, settings_tree, accepted
    ):
        # Documents a hazard rather than a feature. The composition file is
        # content-addressed and immutable, so its `env_hash` is the one in force at
        # *first sighting* and is never refreshed; a later session under a different
        # model leaves the ledger completely unmoved, because the harness did not
        # move. An episode's `env_hash` therefore has to be resolved for the episode
        # and must never be read off the composition record (ADR-007).
        tree = settings_tree(
            user=dict(ENABLED_ONE, env={"ANTHROPIC_MODEL": "claude-a"}),
            plugins=ONE_PLUGIN,
        )
        run_hook(SCRIPT, payload(tree.project, session="sess-1"))
        digest = ledger.current_digest(str(tree.project), accepted)
        pinned = ledger.read_composition(str(tree.project), digest, accepted)["env_hash"]

        settings_tree(
            user=dict(ENABLED_ONE, env={"ANTHROPIC_MODEL": "claude-b"}),
            plugins=ONE_PLUGIN,
        )
        run_hook(SCRIPT, payload(tree.project, session="sess-2"))

        assert ledger.current_digest(str(tree.project), accepted) == digest
        assert len(changes(tree.project, accepted)) == 1
        record = ledger.read_composition(str(tree.project), digest, accepted)
        assert record["env_hash"] == pinned


class TestTheEnvironmentPin:
    def test_the_record_carries_a_full_width_env_hash(
        self, run_hook, settings_tree, accepted
    ):
        # 64 hex, not 12: the rig's result schema pins it, and a truncated one fails
        # `--rig-handoff` validation only after every episode has been recorded with
        # the wrong width (ADR-007).
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        run_hook(SCRIPT, payload(tree.project))

        digest = ledger.current_digest(str(tree.project), accepted)
        record = ledger.read_composition(str(tree.project), digest, accepted)
        # Checked for presence before width. A hook that stops passing the pin at all
        # leaves `None` here, and `len(None)` would report that as a `TypeError` from
        # the test rather than as the missing field it is.
        assert isinstance(record["env_hash"], str)
        assert len(record["env_hash"]) == 64
        assert set(record["env_hash"]) <= set("0123456789abcdef")

    def test_the_transition_carries_the_same_env_hash(
        self, run_hook, settings_tree, accepted
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        run_hook(SCRIPT, payload(tree.project))

        log = changes(tree.project, accepted)
        digest = log[0]["to"]
        record = ledger.read_composition(str(tree.project), digest, accepted)
        assert log[0]["env_hash"] == record["env_hash"]


class TestNoLeaks:
    def test_no_credential_and_no_absolute_path_reaches_a_written_file(
        self, run_hook, settings_tree, accepted
    ):
        tree = settings_tree(
            user=dict(
                ENABLED_ONE,
                env={"ANTHROPIC_AUTH_TOKEN": PLANTED_TOKEN},
                hooks={
                    "PostToolUse": [
                        {
                            "matcher": "Edit",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/Users/someone/.claude/hooks/x.sh --token=%s"
                                    % PLANTED_TOKEN,
                                }
                            ],
                        }
                    ]
                },
            ),
            plugins=ONE_PLUGIN,
        )

        run_hook(SCRIPT, payload(tree.project))

        root = ledger.root(str(tree.project), accepted)
        blob = ""
        for rel in files_under(root):
            with open(os.path.join(root, rel)) as fh:
                blob += fh.read()

        assert blob != ""
        assert PLANTED_TOKEN not in blob
        assert "/Users/someone" not in blob
        assert str(tree.home) not in blob


class TestFailOpen:
    def test_corrupt_stdin_is_survived_silently(self, run_hook, settings_tree, accepted):
        settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        result = run_hook(SCRIPT, raw_stdin="{not json at all")

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_empty_stdin_is_survived_silently(self, run_hook, settings_tree, accepted):
        settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        result = run_hook(SCRIPT, raw_stdin="")

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_a_blocked_write_path_is_survived_silently(
        self, run_hook, settings_tree, accepted
    ):
        # The fault is planted *inside* the acknowledged state root, not by pointing
        # `$HFIT_STATE_DIR` somewhere unwritable: moving the root also moves
        # `acknowledged.json`, so the hook would refuse on consent and never reach a
        # write. This blocks the write while consent still holds, which is the only
        # way the fail-open path is actually the thing under test.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        _block_the_projects_dir()

        result = run_hook(SCRIPT, payload(tree.project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_an_absent_settings_tree_is_survived_silently(
        self, run_hook, accepted, tmp_path
    ):
        # A fresh machine with no `~/.claude/settings.json` is a real state, not an
        # error. The hook must record the empty harness rather than refuse.
        result = run_hook(SCRIPT, payload(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""
        assert len(changes(tmp_path, accepted)) == 1

    def test_a_blocked_write_is_not_an_exception_even_under_debug(
        self, run_hook, settings_tree, accepted
    ):
        # The interesting half of the pair above. `HFIT_DEBUG=1` re-raises whatever
        # `fail_open` caught, so if the hook's silence on a blocked write came from
        # swallowing an exception, this would produce a traceback. It does not: every
        # dependency in the body is total — `state_store` returns `False` rather than
        # raising, `hfit_time` falls back rather than raising, `settings_read` and
        # `plugin_scan` tolerate unreadable files — so a blocked write degrades to
        # *nothing recorded*, which the ledger's next run reads as an absent baseline
        # and re-attempts.
        #
        # This distinction matters for the next hook written against the same
        # protocol: `fail_open` is the last line of defence here, not the mechanism.
        # (`HFIT_DEBUG`'s re-raise is exercised directly in `test_hook_io.py`, where
        # a fault can be injected instead of hunted for.)
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        _block_the_projects_dir()

        result = run_hook(SCRIPT, payload(tree.project), env={"HFIT_DEBUG": "1"})

        assert result.returncode == 0
        assert result.stderr == ""


class TestLatency:
    @pytest.mark.slow
    def test_it_finishes_far_inside_its_timeout(self, run_hook, settings_tree, accepted):
        # Declared timeout is 15s. This runs on every session start, and a hook that
        # measures the harness's cost while adding meaningfully to it has made its
        # own reading worse (ADR-006).
        import time

        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        started = time.time()
        result = run_hook(SCRIPT, payload(tree.project))
        elapsed = time.time() - started

        # A crashed subprocess is fast. Asserting the exit code keeps the timing
        # assertion from passing because the work never happened.
        assert result.returncode == 0
        assert changes(tree.project, accepted) != []
        assert elapsed < 5.0
