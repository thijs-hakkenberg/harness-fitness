"""The project ledger — where a composition becomes history.

Two properties do the work, and both are about a reader who arrives later:

- **A composition file is content-addressed and never rewritten.** Episodes
  recorded weeks ago point at a digest by name, so rewriting that file would
  retroactively change what those episodes say they ran under.
- **A transition is appended only when the digest actually moves.** This runs on
  every `SessionStart`. A record per session would turn `changes.jsonl` into a
  session log, and "what changed between A and B" would no longer be answerable
  from it.

The consent gate is asserted the way `test_consent.py` asserts it: not by reading
a return value but by asserting the state directory is still *absent* afterwards.
A module that creates its root while resolving a path passes every other test in
this file and still breaks the promise in the README.
"""

import os

import pytest

import consent
import hfit_config
import ledger
import state_store

from conftest import files_under


@pytest.fixture
def cfg(isolated_home):
    return hfit_config.load()


@pytest.fixture
def accepted(cfg):
    """A config whose consent has been recorded, which is the normal case."""
    consent.record_acknowledgement(cfg)
    return cfg


@pytest.fixture
def proj(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return str(path)


def comp(
    digest,
    plugins=(),
    hooks=(),
    mcp=(),
    skills=(),
    agents=(),
    profile=None,
    env_hash=None,
    model_basis=None,
):
    """A composition record shaped the way `composition.build` returns one."""
    return {
        "schema": 1,
        "digest": digest,
        "env_hash": env_hash,
        "model_basis": model_basis,
        "captured_at": "2026-09-14T12:00:00Z",
        "profile": profile or {},
        "plugins": [dict(p) for p in plugins],
        "settings_hooks": [dict(h) for h in hooks],
        "project_mcp_servers": list(mcp),
        "mcpjson_enabled": [],
        "user": {"skills": list(skills), "agents": list(agents)},
        "unresolved": [],
        "marketplaces": {},
        "flags": [],
    }


def plugin(key, version="1.0.0", sha="a" * 12, basis="git", on_disk=True):
    return {
        "key": key,
        "version": version,
        "sha": sha,
        "sha_basis": basis,
        "on_disk": on_disk,
    }


def hook(event, matcher=None, digest="d" * 12, scope="user", owner=None):
    return {
        "scope": scope,
        "owner": owner,
        "event": event,
        "matcher": matcher,
        "type": "command",
        "timeout": None,
        "cmd_basename": "gate.py",
        "cmd_digest": digest,
    }


class TestPaths:
    def test_the_root_is_under_the_state_root(self, cfg, proj):
        assert ledger.root(proj, cfg) == state_store.project_dir(proj)

    def test_in_repo_relocates_the_root_into_the_project(self, cfg, proj):
        # Offered for teams who want committed history. It is off by default
        # because writing a directory into a repository the user may not own is
        # something to be asked for rather than fallen into.
        cfg["ledger"]["in_repo"] = True
        assert ledger.root(proj, cfg) == os.path.join(proj, ".harness-fitness")

    def test_the_in_repo_root_follows_the_cwd_it_was_given(self, cfg, tmp_path):
        # `cwd` is passed in rather than read from `$CLAUDE_PROJECT_DIR`, for the
        # same reason `composition.inventory` takes its two paths: two sources of
        # truth would let the environment redirect a write whose caller already
        # knew which project it meant.
        cfg["ledger"]["in_repo"] = True
        other = tmp_path / "elsewhere"
        other.mkdir()
        assert ledger.root(str(other), cfg).startswith(str(other))

    def test_resolving_a_path_creates_nothing(self, cfg, proj):
        ledger.root(proj, cfg)
        ledger.composition_path(proj, "abc123abc123", cfg)
        ledger.changes_path(proj, cfg)

        assert not os.path.exists(state_store.state_root())

    def test_the_composition_filename_is_the_digest(self, cfg, proj):
        path = ledger.composition_path(proj, "abc123abc123", cfg)

        assert os.path.basename(path) == "abc123abc123.json"
        assert os.path.dirname(path) == os.path.join(
            ledger.root(proj, cfg), "compositions"
        )

    def test_the_state_dir_override_moves_the_root(self, monkeypatch, tmp_path, proj):
        # What lets the suite — and a curious user — point the whole ledger
        # somewhere disposable.
        monkeypatch.setenv("HFIT_STATE_DIR", str(tmp_path / "elsewhere"))
        cfg = hfit_config.load()

        assert ledger.root(proj, cfg).startswith(str(tmp_path / "elsewhere"))


class TestTheConsentGate:
    def test_an_unacknowledged_install_writes_nothing(self, cfg, proj):
        # The load-bearing test. Not "returns an error" — leaves no trace.
        result = ledger.record_composition(proj, comp("aaaaaaaaaaaa"), cfg)

        assert result["ok"] is False
        assert result["reason"] == "not_acknowledged"
        # Existence, not emptiness: an empty directory is a trace, and a listing of
        # files cannot see one (see `files_under`'s docstring). "Leaves no trace" is
        # what ADR-014 promises and what the README says.
        assert not os.path.exists(state_store.state_root())

    def test_a_changed_governing_config_refuses_with_its_own_reason(self, accepted, proj):
        # A different remedy from `not_acknowledged`: the user consented to
        # collecting something else, so the report must say which it was.
        accepted["otel"]["enabled"] = not accepted["otel"]["enabled"]

        result = ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)

        assert result["ok"] is False
        assert result["reason"] == "config_changed"

    def test_an_acknowledged_install_writes(self, accepted, proj):
        result = ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)

        assert result["ok"] is True
        assert os.path.exists(ledger.composition_path(proj, "aaaaaaaaaaaa", accepted))


class TestImmutability:
    def test_a_new_digest_is_written_and_reads_back(self, accepted, proj):
        record = comp("aaaaaaaaaaaa", plugins=[plugin("abacus@abacus")])
        ledger.record_composition(proj, record, accepted)

        assert ledger.read_composition(proj, "aaaaaaaaaaaa", accepted) == record

    def test_a_second_sighting_reports_known(self, accepted, proj):
        first = ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        second = ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)

        assert first["status"] == "new"
        assert second["status"] == "known"

    def test_a_second_sighting_does_not_rewrite_the_file(self, accepted, proj):
        # Content-addressed means the digest names the file's contents. An episode
        # closed last month points at this digest by name; rewriting it would
        # change what that episode says it ran under.
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)

        later = comp("aaaaaaaaaaaa")
        later["captured_at"] = "2026-12-25T00:00:00Z"
        ledger.record_composition(proj, later, accepted)

        stored = ledger.read_composition(proj, "aaaaaaaaaaaa", accepted)
        assert stored["captured_at"] == "2026-09-14T12:00:00Z"


class TestChanges:
    def test_the_first_composition_records_a_baseline(self, accepted, proj):
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        entries = state_store.read_jsonl(ledger.changes_path(proj, accepted))

        assert len(entries) == 1
        assert entries[0]["from"] is None
        assert entries[0]["to"] == "aaaaaaaaaaaa"
        assert entries[0]["kind"] == "baseline"

    def test_the_baseline_claims_nothing_was_added(self, accepted, proj):
        # A first sighting is not a change. Listing forty components as `added` at
        # install time would put a fabricated steering event at the head of every
        # ledger and inflate the workshop's steering-events metric by one harness.
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", plugins=[plugin("abacus@abacus")]), accepted
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[0]

        assert entry["added"] == []
        assert entry["removed"] == []
        assert entry["changed"] == []
        assert entry["profile_delta"] == {}

    def test_an_unchanged_digest_appends_nothing(self, accepted, proj):
        # This runs on every SessionStart. A record per session would make
        # `changes.jsonl` a session log rather than a change log.
        for _ in range(3):
            ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)

        assert len(state_store.read_jsonl(ledger.changes_path(proj, accepted))) == 1

    def test_a_moved_digest_appends_a_transition(self, accepted, proj):
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        ledger.record_composition(proj, comp("bbbbbbbbbbbb"), accepted)

        entries = state_store.read_jsonl(ledger.changes_path(proj, accepted))
        assert len(entries) == 2
        assert entries[1]["from"] == "aaaaaaaaaaaa"
        assert entries[1]["to"] == "bbbbbbbbbbbb"
        assert entries[1]["kind"] == "transition"

    def test_a_transition_names_added_and_removed_plugins(self, accepted, proj):
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", plugins=[plugin("abacus@abacus")]), accepted
        )
        ledger.record_composition(
            proj, comp("bbbbbbbbbbbb", plugins=[plugin("harness-fitness@hfit")]), accepted
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["added"] == ["plugin:harness-fitness@hfit"]
        assert entry["removed"] == ["plugin:abacus@abacus"]

    def test_a_transition_names_a_changed_plugin_version(self, accepted, proj):
        # Neither added nor removed: the same plugin, moved. Reported as `changed`
        # so a report can say "abacus 1.0.0 → 1.1.0" instead of "one plugin left
        # and a different one arrived".
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", plugins=[plugin("abacus@abacus")]), accepted
        )
        ledger.record_composition(
            proj,
            comp(
                "bbbbbbbbbbbb",
                plugins=[plugin("abacus@abacus", version="1.1.0", sha="b" * 12)],
            ),
            accepted,
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["added"] == []
        assert entry["removed"] == []
        assert entry["changed"] == [
            {
                "id": "plugin:abacus@abacus",
                "from": {"version": "1.0.0", "sha": "a" * 12},
                "to": {"version": "1.1.0", "sha": "b" * 12},
            }
        ]

    def test_an_unchanged_plugin_is_not_reported_as_changed(self, accepted, proj):
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", plugins=[plugin("abacus@abacus")]), accepted
        )
        ledger.record_composition(
            proj,
            comp(
                "bbbbbbbbbbbb",
                plugins=[plugin("abacus@abacus")],
                mcp=["github"],
            ),
            accepted,
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["changed"] == []
        assert entry["added"] == ["mcp:github"]

    def test_settings_hooks_and_user_components_appear_in_the_diff(
        self, accepted, proj
    ):
        # Source 7 and source 8 of the inventory. A plugin-only diff would report
        # "nothing changed" when a user-level `auto-test.sh` hook was added — real
        # composition that belongs to no plugin.
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        ledger.record_composition(
            proj,
            comp(
                "bbbbbbbbbbbb",
                hooks=[hook("PostToolUse", matcher="Edit|Write")],
                skills=["audit"],
                agents=["judge"],
            ),
            accepted,
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["added"] == [
            "agent:judge",
            "hook:user:-:PostToolUse:Edit|Write:dddddddddddd",
            "skill:audit",
        ]

    def test_a_hook_matcher_change_is_a_different_component(self, accepted, proj):
        # A widened matcher is a widened sensor reach. Collapsing it into "the same
        # hook" would hide the change that mattered.
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", hooks=[hook("PostToolUse", matcher="Edit")]),
            accepted,
        )
        ledger.record_composition(
            proj,
            comp("bbbbbbbbbbbb", hooks=[hook("PostToolUse", matcher="Edit|Write")]),
            accepted,
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["added"] == ["hook:user:-:PostToolUse:Edit|Write:dddddddddddd"]
        assert entry["removed"] == ["hook:user:-:PostToolUse:Edit:dddddddddddd"]

    def test_an_absent_matcher_is_not_an_empty_one(self, accepted, proj):
        # `None` fires on every tool; `""` fires on none. The two ids must differ
        # or the diff would report a sensor that reaches everything and one that
        # reaches nothing as the same component.
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", hooks=[hook("PostToolUse", matcher=None)]),
            accepted,
        )
        ledger.record_composition(
            proj, comp("bbbbbbbbbbbb", hooks=[hook("PostToolUse", matcher="")]),
            accepted,
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["removed"] == ["hook:user:-:PostToolUse:-:dddddddddddd"]
        assert entry["added"] == ["hook:user:-:PostToolUse::dddddddddddd"]

    def test_a_transition_carries_the_profile_delta(self, accepted, proj):
        # The sentence a report wants to write: "B added two FB·COMP sensors".
        # Computed here rather than at read time, because the *previous* profile is
        # only reliably available at the moment of transition.
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", profile={"fb_comp": 1, "ff_inf": 3}), accepted
        )
        ledger.record_composition(
            proj, comp("bbbbbbbbbbbb", profile={"fb_comp": 3, "ff_inf": 3}), accepted
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["profile_delta"] == {"fb_comp": 2}

    def test_the_transition_timestamp_comes_from_the_clock(
        self, accepted, proj, frozen_now
    ):
        frozen_now("2026-09-14T08:30:00Z")
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[0]

        assert entry["ts"] == "2026-09-14T08:30:00Z"

    def test_the_transition_carries_the_env_hash(self, accepted, proj):
        # So a reader of `changes.jsonl` alone can tell whether the two sides were
        # comparable at all, without resolving both composition files (ADR-007).
        ledger.record_composition(
            proj, comp("aaaaaaaaaaaa", env_hash="e" * 64), accepted
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[0]

        assert entry["env_hash"] == "e" * 64

    def test_the_transition_carries_the_model_basis_too(self, accepted, proj):
        # An `env_hash` in a change entry answers "were these two comparable?" only
        # if the basis it was resolved on is there beside it. Carrying the hash
        # alone would let a reader of `changes.jsonl` conclude two sides were
        # comparable when ADR-007 obliges a refusal.
        ledger.record_composition(
            proj,
            comp("aaaaaaaaaaaa", env_hash="e" * 64, model_basis="declared"),
            accepted,
        )
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[0]

        assert entry["model_basis"] == "declared"

    def test_a_missing_previous_record_yields_a_null_diff_not_an_empty_one(
        self, accepted, proj
    ):
        # A deleted composition file makes the diff unknowable. Empty lists would
        # claim the harness changed digest while nothing about it moved, which is
        # impossible — so the absence is stated instead.
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        os.unlink(ledger.composition_path(proj, "aaaaaaaaaaaa", accepted))

        ledger.record_composition(proj, comp("bbbbbbbbbbbb"), accepted)
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert entry["diff_basis"] == "unavailable"
        assert entry["added"] is None
        assert entry["removed"] is None
        assert entry["changed"] is None
        assert entry["profile_delta"] is None

    def test_a_resolvable_diff_says_so(self, accepted, proj):
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        ledger.record_composition(proj, comp("bbbbbbbbbbbb"), accepted)
        entries = state_store.read_jsonl(ledger.changes_path(proj, accepted))

        assert entries[0]["diff_basis"] == "baseline"
        assert entries[1]["diff_basis"] == "records"

    def test_the_returned_change_is_the_appended_record(self, accepted, proj):
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        result = ledger.record_composition(proj, comp("bbbbbbbbbbbb"), accepted)
        entry = state_store.read_jsonl(ledger.changes_path(proj, accepted))[1]

        assert result["change"] == entry

    def test_no_change_is_returned_when_the_digest_held_still(self, accepted, proj):
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        result = ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)

        assert result["change"] is None


class TestReading:
    def test_current_digest_is_the_last_transition(self, accepted, proj):
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        ledger.record_composition(proj, comp("bbbbbbbbbbbb"), accepted)

        assert ledger.current_digest(proj, accepted) == "bbbbbbbbbbbb"

    def test_current_digest_is_none_on_a_fresh_ledger(self, cfg, proj):
        assert ledger.current_digest(proj, cfg) is None

    def test_read_composition_returns_none_for_an_unknown_digest(self, cfg, proj):
        assert ledger.read_composition(proj, "ffffffffffff", cfg) is None

    def test_a_corrupt_changes_line_costs_one_record(self, accepted, proj):
        # A crash mid-append leaves a truncated final line. The next session must
        # still resolve a current digest rather than treating the ledger as empty
        # and appending a second baseline. The tolerance itself lives in
        # `state_store.read_jsonl`; this asserts the ledger inherits it.
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        with open(ledger.changes_path(proj, accepted), "a") as fh:
            fh.write('{"to": "trunc')

        assert ledger.current_digest(proj, accepted) == "aaaaaaaaaaaa"

    def test_a_trailing_record_with_no_digest_is_skipped_not_trusted(
        self, accepted, proj
    ):
        # Distinct from the corrupt-line case, and not covered by it: this line is
        # valid JSON, so `read_jsonl` keeps it. A newer writer appending a record
        # kind that carries no `to` — a marker, an annotation — would make the last
        # line digestless, and taking the final line blindly would resolve the
        # current digest as `None` and append a second baseline into the middle of
        # the history.
        ledger.record_composition(proj, comp("aaaaaaaaaaaa"), accepted)
        state_store.append_jsonl(
            ledger.changes_path(proj, accepted), {"ts": "2026-10-01T00:00:00Z"}
        )

        assert ledger.current_digest(proj, accepted) == "aaaaaaaaaaaa"

        ledger.record_composition(proj, comp("bbbbbbbbbbbb"), accepted)
        transition = state_store.read_jsonl(ledger.changes_path(proj, accepted))[-1]
        assert transition["from"] == "aaaaaaaaaaaa"
        assert transition["kind"] == "transition"


class TestMalformedInput:
    def test_a_record_with_no_digest_is_refused(self, accepted, proj):
        record = comp("aaaaaaaaaaaa")
        del record["digest"]

        result = ledger.record_composition(proj, record, accepted)

        assert result["ok"] is False
        assert result["reason"] == "no_digest"
        # The validation happens before any path is resolved as a directory, so the
        # ledger root is not merely empty — it was never created. Asserted on the
        # directory rather than on its file listing, which would pass either way.
        assert not os.path.isdir(ledger.root(proj, accepted))

    def test_a_non_dict_record_is_refused(self, accepted, proj):
        result = ledger.record_composition(proj, "not a record", accepted)

        assert result["ok"] is False
        assert result["reason"] == "no_digest"

    def test_the_config_is_loaded_when_omitted(self, accepted, proj):
        # The hook path calls this with a config it already loaded; a skill may
        # not. Both must respect the same consent fingerprint.
        assert ledger.record_composition(proj, comp("aaaaaaaaaaaa"))["ok"] is True
