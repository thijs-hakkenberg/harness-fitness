"""The `PostToolUse` and `Stop` hooks, driven as real subprocesses.

One script serves both events, so this module drives both payload shapes through
the same file the way Claude Code does: `python3 <script>` with JSON on stdin. An
in-process call would not catch an import error, a stdout-protocol violation or a
non-zero exit, which are the three failures that make a hook indistinguishable
from a plugin that does nothing.

Three assertions repeat throughout.

- **stdout is empty.** A `PostToolUse` response can inject context and a `Stop`
  response can send the agent back to work. This hook claims neither authority: a
  tool whose purpose is to measure what the harness costs must not be a line item
  in that cost (ADR-006), and deciding when a session is finished is not a power
  an instrument should hold.
- **stderr is empty and the exit code is 0**, on every path including a malformed
  payload and an unwritable state root. Claude Code surfaces hook stderr, so a
  traceback here is a measurement tool printing into a session the user did not
  ask it to speak in.
- **`bd` is not run at all** wherever it should not be. That one is the point of
  `TestTheCheapPath` and of `TestTheConsentGate`, and it is a property no
  assertion about files could reach: `PostToolUse:Bash` fires on every shell call
  and `Stop` on every turn, so "no subprocess" is what keeps the hook affordable,
  and separately what keeps an unacknowledged install from executing a process in
  a repository that may not be the user's.
"""

import json
import os
import time

import pytest

import beads_read
import consent
import hfit_config
import ledger
import state_store

SCRIPT = "reconcile_hook.py"

# Assembled from parts. A credential-shaped literal in a public repo is a
# liability even when it is synthetic, because a scanner matches the shape and not
# the provenance.
PLANTED_TOKEN = "dapi" + "0123456789abcdef" * 2

# Measured against `bd` 1.1.2: `bd list --all --json` prints a bare array of this
# shape, with abacus's keys already on the issue's `metadata`.
ISSUE = {
    "id": "Proj-abc",
    "title": "a closed thing",
    "status": "closed",
    "priority": 2,
    "issue_type": "task",
    "created_at": "2026-09-14T20:51:53Z",
    "started_at": "2026-09-14T20:52:02Z",
    "closed_at": "2026-09-15T08:35:23Z",
    "close_reason": "accepted: it works",
    "metadata": {
        "abacus_schema": 1,
        "abacus_partial": False,
        "abacus_models": "claude-opus-5",
        "abacus_tokens_total": 8134206,
        "abacus_tool_calls": 57,
    },
}


def issue(**over):
    out = dict(ISSUE)
    out["metadata"] = dict(ISSUE["metadata"])
    out.update(over)
    return out


@pytest.fixture
def cfg():
    return hfit_config.load()


@pytest.fixture
def accepted(cfg):
    consent.record_acknowledgement(cfg)
    return cfg


@pytest.fixture
def project(tmp_path):
    """A directory the subprocess can actually `cd` into.

    `beads_read._run` passes `cwd=` to `subprocess.run`, and an absent directory
    would surface as `bd_unavailable` — a refusal that would make several tests
    here pass for the wrong reason.
    """
    path = tmp_path / "proj"
    path.mkdir()
    return path


@pytest.fixture
def backlog(stub_bin):
    """Make `bd list` answer with a population, newest-first as `bd` does."""

    def install(*issues):
        stub_bin.on("bd", ["list"], stdout=list(issues) or [ISSUE])

    return install


def bash_payload(cwd, command):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": command},
        "tool_response": {"stdout": "", "stderr": "", "interrupted": False},
    }


def stop_payload(cwd, active=False):
    payload = {"hook_event_name": "Stop", "cwd": str(cwd)}
    if active:
        payload["stop_hook_active"] = True
    return payload


def episodes(cwd, cfg=None):
    return ledger.episodes(str(cwd), cfg)


def raw_ledger(cwd, cfg=None):
    path = ledger.episodes_path(str(cwd), cfg)
    if not os.path.exists(path):
        return ""
    with open(path) as fh:
        return fh.read()


def updates(stub_bin):
    """Every `bd update` argv, which is every index write this hook caused."""
    return [call for call in stub_bin.calls("bd") if call[:1] == ["update"]]


def _block_the_projects_dir():
    """Plant a regular file where the per-project directories have to be created.

    `state_root()/projects/<slug>/` can then never be made, so the first write
    raises `NotADirectoryError` from the real write path. `acknowledged.json` sits
    above `projects/` and stays readable, which is what keeps consent holding
    while the write fails.
    """
    root = state_store.state_root()
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "projects"), "w") as fh:
        fh.write("not a directory\n")


class TestThePostToolUseContract:
    def test_a_close_command_records_the_episode_and_writes_the_index(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        backlog()

        result = run_hook(
            SCRIPT, bash_payload(project, "bd close Proj-abc --reason 'accepted: it works'")
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

        recorded = episodes(project, accepted)
        assert [record["issue_id"] for record in recorded] == ["Proj-abc"]
        assert recorded[0]["verdict"] == "accepted"
        assert recorded[0]["verdict_basis"] == "structured"

        written = updates(stub_bin)
        assert len(written) == 1
        assert "hfit_verdict=accepted" in written[0]

    def test_the_close_is_recognised_behind_the_command_that_justified_it(
        self, run_hook, backlog, accepted, project
    ):
        # The shape a real close arrives in as often as not: the tests that earned
        # the close, chained ahead of it. A substring match would have found this
        # one too, but only by accident — `is_close_command` tokenises, which is
        # what makes it both find this and reject an echo of the same text.
        backlog()

        result = run_hook(SCRIPT, bash_payload(project, "pytest -q && bd close Proj-abc"))

        assert result.returncode == 0
        assert len(episodes(project, accepted)) == 1

    def test_the_subprocess_runs_in_the_project_the_payload_names(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # `bd` resolves its database by walking up from the working directory, so a
        # read from the wrong directory reports whichever backlog happens to sit
        # above this process. The payload's `cwd` is authoritative over both
        # `$CLAUDE_PROJECT_DIR` and the hook process's own cwd.
        backlog()

        run_hook(SCRIPT, bash_payload(project, "bd close Proj-abc"))

        assert [call["cwd"] for call in stub_bin.invocations()] == [str(project)] * len(
            stub_bin.calls("bd")
        )


class TestTheCheapPath:
    """No subprocess where none is owed.

    `PostToolUse:Bash` fires for every shell call a session makes, so this is the
    property the whole `is_close_command` predicate exists for. It is asserted as
    "no `bd` process ran" rather than "no file was written", because the cost being
    avoided is the subprocess.
    """

    def test_a_command_that_merely_mentions_a_close_runs_no_subprocess(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        backlog()

        result = run_hook(SCRIPT, bash_payload(project, 'echo "bd close Proj-abc"'))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert stub_bin.calls("bd") == []
        assert episodes(project, accepted) == []

    def test_another_bd_subcommand_runs_no_subprocess(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        backlog()

        run_hook(SCRIPT, bash_payload(project, "bd list --all"))

        assert stub_bin.calls("bd") == []

    def test_a_tool_event_carrying_no_command_runs_no_subprocess(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # A `tool_input` that is present but has no usable `command` means "a tool
        # event that closed nothing", which is a different answer from the absent
        # `tool_input` of a `Stop`. Getting them the same way round matters most
        # here: a malformed tool payload must not be the one shape that bypasses
        # the predicate and buys a `bd list` on every shell call.
        payload = bash_payload(project, "bd close Proj-abc")
        payload["tool_input"] = {}

        run_hook(SCRIPT, payload)

        assert stub_bin.calls("bd") == []

    def test_a_non_string_command_runs_no_subprocess(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        payload = bash_payload(project, "unused")
        payload["tool_input"] = {"command": ["bd", "close", "Proj-abc"]}

        run_hook(SCRIPT, payload)

        assert stub_bin.calls("bd") == []

    def test_an_unbalanced_quote_is_not_a_reason_to_fail(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # A hand-typed command with a stray quote reaches this hook after the shell
        # has already dealt with it. `shlex` raises on it; the predicate returns
        # False rather than letting a tokeniser error become a hook failure.
        backlog()

        result = run_hook(SCRIPT, bash_payload(project, 'bd close Proj-abc --reason "oops'))

        assert result.returncode == 0
        assert result.stderr == ""
        assert stub_bin.calls("bd") == []


class TestTheStopContract:
    def test_a_stop_reconciles_with_no_command_to_inspect(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # The trigger that catches a close made from another terminal, by an editor,
        # or on another machine and arrived over Dolt sync. There is no command, so
        # the work is unconditional.
        backlog()

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert [record["issue_id"] for record in episodes(project, accepted)] == ["Proj-abc"]
        assert len(updates(stub_bin)) == 1

    def test_a_reentrant_stop_runs_no_subprocess(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # `stop_hook_active` is honoured for cost, not for correctness: this hook
        # emits nothing and so cannot cause the loop the flag exists to break. The
        # second pass would find every episode `unchanged`, having paid a `bd list`
        # to learn it.
        backlog()

        result = run_hook(SCRIPT, stop_payload(project, active=True))

        assert result.returncode == 0
        assert stub_bin.calls("bd") == []
        assert episodes(project, accepted) == []


class TestTheConsentGate:
    def test_an_unacknowledged_install_runs_no_bd_and_leaves_no_trace(
        self, run_hook, stub_bin, backlog, cfg, project
    ):
        # Deliberately without the `accepted` fixture. `Stop` fires every turn, so
        # an unacknowledged install would otherwise be running a subprocess in the
        # user's repository continuously without ever having been allowed to — a
        # stronger property than not writing a file, and the one that matters when
        # the repository is not the user's own.
        #
        # The trace assertion is on the state root's *existence*, not on the files
        # under it: `files_under` cannot see a leaked `mkdir`, and a directory
        # created before the gate is checked would pass a file-only assertion.
        backlog()

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert stub_bin.calls("bd") == []
        assert not os.path.exists(state_store.state_root())

    def test_a_governing_config_change_closes_the_gate_again(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # Acknowledgement is a fingerprint of the governing keys, and
        # `beads.write_metadata` is one of them. Flipping it after acknowledgement
        # is `config_changed`, not a silent switch to write-nothing — the remedy is
        # a re-acknowledgement, and the user is the one who has to make it.
        backlog()
        with open(hfit_config.config_path(), "w") as fh:
            json.dump({"beads": {"write_metadata": False}}, fh)

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stderr == ""
        assert stub_bin.calls("bd") == []


class TestTheSteadyState:
    def test_a_second_run_appends_nothing_and_writes_no_index_update(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # The overwhelmingly common case for a per-turn hook. Reconciliation is
        # idempotent discovery: the whole closed population is read every time, and
        # only a record that moved is appended or indexed.
        backlog()
        run_hook(SCRIPT, stop_payload(project))
        assert len(updates(stub_bin)) == 1

        # `reset()` clears the recorded calls *and* the rules, so the backlog has to
        # be reinstalled or the second read would find nothing and pass vacuously.
        stub_bin.reset()
        backlog()

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert updates(stub_bin) == []
        assert len(episodes(project, accepted)) == 1

    def test_a_moved_verdict_is_appended_rather_than_replacing_the_first_reading(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        backlog()
        run_hook(SCRIPT, stop_payload(project))

        stub_bin.reset()
        backlog(issue(close_reason="rejected: it does not"))

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        recorded = episodes(project, accepted)
        assert [record["verdict"] for record in recorded] == ["accepted", "rejected"]
        assert len(updates(stub_bin)) == 1


class TestAnUnreadableBacklog:
    """An environmental failure is not an empty population.

    `beads_read` keeps `no_database` and `bd_error` apart because one is a project
    that does not use beads and the other is a defect. What the hook owes is that
    neither becomes a *reading*: nothing is appended and nothing is indexed on the
    strength of a failed read, so a later run over a readable backlog still sees
    every episode as new.
    """

    def test_no_database_appends_nothing_and_indexes_nothing(
        self, run_hook, stub_bin, accepted, project
    ):
        stub_bin.on("bd", ["list"], rc=1, stderr="error: no beads database found\n")

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert episodes(project, accepted) == []
        assert updates(stub_bin) == []

    def test_an_absent_bd_appends_nothing_and_indexes_nothing(
        self, run_hook, stub_bin, accepted, project
    ):
        stub_bin.on("bd", ["list"], rc=127, stderr="bd: command not found\n")

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stderr == ""
        assert episodes(project, accepted) == []
        assert updates(stub_bin) == []

    def test_an_unparseable_answer_appends_nothing(
        self, run_hook, stub_bin, accepted, project
    ):
        stub_bin.on("bd", ["list"], stdout="not json at all")

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stderr == ""
        assert episodes(project, accepted) == []

    def test_a_readable_backlog_after_a_failed_read_is_still_all_new(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        stub_bin.on("bd", ["list"], rc=1, stderr="error: no beads database found\n")
        run_hook(SCRIPT, stop_payload(project))

        stub_bin.reset()
        backlog()

        run_hook(SCRIPT, stop_payload(project))

        assert len(episodes(project, accepted)) == 1
        assert len(updates(stub_bin)) == 1


class TestNoLeaks:
    def test_no_issue_text_reaches_the_ledger_or_any_bd_argv(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # Issue text is one field away in `bd` and this plugin owns none of it. A
        # title or close reason copied into either surface would be a stale
        # duplicate at best, and a credential someone pasted into an issue at worst
        # — replicated to every machine sharing the database in the second case.
        backlog(
            issue(
                title="token " + PLANTED_TOKEN,
                close_reason="accepted: with " + PLANTED_TOKEN,
            )
        )

        result = run_hook(SCRIPT, bash_payload(project, "bd close Proj-abc"))

        assert result.returncode == 0
        assert PLANTED_TOKEN not in raw_ledger(project, accepted)
        assert PLANTED_TOKEN not in result.stdout
        assert PLANTED_TOKEN not in result.stderr
        for call in stub_bin.calls("bd"):
            for argument in call:
                assert PLANTED_TOKEN not in argument

    def test_the_command_that_triggered_the_hook_is_not_stored(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # The command is read, tokenised and discarded. It arrives from an untrusted
        # payload and a shell line is exactly the place a secret gets typed.
        backlog()

        run_hook(
            SCRIPT,
            bash_payload(project, "bd close Proj-abc --reason 'accepted: " + PLANTED_TOKEN + "'"),
        )

        assert PLANTED_TOKEN not in raw_ledger(project, accepted)

    def test_a_hostile_issue_id_is_not_a_command(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # The ids reaching `bd update` come from a backlog that is hand-editable and
        # Dolt-synced. `shell=False` with an explicit argv is what makes a shell
        # metacharacter in one an inert string.
        backlog(issue(id="Proj-abc; touch pwned"))

        run_hook(SCRIPT, stop_payload(project))

        assert not os.path.exists(project / "pwned")
        written = updates(stub_bin)
        assert len(written) == 1
        # One argument, arriving whole. A shell would have made it two commands.
        assert "Proj-abc; touch pwned" in written[0]


class TestNoAbacusKeyIsEverWritten:
    def test_every_metadata_pair_is_prefixed_hfit(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        # The namespace boundary, asserted at the only boundary that can actually
        # observe it — the argv of the process that would do the damage. Two tools
        # writing one namespace means whichever ran last wins and neither knows it
        # lost.
        backlog()

        run_hook(SCRIPT, stop_payload(project))

        written = updates(stub_bin)
        assert written
        for call in written:
            pairs = [
                argument
                for index, argument in enumerate(call)
                if index and call[index - 1] == "--set-metadata"
            ]
            assert pairs
            for pair in pairs:
                assert pair.startswith("hfit_"), pair


class TestFailOpen:
    def test_corrupt_stdin_is_survived_silently(self, run_hook, stub_bin, project):
        result = run_hook(SCRIPT, raw_stdin="{not json at all")

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_empty_stdin_is_survived_silently(self, run_hook, stub_bin, project):
        result = run_hook(SCRIPT, raw_stdin="")

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_a_blocked_write_path_is_survived_silently(
        self, run_hook, backlog, accepted, project
    ):
        # The fault is planted *inside* the acknowledged state root rather than by
        # moving `$HFIT_STATE_DIR`: moving the root also moves `acknowledged.json`,
        # so the hook would refuse on consent and never reach a write.
        backlog()
        _block_the_projects_dir()

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_a_blocked_write_is_not_an_exception_even_under_debug(
        self, run_hook, backlog, accepted, project
    ):
        # The interesting half of the pair. `HFIT_DEBUG=1` re-raises whatever
        # `fail_open` caught, so silence here would be silence from a swallowed
        # exception if that were where it came from. It is not:
        # `state_store.append_jsonl` returns `False` rather than raising, and
        # `record_episode` turns that into a refusal — so a blocked write degrades
        # to *nothing recorded*, which the next run re-attempts.
        backlog()
        _block_the_projects_dir()

        result = run_hook(SCRIPT, stop_payload(project), env={"HFIT_DEBUG": "1"})

        assert result.returncode == 0
        assert result.stderr == ""

    def test_a_hung_bd_is_survived_within_the_budget(
        self, run_hook, stub_bin, accepted, project
    ):
        # Not a real hang — `beads_read.TIMEOUT_SECONDS` is 10s and a test that
        # waited it out would cost more than the whole suite. What is asserted is
        # the reason distinction reaching the hook: a timeout is an environmental
        # refusal like any other, so nothing is appended and nothing is indexed.
        stub_bin.on("bd", ["list"], rc=124, stderr="timed out\n")

        result = run_hook(SCRIPT, stop_payload(project))

        assert result.returncode == 0
        assert result.stderr == ""
        assert episodes(project, accepted) == []


class TestLatency:
    @pytest.mark.slow
    def test_the_cheap_path_is_genuinely_cheap(
        self, run_hook, stub_bin, backlog, accepted, project
    ):
        backlog()
        started = time.time()
        result = run_hook(SCRIPT, bash_payload(project, 'echo "bd close Proj-abc"'))
        elapsed = time.time() - started

        # A crashed subprocess is also fast, and so is one that did no work because
        # it could not start. Asserting the exit code and the absence of a `bd` call
        # keeps the timing assertion from passing for either reason.
        assert result.returncode == 0
        assert stub_bin.calls("bd") == []
        assert elapsed < 5.0

    @pytest.mark.slow
    def test_a_real_reconcile_finishes_far_inside_its_timeout(
        self, run_hook, backlog, accepted, project
    ):
        backlog()
        started = time.time()
        result = run_hook(SCRIPT, stop_payload(project))
        elapsed = time.time() - started

        assert result.returncode == 0
        assert len(episodes(project, accepted)) == 1
        assert elapsed < 5.0


def test_the_reasons_this_hook_can_receive_are_the_declared_ones(accepted):
    """A guard on the vocabulary the hook branches on, asserted where it is cheap.

    `reconcile` returns `ok: false` with a reason from `REFUSALS`, and the hook's
    only branch is on `ok`. If a reason were added that the hook should *not*
    ignore, this is the assertion that would have to be rewritten to allow it —
    which is the point of pinning it here rather than trusting the branch.
    """
    import reconcile as reconcile_lib

    assert set(beads_read.REASONS) <= set(reconcile_lib.REFUSALS)
    assert "not_acknowledged" in reconcile_lib.REFUSALS
    assert "config_changed" in reconcile_lib.REFUSALS
