"""`acknowledge.py` — the only way consent is ever given, driven as a subprocess.

Every write in this plugin is gated on an acknowledgement (ADR-014), and this script
is the sole thing that can produce one. That makes it the shortest single point of
total failure in the plugin: without it the gate is closed permanently, every hook
records nothing forever, and the only symptom is a report saying `not-acknowledged`
and naming a command that does not exist. `consent.py`'s docstring names
`acknowledge.py --accept`, `fitness.py`'s refusal message names it, and
`skills/harness-composition/SKILL.md` tells the user to run whatever the refusal
names. Three surfaces point here.

Two properties carry more weight than the rest:

- **`--show` writes nothing.** The README's promise is that nothing is recorded before
  consent, and the command a cautious user runs *first*, to find out what this thing
  would do, is the one place that promise is easiest to break by accident — resolving
  a path with `makedirs` on the way to reading it. Asserted on existence, because an
  empty directory is a trace too.
- **Consent is never implied.** A bare invocation must not accept. A script where the
  default action is "agree" has collected a keystroke, not a decision, and the whole
  gate reduces to whether the user happened to type a flag.
"""

import json
import os

import pytest

import consent
import hfit_config
import state_store

SCRIPT = "acknowledge.py"


@pytest.fixture
def cfg():
    return hfit_config.load()


def _stored():
    return state_store.read_json(consent.ack_path())


class TestShowingBeforeAccepting:
    def test_it_reports_not_accepted_and_exits_zero(self, run_cli, cfg):
        result = run_cli(SCRIPT, ["--show"])

        assert result.returncode == 0
        assert result.stderr == ""
        assert "not" in result.stdout.lower()
        assert not consent.is_acknowledged(cfg)

    def test_it_writes_absolutely_nothing(self, run_cli):
        # The promise the README makes, at the one moment a user is most likely to
        # be checking whether it holds. Existence, not emptiness: a `makedirs` on
        # the way to a read leaves a directory that no file listing can see.
        run_cli(SCRIPT, ["--show"])

        assert not os.path.exists(state_store.state_root())

    def test_it_says_where_and_what_would_be_recorded(self, run_cli):
        # Informed consent is a phrase until the notice names the directory and says
        # that nothing leaves the machine. A user cannot consent to "telemetry" they
        # have to read the source to understand.
        result = run_cli(SCRIPT, ["--show"])

        assert state_store.state_root() in result.stdout
        assert "harness-fitness" in result.stdout


class TestAccepting:
    def test_it_records_consent_and_nothing_else(self, run_cli, cfg):
        result = run_cli(SCRIPT, ["--accept"])

        assert result.returncode == 0
        assert result.stderr == ""
        assert consent.is_acknowledged(cfg)
        # One file. A script that also seeded a ledger, a config or a session file
        # would be writing about work the user has not done yet.
        from conftest import files_under

        assert files_under(state_store.state_root()) == [consent.ACK_FILENAME]

    def test_the_fingerprint_matches_the_governing_config(self, run_cli, cfg):
        run_cli(SCRIPT, ["--accept"])

        assert _stored()["fingerprint"] == consent.fingerprint(cfg)

    def test_accepting_twice_keeps_the_original_timestamp(self, run_cli):
        # The record has to keep saying when the user actually agreed. Re-running the
        # command — which a user will, to check it worked — must not rewrite history
        # into a consent given just now.
        run_cli(SCRIPT, ["--accept"], env={"HFIT_NOW": "2026-01-01T00:00:00Z"})
        first = _stored()["accepted_at"]

        run_cli(SCRIPT, ["--accept"], env={"HFIT_NOW": "2026-06-06T12:00:00Z"})

        assert _stored()["accepted_at"] == first

    def test_it_confirms_in_words_a_person_can_act_on(self, run_cli):
        result = run_cli(SCRIPT, ["--accept"])

        assert result.stdout.strip() != ""


class TestConsentIsNeverImplied:
    def test_a_bare_invocation_does_not_accept(self, run_cli, cfg):
        # The assertion this file exists for. A default of "accept" turns the gate
        # into a formality: the user typed a command to find out what it does and
        # agreed to everything by doing so.
        result = run_cli(SCRIPT, [])

        assert result.returncode != 0
        assert not consent.is_acknowledged(cfg)
        assert not os.path.exists(state_store.state_root())

    def test_an_unknown_flag_does_not_accept(self, run_cli, cfg):
        # Unlike a hook, this complains — silently ignoring `--acccept` and printing
        # a friendly line would leave the user believing they had consented.
        result = run_cli(SCRIPT, ["--acccept"])

        assert result.returncode != 0
        assert result.stderr != ""
        assert not consent.is_acknowledged(cfg)


class TestAStaleAcknowledgement:
    def test_a_changed_governing_config_reads_as_not_accepted(self, run_cli, cfg):
        run_cli(SCRIPT, ["--accept"])
        assert consent.is_acknowledged(cfg)

        state_store.write_json_atomic(
            hfit_config.config_path(), {"ledger": {"in_repo": True}}
        )

        result = run_cli(SCRIPT, ["--show"])
        assert result.returncode == 0
        assert "not" in result.stdout.lower()
        assert not consent.is_acknowledged(hfit_config.load())

    def test_re_accepting_after_a_change_restores_consent(self, run_cli):
        run_cli(SCRIPT, ["--accept"])
        state_store.write_json_atomic(
            hfit_config.config_path(), {"ledger": {"in_repo": True}}
        )

        run_cli(SCRIPT, ["--accept"])

        assert consent.is_acknowledged(hfit_config.load())


class TestRevoking:
    def test_it_removes_the_acknowledgement(self, run_cli, cfg):
        run_cli(SCRIPT, ["--accept"])

        result = run_cli(SCRIPT, ["--revoke"])

        assert result.returncode == 0
        assert result.stderr == ""
        assert not consent.is_acknowledged(cfg)

    def test_revoking_when_nothing_was_accepted_is_not_an_error(self, run_cli):
        # Withdrawing consent must never fail. A non-zero exit here reads as "the
        # revoke did not work", which is the one message that must not be wrong.
        result = run_cli(SCRIPT, ["--revoke"])

        assert result.returncode == 0
        assert result.stderr == ""

    def test_it_does_not_delete_anything_else(self, run_cli):
        run_cli(SCRIPT, ["--accept"])
        state_store.write_json_atomic(
            hfit_config.config_path(), {"ledger": {"in_repo": False}}
        )

        run_cli(SCRIPT, ["--revoke"])

        # Revoking consent stops future writing; it is not a request to destroy the
        # user's configuration or the history they already agreed to record.
        assert state_store.read_json(hfit_config.config_path()) is not None


class TestTheMachineReadableForm:
    def test_show_json_emits_the_status_object(self, run_cli, cfg):
        result = run_cli(SCRIPT, ["--show", "--json"])

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["accepted"] is False
        assert payload["accepted_at"] is None
        assert payload["fingerprint"] == consent.fingerprint(cfg)

    def test_show_json_is_only_the_object(self, run_cli):
        # A skill parses this. A banner or a friendly preamble on the same stream
        # breaks `json.loads` and there is no second output channel to lose it in.
        run_cli(SCRIPT, ["--accept"])

        result = run_cli(SCRIPT, ["--show", "--json"])

        payload = json.loads(result.stdout)
        assert payload["accepted"] is True
        assert result.stderr == ""
