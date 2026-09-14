"""Where state lives and how it is written.

Two properties carry the weight here. Writes are atomic and mode-restricted,
because a hook can be killed mid-write at any moment and a half-written ledger
must not be indistinguishable from a real one. And reads are lossy-tolerant: a
corrupt JSONL line is skipped, never raised, because the alternative is that one
bad line permanently disables every measure.

The project slug is deliberately not clever. Claude Code's own transform is
observable across 99 real directories only up to a point: every non-alphanumeric
character becomes a hyphen, but no real repository path contains two adjacent
non-alphanumerics, so whether runs collapse is *not determinable* from evidence.
Rather than guess, ``project_slug`` implements the simpler rule (character-wise,
no collapsing) and ``transcript_dir_for`` resolves against the filesystem,
trying both candidates and returning ``None`` when neither exists.
"""

import json
import os
import stat

import pytest

import state_store


class TestStateRoot:
    def test_defaults_to_claude_harness_fitness_under_home(self, isolated_home):
        assert state_store.state_root() == str(
            isolated_home / ".claude" / "harness-fitness"
        )

    def test_hfit_state_dir_overrides_the_default(self, tmp_path, monkeypatch):
        override = tmp_path / "elsewhere"
        monkeypatch.setenv("HFIT_STATE_DIR", str(override))
        assert state_store.state_root() == str(override)

    def test_state_root_does_not_create_anything(self, isolated_home):
        # Resolving a path must not be a write. Nothing is written before consent.
        state_store.state_root()
        assert not (isolated_home / ".claude" / "harness-fitness").exists()


class TestProjectSlug:
    @pytest.mark.parametrize(
        "cwd,expected",
        [
            ("/Users/hakketh/projects/repos", "-Users-hakketh-projects-repos"),
            (
                "/Users/hakketh/projects/repos/App.DigitalTwin",
                "-Users-hakketh-projects-repos-App-DigitalTwin",
            ),
            (
                "/Users/hakketh/projects/repos/ClaudeCode.AccessRequest",
                "-Users-hakketh-projects-repos-ClaudeCode-AccessRequest",
            ),
        ],
    )
    def test_matches_the_transform_observed_on_real_directories(self, cwd, expected):
        assert state_store.project_slug(cwd) == expected

    def test_underscores_become_hyphens_too(self):
        assert state_store.project_slug("/tmp/my_project") == "-tmp-my-project"

    def test_adjacent_separators_are_not_collapsed(self):
        # Deliberate: no observed directory disambiguates this, so the simpler
        # rule wins and `transcript_dir_for` does the disambiguating on disk.
        assert state_store.project_slug("/tmp/a_-b") == "-tmp-a--b"

    def test_a_trailing_separator_does_not_change_the_slug(self):
        assert state_store.project_slug("/tmp/proj/") == state_store.project_slug(
            "/tmp/proj"
        )

    def test_is_stable_across_calls(self):
        assert state_store.project_slug("/tmp/proj") == state_store.project_slug(
            "/tmp/proj"
        )


class TestTranscriptDirFor:
    def test_finds_the_character_wise_variant(self, isolated_home):
        d = isolated_home / ".claude" / "projects" / "-tmp-a--b"
        d.mkdir(parents=True)
        assert state_store.transcript_dir_for("/tmp/a_-b") == str(d)

    def test_falls_back_to_the_collapsed_variant_when_that_is_what_exists(
        self, isolated_home
    ):
        d = isolated_home / ".claude" / "projects" / "-tmp-a-b"
        d.mkdir(parents=True)
        assert state_store.transcript_dir_for("/tmp/a_-b") == str(d)

    def test_returns_none_rather_than_a_nonexistent_path(self, isolated_home):
        # Unknown is never a guess. A caller must be able to flag
        # `transcript-unavailable` instead of reading an empty directory.
        assert state_store.transcript_dir_for("/tmp/never-opened") is None


class TestProjectDir:
    def test_is_the_slug_under_projects_in_the_state_root(self, isolated_home):
        got = state_store.project_dir("/tmp/proj")
        assert got == str(
            isolated_home
            / ".claude"
            / "harness-fitness"
            / "projects"
            / "-tmp-proj"
        )


class TestWriteJsonAtomic:
    def test_writes_readable_json(self, tmp_path):
        target = tmp_path / "a" / "b" / "rec.json"
        assert state_store.write_json_atomic(str(target), {"k": 1}) is True
        assert json.loads(target.read_text()) == {"k": 1}

    def test_creates_parent_directories_at_0700(self, tmp_path):
        target = tmp_path / "a" / "b" / "rec.json"
        state_store.write_json_atomic(str(target), {"k": 1})
        assert stat.S_IMODE(os.stat(str(target.parent)).st_mode) == 0o700

    def test_the_file_is_0600(self, tmp_path):
        target = tmp_path / "rec.json"
        state_store.write_json_atomic(str(target), {"k": 1})
        assert stat.S_IMODE(os.stat(str(target)).st_mode) == 0o600

    def test_leaves_no_temporary_residue(self, tmp_path):
        # Its own directory, because the autouse isolation fixtures also live
        # under tmp_path and would otherwise read as residue.
        target = tmp_path / "residue" / "rec.json"
        state_store.write_json_atomic(str(target), {"k": 1})
        assert sorted(p.name for p in target.parent.iterdir()) == ["rec.json"]

    def test_overwrites_in_place_without_truncating_first(self, tmp_path):
        target = tmp_path / "rec.json"
        state_store.write_json_atomic(str(target), {"v": 1})
        state_store.write_json_atomic(str(target), {"v": 2})
        assert json.loads(target.read_text()) == {"v": 2}

    def test_an_unwritable_destination_returns_false_without_raising(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert state_store.write_json_atomic(str(locked / "rec.json"), {"k": 1}) is False
        finally:
            locked.chmod(0o700)

    def test_unserialisable_content_returns_false_without_raising(self, tmp_path):
        target = tmp_path / "rec.json"
        assert state_store.write_json_atomic(str(target), {"k": object()}) is False
        assert not target.exists()


class TestReadJson:
    def test_round_trips(self, tmp_path):
        target = tmp_path / "rec.json"
        state_store.write_json_atomic(str(target), {"k": 1})
        assert state_store.read_json(str(target)) == {"k": 1}

    def test_missing_file_returns_the_default(self, tmp_path):
        assert state_store.read_json(str(tmp_path / "nope.json")) is None
        assert state_store.read_json(str(tmp_path / "nope.json"), {}) == {}

    def test_corrupt_file_returns_the_default_rather_than_raising(self, tmp_path):
        target = tmp_path / "rec.json"
        target.write_text("{not json")
        assert state_store.read_json(str(target), {"fallback": True}) == {"fallback": True}


class TestAppendJsonl:
    def test_appends_one_line_per_record_and_creates_parents(self, tmp_path):
        target = tmp_path / "deep" / "log.jsonl"
        state_store.append_jsonl(str(target), {"i": 1})
        state_store.append_jsonl(str(target), {"i": 2})
        assert target.read_text().splitlines() == ['{"i": 1}', '{"i": 2}']

    def test_the_file_is_0600(self, tmp_path):
        target = tmp_path / "log.jsonl"
        state_store.append_jsonl(str(target), {"i": 1})
        assert stat.S_IMODE(os.stat(str(target)).st_mode) == 0o600

    def test_records_never_contain_an_embedded_newline(self, tmp_path):
        target = tmp_path / "log.jsonl"
        state_store.append_jsonl(str(target), {"text": "a\nb"})
        assert len(target.read_text().splitlines()) == 1

    def test_an_unserialisable_record_returns_false_and_writes_nothing(self, tmp_path):
        target = tmp_path / "log.jsonl"
        assert state_store.append_jsonl(str(target), {"k": object()}) is False
        assert not target.exists()

    def test_an_unwritable_destination_returns_false_without_raising(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert state_store.append_jsonl(str(locked / "log.jsonl"), {"i": 1}) is False
        finally:
            locked.chmod(0o700)

    def test_an_existing_directory_is_not_chmodded_open(self, tmp_path):
        # `_ensure_dir` must chmod only directories it created. Relaxing an
        # existing 0o500 directory to 0o700 would make the test above pass for
        # the wrong reason, and would loosen a permission the user chose.
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            state_store.append_jsonl(str(locked / "log.jsonl"), {"i": 1})
            assert stat.S_IMODE(os.stat(str(locked)).st_mode) == 0o500
        finally:
            locked.chmod(0o700)


class TestReadJsonl:
    def test_missing_file_is_an_empty_list(self, tmp_path):
        assert state_store.read_jsonl(str(tmp_path / "nope.jsonl")) == []

    def test_skips_corrupt_lines_and_keeps_the_good_ones(self, tmp_path):
        target = tmp_path / "log.jsonl"
        target.write_text('{"i": 1}\nnot json\n\n{"i": 2}\n')
        assert state_store.read_jsonl(str(target)) == [{"i": 1}, {"i": 2}]

    def test_skips_lines_that_are_valid_json_but_not_objects(self, tmp_path):
        target = tmp_path / "log.jsonl"
        target.write_text('{"i": 1}\n["a"]\n42\n')
        assert state_store.read_jsonl(str(target)) == [{"i": 1}]

    def test_a_truncated_final_line_does_not_lose_earlier_records(self, tmp_path):
        # This is the crash-mid-append case, and it must degrade by one record.
        target = tmp_path / "log.jsonl"
        target.write_text('{"i": 1}\n{"i": 2}\n{"i": 3')
        assert state_store.read_jsonl(str(target)) == [{"i": 1}, {"i": 2}]


class TestPruneJsonl:
    def _log(self, tmp_path):
        target = tmp_path / "events.jsonl"
        for day, i in ((1, 1), (10, 2), (14, 3)):
            state_store.append_jsonl(
                str(target), {"i": i, "ts": "2026-09-%02dT00:00:00Z" % day}
            )
        return target

    def test_drops_records_older_than_the_retention_window(self, tmp_path, frozen_now):
        frozen_now("2026-09-14T12:00:00Z")
        target = self._log(tmp_path)
        kept = state_store.prune_jsonl(str(target), max_age_days=7)
        assert [r["i"] for r in kept] == [2, 3]
        assert [r["i"] for r in state_store.read_jsonl(str(target))] == [2, 3]

    def test_a_record_without_a_timestamp_is_kept(self, tmp_path, frozen_now):
        # Dropping it would silently delete data on a schema change.
        frozen_now("2026-09-14T12:00:00Z")
        target = tmp_path / "events.jsonl"
        state_store.append_jsonl(str(target), {"i": 1})
        assert [r["i"] for r in state_store.prune_jsonl(str(target), max_age_days=7)] == [1]

    def test_pruning_a_missing_file_is_a_no_op(self, tmp_path):
        assert state_store.prune_jsonl(str(tmp_path / "nope.jsonl"), max_age_days=7) == []

    def test_zero_or_none_retention_prunes_nothing(self, tmp_path, frozen_now):
        frozen_now("2026-09-14T12:00:00Z")
        target = self._log(tmp_path)
        assert len(state_store.prune_jsonl(str(target), max_age_days=None)) == 3
        assert len(state_store.prune_jsonl(str(target), max_age_days=0)) == 3
