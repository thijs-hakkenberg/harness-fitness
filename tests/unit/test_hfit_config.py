"""Configuration defaults, merge order and the governing subset.

The defaults are asserted individually rather than as a blob, because several of
them are load-bearing decisions rather than preferences: ``min_verdict_coverage``
is the threshold below which the most important measure refuses to report,
``mad_min_n`` and ``min_episodes_per_digest`` must agree or a group can pass the
comparison gate while being too small for the outlier rule, and ``ledger.in_repo``
defaults to false because writing into a repository the user may not own is
something to be asked for, not fallen into.

``governing()`` exists because consent must be re-sought when *what gets written
where* changes, and must not be re-sought when a threshold is tuned.
"""

import json

import pytest

import hfit_config


class TestDefaults:
    def test_verdict_coverage_threshold(self):
        assert hfit_config.DEFAULTS["min_verdict_coverage"] == 0.6

    def test_minimum_episodes_matches_the_outlier_rule_minimum(self):
        # If these diverge, a digest group can clear the comparison gate while
        # being too small for MAD to run — a silently unguarded delta.
        assert hfit_config.DEFAULTS["min_episodes_per_digest"] == 5
        assert hfit_config.DEFAULTS["mad_min_n"] == 5

    def test_mad_threshold_is_three(self):
        assert hfit_config.DEFAULTS["mad_threshold"] == 3.0

    def test_ledger_is_not_in_the_repo_by_default(self):
        assert hfit_config.DEFAULTS["ledger"]["in_repo"] is False

    def test_otel_tail_is_bounded(self):
        # The live log is 68 MB with rotated 100 MB siblings. An unbounded read
        # is not an option on a hot path.
        assert hfit_config.DEFAULTS["otel"]["tail_bytes"] == 4 * 1024 * 1024

    def test_events_retention_is_set(self):
        assert hfit_config.DEFAULTS["events_retention_days"] == 30

    def test_inference_is_off_by_default(self):
        # An LLM call must never happen because someone installed a plugin.
        assert hfit_config.DEFAULTS["inference"]["enabled"] is False

    def test_feedforward_hook_events_are_the_four_preventive_ones(self):
        # ADR-005: a PreToolUse deny prevents rather than detects, so it counts
        # as a feedforward catch. Keeping the list in config makes that
        # judgement inspectable and reversible.
        assert hfit_config.DEFAULTS["ff_hook_events"] == [
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PermissionRequest",
        ]

    def test_defaults_are_not_mutable_across_loads(self, isolated_home):
        first = hfit_config.load()
        first["min_verdict_coverage"] = 0.99
        first["ledger"]["in_repo"] = True
        second = hfit_config.load()
        assert second["min_verdict_coverage"] == 0.6
        assert second["ledger"]["in_repo"] is False


class TestLoad:
    def _write(self, isolated_home, obj):
        root = isolated_home / ".claude" / "harness-fitness"
        root.mkdir(parents=True, exist_ok=True)
        (root / "config.json").write_text(json.dumps(obj))

    def test_with_no_file_the_defaults_are_returned(self, isolated_home):
        assert hfit_config.load()["min_verdict_coverage"] == 0.6

    def test_a_scalar_override_wins(self, isolated_home):
        self._write(isolated_home, {"min_verdict_coverage": 0.9})
        assert hfit_config.load()["min_verdict_coverage"] == 0.9

    def test_a_nested_override_merges_rather_than_replaces(self, isolated_home):
        # A shallow update would silently drop `otel.tail_bytes` here.
        self._write(isolated_home, {"otel": {"enabled": False}})
        cfg = hfit_config.load()
        assert cfg["otel"]["enabled"] is False
        assert cfg["otel"]["tail_bytes"] == 4 * 1024 * 1024

    def test_an_unknown_key_is_preserved_not_dropped(self, isolated_home):
        # Forward compatibility: a newer version's key must survive a downgrade.
        self._write(isolated_home, {"future_thing": 7})
        assert hfit_config.load()["future_thing"] == 7

    def test_corrupt_config_yields_defaults_and_a_flag_not_an_exception(
        self, isolated_home
    ):
        root = isolated_home / ".claude" / "harness-fitness"
        root.mkdir(parents=True)
        (root / "config.json").write_text("{not json")
        cfg = hfit_config.load()
        assert cfg["min_verdict_coverage"] == 0.6
        assert "config-unreadable" in cfg["flags"]

    def test_a_non_object_config_yields_defaults_and_a_flag(self, isolated_home):
        self._write(isolated_home, ["not", "an", "object"])
        cfg = hfit_config.load()
        assert cfg["min_verdict_coverage"] == 0.6
        assert "config-unreadable" in cfg["flags"]

    def test_flags_is_empty_on_a_clean_load(self, isolated_home):
        assert hfit_config.load()["flags"] == []

    def test_hfit_state_dir_relocates_the_config_file(self, tmp_path, monkeypatch):
        root = tmp_path / "elsewhere"
        root.mkdir()
        (root / "config.json").write_text(json.dumps({"min_verdict_coverage": 0.42}))
        monkeypatch.setenv("HFIT_STATE_DIR", str(root))
        assert hfit_config.load()["min_verdict_coverage"] == 0.42


class TestGoverning:
    def test_contains_what_gets_written_and_where(self, isolated_home):
        gov = hfit_config.governing(hfit_config.load())
        assert set(gov) == {
            "ledger.in_repo",
            "beads.write_metadata",
            "otel.enabled",
            "inference.enabled",
            "events_retention_days",
        }

    def test_excludes_thresholds(self, isolated_home):
        # Tuning a threshold changes a number, not what is collected. Asking for
        # consent again would train the user to click through it.
        gov = hfit_config.governing(hfit_config.load())
        assert "min_verdict_coverage" not in gov
        assert "mad_threshold" not in gov

    def test_is_flat_so_it_canonicalises_stably(self, isolated_home):
        gov = hfit_config.governing(hfit_config.load())
        assert all(not isinstance(v, (dict, list)) for v in gov.values())

    def test_reflects_an_override(self, isolated_home):
        root = isolated_home / ".claude" / "harness-fitness"
        root.mkdir(parents=True)
        (root / "config.json").write_text(json.dumps({"ledger": {"in_repo": True}}))
        assert hfit_config.governing(hfit_config.load())["ledger.in_repo"] is True

    def test_a_missing_governing_key_falls_back_to_the_default(self):
        # governing() is called on whatever dict it is handed, including a
        # partial one recovered from an older acknowledgement.
        gov = hfit_config.governing({})
        assert gov["ledger.in_repo"] is False
        assert gov["otel.enabled"] is True
