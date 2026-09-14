"""`fitness.py --json` — the read surface, driven as a real subprocess.

At 0.1.0 this reports composition only: there are no episodes yet, so three of the
four measures have no inputs and the fourth has no denominator. That makes the
*shape* of a report with nothing to report the entire subject of this file, and it
is the more interesting half of the contract — a reader meeting this plugin for the
first time meets it in exactly this state.

Two disciplines are asserted throughout, both inherited as interface contracts
rather than as code:

- **`ok` comes first, and a failed read carries no measure keys at all.** Not
  zeroed, not empty — absent. A caller that finds `measures` present has been told
  the read succeeded; one that finds it missing cannot mistake a failure for a
  result.
- **Unknown is never zero.** Where a number cannot be computed yet, the field is
  `null` and a `gaps[]` entry says why. `episodes_seen: 0` would assert we counted
  and found none, when in truth the counter does not exist until 0.2.0.

And one this file adds: **a read surface writes nothing.** A reporting command that
creates state changes the thing it reports on, and at 0.1.0 it would also create it
*before* consent had been given.
"""

import json
import os

import pytest

import consent
import hfit_config
import ledger
import state_store
from conftest import entries_under

SCRIPT = "fitness.py"
SNAPSHOT = "snapshot_composition.py"

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


def payload(cwd):
    return {"cwd": str(cwd), "hook_event_name": "SessionStart", "source": "startup"}


def report(run_cli, cwd, args=("--json",), env=None):
    """Run the CLI and parse its stdout, failing loudly on either count."""
    result = run_cli(SCRIPT, args, cwd=cwd, env=env)
    assert result.returncode == 0, result.stderr
    # Parsed rather than pattern-matched: a skill does `json.loads` on this, so a
    # trailing log line or a bare traceback is a broken contract even when the
    # exit code is 0.
    return json.loads(result.stdout)


def gap_kinds(rep):
    return [g["kind"] for g in rep.get("gaps", [])]


@pytest.fixture
def recorded(run_hook, settings_tree, accepted):
    """A project with one composition on record, the way a real session leaves it.

    Built by running the real `SessionStart` hook rather than by writing a record
    directly: the CLI's whole job is to read what that hook wrote, and a fixture
    that fabricated the file would let the two drift apart while both suites stay
    green.
    """
    tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
    run_hook(SNAPSHOT, payload(tree.project))
    return tree


class TestTheEnvelope:
    def test_it_exits_zero_and_prints_one_json_object(self, run_cli, recorded):
        rep = report(run_cli, recorded.project)

        assert rep["ok"] is True

    def test_it_names_the_version_that_produced_it(self, run_cli, recorded):
        # A report gets pasted into an issue and read weeks later. Without this the
        # reader cannot tell whether a `null` measure means "not implemented at this
        # version" or "unavailable on that machine" — and at 0.1.0 almost every
        # measure is the former.
        rep = report(run_cli, recorded.project)

        assert rep["plugin_version"]

    def test_it_reports_the_project_it_read(self, run_cli, recorded):
        # The single most likely first-run confusion is a report about a different
        # directory than the user thinks they are in, and the ledger is per-project.
        rep = report(run_cli, recorded.project)

        assert rep["project"] == str(recorded.project)


class TestTheCompositionItReports:
    def test_it_reports_the_digest_the_hook_recorded(self, run_cli, recorded, accepted):
        rep = report(run_cli, recorded.project)

        assert rep["current"]["digest"] == ledger.current_digest(
            str(recorded.project), accepted
        )

    def test_it_reports_the_profile_beside_the_digest(self, run_cli, recorded):
        # The digest alone is unreadable by a human. The profile is what makes
        # "B added two FB·COMP sensors" a sentence a report can write.
        rep = report(run_cli, recorded.project)

        assert rep["current"]["profile"]["fb_comp"] >= 1

    def test_it_carries_both_halves_of_the_pin(self, run_cli, recorded):
        # ADR-007: a comparison across a differing `env_hash` or a differing
        # `model_basis` must be refused, and a report that shows the hash alone
        # invites a reader to make the comparison the tool would refuse.
        rep = report(run_cli, recorded.project)

        assert len(rep["current"]["env_hash"]) == 64
        assert rep["current"]["model_basis"] in ("observed", "declared", "unknown")

    def test_it_reports_the_changes_log(self, run_cli, recorded):
        rep = report(run_cli, recorded.project)

        assert len(rep["changes"]) == 1
        assert rep["changes"][0]["kind"] == "baseline"

    def test_a_moved_harness_shows_up_as_a_transition(
        self, run_cli, run_hook, settings_tree, recorded
    ):
        settings_tree(user=ENABLED_BOTH, plugins=ONE_PLUGIN)
        run_hook(SNAPSHOT, payload(recorded.project))

        rep = report(run_cli, recorded.project)

        assert len(rep["changes"]) == 2
        assert rep["changes"][-1]["added"] == ["plugin:other@abacus"]


class TestUnknownIsNeverZero:
    def test_the_measures_are_null_at_this_version_not_empty(self, run_cli, recorded):
        # `{}` would say the measure layer ran and produced nothing. It does not
        # exist until 0.3.0, and a report must not imply a computation it never
        # attempted.
        rep = report(run_cli, recorded.project)

        assert rep["measures"] is None

    def test_the_episode_count_is_null_rather_than_zero(self, run_cli, recorded):
        # The distinction that matters: `0` asserts we looked at the episode store
        # and found it empty, which would be a claim about the user's work. There is
        # no episode store yet.
        rep = report(run_cli, recorded.project)

        assert rep["episodes_seen"] is None

    def test_a_gap_says_why_the_measures_are_absent(self, run_cli, recorded):
        rep = report(run_cli, recorded.project)

        assert "measures-unavailable" in gap_kinds(rep)

    def test_every_gap_carries_a_human_readable_reason(self, run_cli, recorded):
        # A gap kind is for a skill to branch on; the reason is what a user reads.
        # A kind with no reason makes the skill invent one, which is the failure
        # mode this whole surface exists to prevent.
        rep = report(run_cli, recorded.project)

        assert rep["gaps"] != []
        for gap in rep["gaps"]:
            assert gap["reason"].strip() != "", gap


class TestTheFailedRead:
    def test_an_unacknowledged_install_reports_not_ok(self, run_cli, tmp_path, cfg):
        rep = report(run_cli, tmp_path)

        assert rep["ok"] is False
        assert "not-acknowledged" in gap_kinds(rep)

    def test_a_failed_read_carries_no_measure_keys_at_all(
        self, run_cli, tmp_path, cfg
    ):
        # Absent, not null and not zero. This is the one shape that makes it
        # impossible for a caller to mistake a failure for an empty result — a
        # `measures: null` on a failed read would be indistinguishable from the
        # legitimate `null` of a successful read at this version.
        rep = report(run_cli, tmp_path)

        assert "measures" not in rep
        assert "episodes_seen" not in rep
        assert "current" not in rep
        # `changes` belongs in the same list: an empty change log is a real and
        # legitimate state of a successful read, so emitting `[]` here would say
        # "this project's harness has never moved" when we never looked.
        assert "changes" not in rep

    def test_the_unacknowledged_reason_says_what_to_run(self, run_cli, tmp_path, cfg):
        # The highest-value message in the plugin. Without it the first-run
        # experience is `/hfit:composition` printing nothing, which reads as a
        # broken plugin rather than as an unmade decision (ADR-014).
        rep = report(run_cli, tmp_path)

        reason = [g["reason"] for g in rep["gaps"] if g["kind"] == "not-acknowledged"][0]
        assert "acknowledge" in reason.lower()

    def test_a_project_with_no_composition_reports_ok_with_a_gap(
        self, run_cli, accepted, tmp_path
    ):
        # Acknowledged but never visited: not a failure. The plugin is installed and
        # working, and the honest answer is "no session has started here yet".
        rep = report(run_cli, tmp_path)

        assert rep["ok"] is True
        assert rep["current"] is None
        assert "no-composition-recorded" in gap_kinds(rep)

    def test_a_digest_whose_record_is_gone_keeps_the_digest_and_nulls_the_rest(
        self, run_cli, recorded, accepted
    ):
        # The change log and the composition files can disagree — a half-restored
        # backup, a manual delete, a partial sync. The digest is still real, so it
        # is reported; everything that would have come out of the record file is
        # `null`, because a `profile` of `{}` would describe a harness with no
        # components rather than a record we could not read.
        digest = ledger.current_digest(str(recorded.project), accepted)
        os.unlink(ledger.composition_path(str(recorded.project), digest, accepted))

        rep = report(run_cli, recorded.project)

        assert rep["ok"] is True
        assert rep["current"]["digest"] == digest
        assert rep["current"]["profile"] is None
        assert "unresolved-composition" in gap_kinds(rep)


class TestItOnlyReads:
    def test_it_writes_nothing_at_all(self, run_cli, recorded, accepted):
        # A reporting command that creates state has changed the thing it reports
        # on. Asserted across the whole state root rather than the project ledger,
        # so a stray session file also fails — and with `entries_under` rather than
        # `files_under`, so does a directory created and left empty.
        before = entries_under(state_store.state_root())

        report(run_cli, recorded.project)

        assert entries_under(state_store.state_root()) == before

    def test_it_writes_nothing_when_unacknowledged_either(self, run_cli, tmp_path, cfg):
        # The stricter half, and the one that has to be asserted on existence rather
        # than on a listing: before consent there must be no trace at all, and an
        # empty directory is a trace (ADR-014). A `files_under(...) == []` here would
        # be satisfied by a leaked `mkdir` of the very tree it is guarding — which is
        # exactly what a mutation proved, so this now names the path directly.
        report(run_cli, tmp_path)

        assert not os.path.exists(state_store.state_root())

    def test_it_does_not_block_on_stdin(self, run_cli, recorded):
        # `run_cli` closes stdin, so a surface that reads it returns immediately
        # here — but under a skill, stdin is a pipe that never closes and the same
        # code hangs until the timeout. Asserting a fast exit is the closest a test
        # can get to that without hanging the suite.
        result = run_cli(SCRIPT, ("--json",), cwd=recorded.project, timeout=20)

        assert result.returncode == 0


class TestTheCliContract:
    def test_an_unknown_flag_is_refused_loudly(self, run_cli, recorded):
        # The one place this surface is allowed to be noisy: it is a human at a
        # terminal, not a hook in a session, and silently ignoring `--sinse` would
        # print a full report the user reads as an answer to a question they did
        # not ask.
        result = run_cli(SCRIPT, ("--json", "--not-a-flag"), cwd=recorded.project)

        assert result.returncode != 0
        # Naming the flag back, so this cannot pass for the wrong reason — an
        # absent script and a broken import both exit non-zero too.
        assert "--not-a-flag" in result.stderr

    def test_it_refuses_to_print_anything_but_json_for_now(self, run_cli, recorded):
        # `--json` is mandatory at 0.1.0. A default human format would be a second
        # output contract to keep in step with the first, and every consumer here
        # is a skill.
        result = run_cli(SCRIPT, (), cwd=recorded.project)

        assert result.returncode != 0
        assert "--json" in (result.stderr + result.stdout)
