"""Consent gates every write.

This plugin reads settings files, enumerates installed plugins and inspects hook
configuration. None of that is written anywhere until the user has run
``acknowledge.py --accept``, and the acknowledgement is fingerprinted against the
*governing* configuration so that changing what gets collected re-asks, while
tuning a threshold does not.

The load-bearing test in this file is the last one: on a fresh, unacknowledged
install, resolving configuration and asking whether we may write must leave the
state directory entirely absent. A module that creates its own root eagerly
passes every other test here and violates the promise in the README.
"""

import json

import pytest

import consent
import hfit_config
import state_store

from conftest import files_under


@pytest.fixture
def cfg(isolated_home):
    return hfit_config.load()


class TestFingerprint:
    def test_is_twelve_hex(self, cfg):
        fp = consent.fingerprint(cfg)
        assert len(fp) == 12
        assert all(c in "0123456789abcdef" for c in fp)

    def test_is_deterministic(self, cfg):
        assert consent.fingerprint(cfg) == consent.fingerprint(cfg)

    def test_ignores_key_insertion_order(self, cfg):
        reversed_cfg = dict(reversed(list(cfg.items())))
        assert consent.fingerprint(reversed_cfg) == consent.fingerprint(cfg)

    def test_moves_when_a_governing_value_changes(self, cfg):
        changed = json.loads(json.dumps(cfg))
        changed["ledger"]["in_repo"] = True
        assert consent.fingerprint(changed) != consent.fingerprint(cfg)

    def test_does_not_move_when_a_threshold_changes(self, cfg):
        changed = json.loads(json.dumps(cfg))
        changed["min_verdict_coverage"] = 0.95
        assert consent.fingerprint(changed) == consent.fingerprint(cfg)


class TestUnacknowledged:
    def test_is_acknowledged_is_false(self, cfg):
        assert consent.is_acknowledged(cfg) is False

    def test_require_consent_refuses_with_a_reason(self, cfg):
        allowed, reason = consent.require_consent(cfg)
        assert allowed is False
        assert reason == "not_acknowledged"

    def test_status_reports_not_accepted(self, cfg):
        st = consent.status(cfg)
        assert st["accepted"] is False
        assert st["accepted_at"] is None
        assert st["fingerprint"] == consent.fingerprint(cfg)


class TestRecordAcknowledgement:
    def test_writes_the_acknowledgement_file(self, cfg, isolated_home, frozen_now):
        frozen_now("2026-09-14T12:00:00Z")
        consent.record_acknowledgement(cfg)
        path = isolated_home / ".claude" / "harness-fitness" / "acknowledged.json"
        assert json.loads(path.read_text()) == {
            "schema": 1,
            "accepted_at": "2026-09-14T12:00:00Z",
            "fingerprint": consent.fingerprint(cfg),
        }

    def test_after_accepting_writes_are_allowed(self, cfg):
        consent.record_acknowledgement(cfg)
        assert consent.is_acknowledged(cfg) is True
        assert consent.require_consent(cfg) == (True, None)

    def test_the_acknowledgement_file_is_0600(self, cfg, isolated_home):
        import os
        import stat

        consent.record_acknowledgement(cfg)
        path = isolated_home / ".claude" / "harness-fitness" / "acknowledged.json"
        assert stat.S_IMODE(os.stat(str(path)).st_mode) == 0o600

    def test_accepting_twice_is_idempotent(self, cfg):
        consent.record_acknowledgement(cfg)
        first = consent.status(cfg)["accepted_at"]
        consent.record_acknowledgement(cfg)
        assert consent.status(cfg)["accepted_at"] == first


class TestInvalidation:
    def _accept_then_reload(self, isolated_home, override):
        consent.record_acknowledgement(hfit_config.load())
        root = isolated_home / ".claude" / "harness-fitness"
        (root / "config.json").write_text(json.dumps(override))
        return hfit_config.load()

    def test_a_governing_change_invalidates_the_acknowledgement(self, isolated_home):
        cfg = self._accept_then_reload(isolated_home, {"ledger": {"in_repo": True}})
        allowed, reason = consent.require_consent(cfg)
        assert allowed is False
        assert reason == "config_changed"

    def test_a_non_governing_change_does_not_invalidate(self, isolated_home):
        cfg = self._accept_then_reload(isolated_home, {"min_verdict_coverage": 0.95})
        assert consent.require_consent(cfg) == (True, None)

    def test_re_accepting_after_a_governing_change_restores_writes(self, isolated_home):
        cfg = self._accept_then_reload(isolated_home, {"ledger": {"in_repo": True}})
        consent.record_acknowledgement(cfg)
        assert consent.require_consent(cfg) == (True, None)

    def test_a_corrupt_acknowledgement_refuses_rather_than_raising(
        self, cfg, isolated_home
    ):
        root = isolated_home / ".claude" / "harness-fitness"
        root.mkdir(parents=True, exist_ok=True)
        (root / "acknowledged.json").write_text("{not json")
        allowed, reason = consent.require_consent(cfg)
        assert allowed is False
        assert reason == "not_acknowledged"


class TestRevoke:
    def test_removes_the_acknowledgement(self, cfg):
        consent.record_acknowledgement(cfg)
        assert consent.revoke() is True
        assert consent.is_acknowledged(cfg) is False

    def test_revoking_when_never_accepted_is_harmless(self, cfg):
        assert consent.revoke() is False


class TestNothingIsWrittenBeforeConsent:
    def test_resolving_config_and_asking_permission_writes_no_files(self, isolated_home):
        # The promise in the README, asserted. A module that creates its own
        # state root at import or on first read breaks this and nothing else.
        cfg = hfit_config.load()
        consent.is_acknowledged(cfg)
        consent.require_consent(cfg)
        consent.status(cfg)
        state_store.state_root()
        state_store.project_dir("/tmp/proj")

        assert files_under(state_store.state_root()) == []
        assert not (isolated_home / ".claude" / "harness-fitness").exists()
