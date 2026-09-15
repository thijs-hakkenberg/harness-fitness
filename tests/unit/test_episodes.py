"""`episodes.jsonl` — the store of record for what happened, per closed issue.

An episode is one closed beads issue, and `reconcile` runs from four triggers, so
the same episode is offered to this ledger repeatedly. That makes the append rule
the whole design, and it is the one `changes.jsonl` already uses: **append only on
movement.**

Both halves of that rule are load-bearing, in opposite directions:

- **Appending on every offer** would turn the store of record into a session log.
  "What was this episode's outcome" would become a de-duplication problem whose
  duplicates are indistinguishable from genuine re-entries.
- **Skipping on a known issue id** would freeze the first answer forever. An
  episode reconciled at `Stop` is usually `unstated`; `/hfit:outcome` exists so a
  human can state it afterwards, and a ledger that ignored the second reading would
  make `verdict_coverage` permanently unimprovable — defeating W2's only mitigation.

So a record is never rewritten, a second reading is appended beside the first, and
the **last append wins**. Comparison is over every field except a small declared
volatile set, which errs toward appending: an unrecognised field that moves shows up
as an extra record, which is loud, rather than as a reading nobody stored, which is
silent.
"""

import os

import pytest

import consent
import hfit_config
import hfit_time
import ledger
import state_store
import verdict as verdict_lib

from conftest import files_under


def at(text):
    """`now` is a datetime throughout the ledger, matching `hfit_time.iso`."""
    return hfit_time.parse_iso(text)


@pytest.fixture
def cfg(isolated_home):
    return hfit_config.load()


@pytest.fixture
def accepted(cfg):
    consent.record_acknowledgement(cfg)
    return cfg


@pytest.fixture
def proj(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return str(path)


def episode(issue_id="Proj-abc", verdict="unstated", basis="unstated", **over):
    """An episode record shaped the way `reconcile` will hand one over."""
    out = {
        "issue_id": issue_id,
        "closed_at": "2026-09-15T08:35:23Z",
        "started_at": "2026-09-14T20:52:02Z",
        "issue_type": "task",
        "composition_digest": "a" * 12,
        "env_hash": "b" * 64,
        "model_basis": "observed",
        "verdict": verdict,
        "verdict_basis": basis,
        "verdict_reason": "bd_default",
        "lexicon_version": None,
        "capture_ok": True,
        "partial": False,
        "tokens_total": 8134206,
        "tool_calls": 57,
    }
    out.update(over)
    return out


def lines(cwd):
    with open(ledger.episodes_path(cwd)) as fh:
        return [l for l in fh.read().splitlines() if l.strip()]


# --------------------------------------------------------------------------
# Append only on movement
# --------------------------------------------------------------------------


def test_a_first_sighting_of_an_episode_is_appended(accepted, proj):
    result = ledger.record_episode(proj, episode(), cfg=accepted)

    assert result["ok"] is True
    assert result["status"] == "new"
    assert result["issue_id"] == "Proj-abc"
    assert [r["issue_id"] for r in ledger.episodes(proj, accepted)] == ["Proj-abc"]


def test_re_recording_an_unchanged_episode_appends_nothing(accepted, proj):
    """`reconcile` runs from four triggers and is called again on every `Stop`.

    A record per call would make this file a session log, and the later time is
    deliberately different so the assertion is about *movement* rather than about
    two identical dicts comparing equal.
    """
    ledger.record_episode(proj, episode(), cfg=accepted, now=at("2026-09-15T09:00:00Z"))
    second = ledger.record_episode(
        proj, episode(), cfg=accepted, now=at("2026-09-15T17:41:02Z")
    )

    assert second["ok"] is True
    assert second["status"] == "unchanged"
    assert len(lines(proj)) == 1, "a later reading of the same facts is not a record"


def test_a_moved_verdict_appends_rather_than_rewriting_the_first_record(accepted, proj):
    """The `/hfit:outcome` path, which is W2's whole mitigation.

    The first record must survive intact: it is the evidence that the outcome was
    unstated at close time, which is what makes a coverage figure improving over
    time readable as a habit forming rather than as a number that was always there.
    """
    ledger.record_episode(proj, episode(), cfg=accepted)

    second = ledger.record_episode(
        proj,
        episode(verdict="accepted", basis="declared", verdict_reason=None),
        cfg=accepted,
    )

    assert second["status"] == "updated"
    stored = ledger.episodes(proj, accepted)
    assert len(stored) == 2
    assert stored[0]["verdict"] == "unstated", "history is appended to, never edited"
    assert stored[1]["verdict"] == "accepted"


def test_re_offering_the_reading_that_already_won_appends_nothing(accepted, proj):
    """The `Stop` that follows `/hfit:outcome`, and every `Stop` after it.

    Comparison has to be against the *last* reading rather than the first, because
    once an episode has moved once it is offered again on every trigger. Comparing
    against the first would make exactly the episodes someone bothered to state a
    verdict for the ones whose history grows without bound.
    """
    ledger.record_episode(proj, episode(), cfg=accepted)
    ledger.record_episode(
        proj, episode(verdict="accepted", basis="declared"), cfg=accepted
    )

    third = ledger.record_episode(
        proj, episode(verdict="accepted", basis="declared"), cfg=accepted
    )

    assert third["status"] == "unchanged"
    assert len(lines(proj)) == 2, "the move is one record, not one per later trigger"


def test_the_latest_record_for_an_issue_is_the_one_measures_see(accepted, proj):
    ledger.record_episode(proj, episode(), cfg=accepted)
    ledger.record_episode(
        proj, episode(verdict="accepted", basis="declared"), cfg=accepted
    )

    latest = ledger.latest_episodes(proj, accepted)

    assert len(latest) == 1, "one episode is one issue, however many readings it took"
    assert latest[0]["verdict"] == "accepted"
    assert latest[0]["verdict_basis"] == "declared"


def test_the_winner_is_the_last_append_not_the_latest_timestamp(accepted, proj):
    """File position is the causal order of appends; `ts` is not.

    `$HFIT_NOW` freezes the clock for a whole test run and a real clock can step
    backwards, so two records can carry the same or a decreasing `ts`. Resolving the
    winner by timestamp would leave that undefined — and would silently prefer a
    stale `unstated` over the verdict a human just declared.
    """
    ledger.record_episode(proj, episode(), cfg=accepted, now=at("2026-09-15T17:00:00Z"))
    ledger.record_episode(
        proj,
        episode(verdict="accepted", basis="declared"),
        cfg=accepted,
        now=at("2026-09-15T09:00:00Z"),
    )

    assert ledger.latest_episodes(proj, accepted)[0]["verdict"] == "accepted"


def test_a_field_this_version_does_not_know_about_still_counts_as_movement(
    accepted, proj
):
    """Steps 5 and 6 add touch and catch counts to this record.

    Comparison is over everything outside a declared volatile set, so a field added
    later moves the record without anyone remembering to declare it. The failure
    direction is deliberate: an unnecessary append is visible as an extra line,
    while a missed one is a reading nobody stored and nothing can detect.
    """
    ledger.record_episode(proj, episode(hard_touches=0), cfg=accepted)

    result = ledger.record_episode(proj, episode(hard_touches=2), cfg=accepted)

    assert result["status"] == "updated"
    assert ledger.latest_episodes(proj, accepted)[0]["hard_touches"] == 2


def test_a_field_that_disappears_is_movement_too(accepted, proj):
    """Losing a field is a change, and the comparison has to be able to see it.

    An episode whose `hfit_verdict` was deleted from beads reads as `unstated` again,
    and a comparison over only the *new* record's keys would call that holding still —
    leaving the ledger asserting a verdict that no longer exists anywhere.
    """
    ledger.record_episode(proj, episode(hard_touches=2), cfg=accepted)

    result = ledger.record_episode(proj, episode(), cfg=accepted)

    assert result["status"] == "updated"


def test_no_measure_bearing_field_is_treated_as_volatile(accepted, proj):
    """The one direction the volatile set must never grow in.

    Anything a measure divides by has to move the record, so this names them
    explicitly rather than trusting that a future edit will think it through.
    """
    for field in (
        "verdict",
        "verdict_basis",
        "capture_ok",
        "partial",
        "tokens_total",
        "tool_calls",
        "composition_digest",
        "env_hash",
        "closed_at",
    ):
        assert field not in ledger.EPISODE_VOLATILE_FIELDS, field


def test_a_bumped_schema_alone_is_not_movement(accepted, proj, monkeypatch):
    """The schema describes the record's shape, not the episode.

    Comparing it would append one record per episode on every upgrade, claiming
    movement in work that finished weeks ago. A reader can see which shape a record
    has by reading it.
    """
    ledger.record_episode(proj, episode(), cfg=accepted)
    monkeypatch.setattr(ledger, "EPISODE_SCHEMA", ledger.EPISODE_SCHEMA + 1)

    result = ledger.record_episode(proj, episode(), cfg=accepted)

    assert result["status"] == "unchanged"
    assert len(lines(proj)) == 1


def test_two_different_issues_are_two_episodes(accepted, proj):
    """Deduplication is per issue id, not global."""
    ledger.record_episode(proj, episode(issue_id="Proj-abc"), cfg=accepted)
    ledger.record_episode(proj, episode(issue_id="Proj-def"), cfg=accepted)

    assert [r["issue_id"] for r in ledger.latest_episodes(proj, accepted)] == [
        "Proj-abc",
        "Proj-def",
    ]


def test_the_stored_record_carries_the_schema_and_a_timestamp(accepted, proj):
    ledger.record_episode(proj, episode(), cfg=accepted, now=at("2026-09-15T09:00:00Z"))

    stored = ledger.episodes(proj, accepted)[0]

    assert stored["schema"] == ledger.EPISODE_SCHEMA
    assert stored["ts"] == "2026-09-15T09:00:00Z"


# --------------------------------------------------------------------------
# What is refused
# --------------------------------------------------------------------------


def test_an_episode_with_no_issue_id_is_refused(accepted, proj):
    """Nothing can be deduplicated against it, so it would append on every trigger.

    Stored under a placeholder it would be worse: every id-less episode would
    collapse into one, and the last one written would be the only one measured.
    """
    result = ledger.record_episode(proj, episode(issue_id=None), cfg=accepted)

    assert result["ok"] is False
    assert result["reason"] == "no_issue_id"
    assert not os.path.exists(ledger.episodes_path(proj, accepted))


def test_an_episode_with_no_closed_at_is_refused(accepted, proj):
    """The boundary is the timestamp and not the `status` label (matching `beads_read`).

    Without `closed_at` the episode has no window, so every measure over it would
    cover an undefined interval — and it would be re-offered as unfinished work
    forever.
    """
    result = ledger.record_episode(proj, episode(closed_at=None), cfg=accepted)

    assert result["ok"] is False
    assert result["reason"] == "no_closed_at"
    assert not os.path.exists(ledger.episodes_path(proj, accepted))


def test_nothing_is_written_before_consent(cfg, proj):
    """Asserted on the filesystem rather than on the return value (ADR-014).

    A module that created its root while resolving a path passes every other test
    in this file and still breaks the promise the README makes.
    """
    result = ledger.record_episode(proj, episode(), cfg=cfg)

    assert result["ok"] is False
    assert result["reason"] == "not_acknowledged"
    assert not os.path.exists(state_store.state_root()), files_under(
        os.path.dirname(state_store.state_root())
    )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_an_empty_ledger_reads_as_no_episodes_and_the_measure_layer_calls_that_unknown(
    accepted, proj
):
    """Where the "unknown is never zero" chain has to hold across two layers.

    An absent ledger genuinely holds nothing, so `[]` is the honest answer here —
    but an *unreadable* one presents the same way, which is why the layer above must
    not turn an empty population into a number. It does not: coverage over `[]` is
    `None`.
    """
    assert ledger.episodes(proj, accepted) == []
    assert ledger.latest_episodes(proj, accepted) == []
    assert verdict_lib.coverage([])["coverage"] is None


def test_a_corrupt_line_costs_one_record_not_the_population(accepted, proj):
    """A truncated final line is the ordinary result of a crash mid-append."""
    ledger.record_episode(proj, episode(issue_id="Proj-abc"), cfg=accepted)
    with open(ledger.episodes_path(proj, accepted), "a") as fh:
        fh.write('{"issue_id": "Proj-trunc", "clos\n')
    ledger.record_episode(proj, episode(issue_id="Proj-def"), cfg=accepted)

    assert [r["issue_id"] for r in ledger.latest_episodes(proj, accepted)] == [
        "Proj-abc",
        "Proj-def",
    ]


def test_latest_episodes_is_ordered_by_when_the_episode_closed(accepted, proj):
    """Deterministic and meaningful, rather than by the order reconcile happened to
    see them — which is the order sessions were opened in, not the order work
    finished.

    The two orderings are made to contradict each other on purpose: the episode that
    closed later is read *first*, and its id sorts first alphabetically too. Ordering
    by either the reading time or the id alone would reverse the result, so neither
    can pass by coincidence.
    """
    ledger.record_episode(
        proj,
        episode(issue_id="Proj-aaa", closed_at="2026-09-15T18:00:00Z"),
        cfg=accepted,
        now=at("2026-09-15T18:05:00Z"),
    )
    ledger.record_episode(
        proj,
        episode(issue_id="Proj-zzz", closed_at="2026-09-14T08:00:00Z"),
        cfg=accepted,
        now=at("2026-09-15T18:06:00Z"),
    )

    assert [r["issue_id"] for r in ledger.latest_episodes(proj, accepted)] == [
        "Proj-zzz",
        "Proj-aaa",
    ]


def test_the_episode_ledger_follows_the_in_repo_setting(cfg, proj):
    """A team wanting committed history gets episodes beside the compositions."""
    cfg["ledger"]["in_repo"] = True

    path = ledger.episodes_path(proj, cfg)

    assert path == os.path.join(proj, ledger.IN_REPO_DIRNAME, "episodes.jsonl")


def test_resolving_the_episode_path_creates_nothing(cfg, proj):
    ledger.episodes_path(proj, cfg)
    ledger.episodes(proj, cfg)
    ledger.latest_episodes(proj, cfg)

    assert not os.path.exists(state_store.state_root())
