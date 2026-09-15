"""The verdict ladder — did the episode succeed, and how do we know?

abacus tracks lifecycle and never success, so without this module "tokens per
outcome" has no denominator. The ladder exists because the five ways of learning an
outcome are not equally trustworthy and must not be averaged into one claim:

    declared > structured > lexical > inferential > unstated

Two properties matter more than any individual rung.

**`unstated` is the dominant real case and must not read as failure.** On this
machine 10 of 12 closed issues carry bd's default `close_reason` of exactly
`"Closed"`. An `unstated` episode is one whose outcome nobody recorded — it is not
a rejection, and counting it as one would report a working harness as a failing
one. It is excluded from the passing count *and* published as a coverage shortfall,
which is why `verdict_coverage` is a headline number rather than a footnote.

**Coverage over an empty population is `None`, never `0.0`.** Zero would be a claim
about the user's work; `None` is the truth, which is that nothing has been looked at
yet.
"""

import pytest

import verdict

from conftest import entries_under


def issue(close_reason="Closed", metadata=None, closed_at="2026-09-15T08:35:23Z", **over):
    out = {
        "id": "Proj-abc",
        "status": "closed",
        "closed_at": closed_at,
        "close_reason": close_reason,
        "metadata": {"abacus_schema": 1, "abacus_partial": False},
    }
    if metadata is not None:
        out["metadata"] = metadata
    out.update(over)
    return out


# --------------------------------------------------------------------------
# The rungs, most trustworthy first
# --------------------------------------------------------------------------


def test_a_declared_verdict_is_taken_at_its_word():
    """`hfit_verdict` was written by a human answering `/hfit:outcome`."""
    result = verdict.classify(
        issue(
            close_reason="some rambling free text about it working",
            metadata={"abacus_schema": 1, "hfit_verdict": "rejected"},
        )
    )

    assert result["verdict"] == "rejected"
    assert result["basis"] == "declared"


def test_an_unrecognised_declared_value_falls_through_instead_of_becoming_a_verdict():
    """Metadata is hand-editable and Dolt-synced from other machines.

    A `hfit_verdict` of `"probably fine"` must not enter the vocabulary a report
    binds to — falling through to the computational read gives a weaker but *known*
    basis, where honouring it would put an unhandled value in the headline.
    """
    result = verdict.classify(
        issue(
            close_reason="accepted: it works",
            metadata={"abacus_schema": 1, "hfit_verdict": "probably fine"},
        )
    )

    assert result["verdict"] == "accepted"
    assert result["basis"] == "structured"


def test_partial_cannot_be_declared_because_it_is_derived():
    """`partial` is a statement about the *capture*, not about the outcome.

    Accepting it as a declared verdict would let a hand-set key claim an interrupted
    session that abacus recorded as clean, which is the one direction of this flag
    nothing can verify.
    """
    result = verdict.classify(
        issue(
            close_reason="rejected: no good",
            metadata={"abacus_schema": 1, "hfit_verdict": "partial"},
        )
    )

    assert result["verdict"] == "rejected"
    assert "partial" not in verdict.DECLARABLE


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("accepted: the parser handles nested arrays now", "accepted"),
        ("rejected: still drops the last row", "rejected"),
        ("abandoned: overtaken by the rewrite", "abandoned"),
        ("superseded: folded into Proj-xyz", "superseded"),
    ],
)
def test_a_structured_prefix_is_read_off_the_close_reason(reason, expected):
    """The habit the plugin teaches, because it is unambiguous and free."""
    result = verdict.classify(issue(close_reason=reason))

    assert result["verdict"] == expected
    assert result["basis"] == "structured"


def test_the_structured_prefix_is_matched_regardless_of_case_and_spacing():
    assert verdict.classify(issue(close_reason="ACCEPTED:  it works"))["basis"] == (
        "structured"
    )
    assert verdict.classify(issue(close_reason="  accepted:it works"))["basis"] == (
        "structured"
    )


def test_a_prefix_word_without_the_colon_is_not_structured():
    """The colon is the whole signal. "accepted" mid-sentence is ordinary prose.

    Without the colon this falls through to the lexicon, which is a *weaker* claim
    and must be labelled as one — a report that called it `structured` would present
    a guess with the authority of a convention.
    """
    result = verdict.classify(issue(close_reason="accepted by review, works fine"))

    assert result["basis"] == "lexical"


def test_free_text_is_read_by_the_lexicon_and_stamped_with_its_version():
    """Stamped because the lexicon will change, and a number computed under v1 must
    stay attributable to v1 rather than silently re-interpreted."""
    result = verdict.classify(issue(close_reason="all tests green, shipped it"))

    assert result["verdict"] == "accepted"
    assert result["basis"] == "lexical"
    assert result["lexicon_version"] == verdict.LEXICON_VERSION


def test_a_stored_inferential_verdict_ranks_below_the_lexicon():
    """ADR-013: `inferential` is a *backfill* tool, not a source of truth.

    It exists to fill in issues closed before the `accepted:` habit. Where a
    computational read is available, the computational read wins — otherwise
    installing the inference path would quietly downgrade the basis of episodes
    that already had a better one.
    """
    result = verdict.classify(
        issue(
            close_reason="broken, reverted the change",
            metadata={
                "abacus_schema": 1,
                "hfit_verdict": "accepted",
                "hfit_verdict_basis": "inferential",
            },
        )
    )

    assert result["verdict"] == "rejected"
    assert result["basis"] == "lexical"


def test_an_inferential_verdict_is_used_where_nothing_computational_reads():
    result = verdict.classify(
        issue(
            close_reason="Closed",
            metadata={
                "abacus_schema": 1,
                "hfit_verdict": "accepted",
                "hfit_verdict_basis": "inferential",
            },
        )
    )

    assert result["verdict"] == "accepted"
    assert result["basis"] == "inferential"


# --------------------------------------------------------------------------
# `unstated` — the dominant real case
# --------------------------------------------------------------------------


def test_bds_default_close_reason_is_unstated_not_success():
    """Measured: 10 of 12 closed issues on this machine say exactly `"Closed"`.

    Reading it as success would supply the priority-1 measure with a denominator
    made of episodes nobody assessed.
    """
    result = verdict.classify(issue(close_reason="Closed"))

    assert result["verdict"] == "unstated"
    assert result["basis"] == "unstated"
    assert result["reason"] == "bd_default", (
        "named apart from `no_signal` because the advice differs: a wall of "
        "`bd_default` means teach the `accepted:` prefix, while `no_signal` means "
        "people are writing reasons the lexicon cannot read"
    )


def test_an_unstated_verdict_is_not_a_failure():
    """The distinction the whole ladder protects.

    A rejection is evidence the harness produced bad work. An unstated verdict is
    evidence of nothing at all, and conflating them reports a working harness as a
    failing one.
    """
    assert verdict.classify(issue(close_reason="Closed"))["verdict"] != "rejected"


def test_an_absent_close_reason_is_unstated():
    for reason in (None, "", "   "):
        result = verdict.classify(issue(close_reason=reason))
        assert result["verdict"] == "unstated", reason
        assert result["basis"] == "unstated", reason


def test_unrecognised_free_text_is_unstated_rather_than_guessed():
    result = verdict.classify(issue(close_reason="see the thread for context"))

    assert result["basis"] == "unstated"
    assert result["reason"] == "no_signal"


def test_every_unstated_reason_the_module_can_emit_is_declared():
    """Same binding as `BASES`: a reason with no declared name reaches a report that
    has no sentence for it."""
    seen = {
        verdict.classify(issue(close_reason=r))["reason"]
        for r in (None, "Closed", "see the thread", "fixed but failing")
    }

    assert seen == set(verdict.UNSTATED_REASONS)


# --------------------------------------------------------------------------
# Where the lexicon must refuse rather than guess
# --------------------------------------------------------------------------


def test_a_negated_success_word_is_not_success():
    """"not working" contains "working", which is how a naive lexicon inverts a
    verdict — the single most damaging error this module can make, because it moves
    an episode into the passing denominator."""
    for reason in (
        "not working yet",
        "doesn't work",
        "still does not work",
        "never worked",
    ):
        result = verdict.classify(issue(close_reason=reason))
        assert result["verdict"] != "accepted", reason


def test_a_reason_pointing_both_ways_is_ambiguous_and_therefore_unstated():
    """Refusing beats coin-flipping: an accepted here inflates the denominator and a
    rejected deflates it, and there is no evidence for either."""
    result = verdict.classify(
        issue(close_reason="fixed the parser but the integration tests are failing")
    )

    assert result["basis"] == "unstated"
    assert result["reason"] == "ambiguous"


# --------------------------------------------------------------------------
# The two overrides
# --------------------------------------------------------------------------


def test_an_interrupted_capture_downgrades_success_to_partial():
    """`abacus_partial` means the session ended without a clean close.

    Its token figure is a floor rather than a total, so calling it `accepted` would
    put a known-incomplete numerator into the most important measure.
    """
    result = verdict.classify(
        issue(
            close_reason="accepted: done",
            metadata={"abacus_schema": 1, "abacus_partial": True},
        )
    )

    assert result["verdict"] == "partial"
    assert result["partial"] is True
    assert result["basis"] == "structured", "the basis is how we know, not what we know"


def test_a_partial_capture_does_not_launder_a_rejection_into_partial():
    """A rejection is a rejection whether or not the capture was clean."""
    result = verdict.classify(
        issue(
            close_reason="rejected: wrong approach",
            metadata={"abacus_schema": 1, "abacus_partial": True},
        )
    )

    assert result["verdict"] == "rejected"


def test_a_close_with_no_abacus_capture_is_not_capture_ok():
    """`closed_at` with no `abacus_schema` means the episode closed unattributed.

    It still counts in the autonomy denominators — the work happened — but it is
    excluded from tokens per outcome, because there is no token figure to include.
    """
    result = verdict.classify(
        issue(close_reason="accepted: done", metadata={"hfit_verdict": "accepted"})
    )

    assert result["capture_ok"] is False
    assert result["verdict"] == "accepted", "the outcome is known; only the cost is not"


def test_a_captured_close_is_capture_ok():
    assert verdict.classify(issue())["capture_ok"] is True


def test_a_string_valued_partial_flag_does_not_read_as_true_by_truthiness():
    """`bd` preserves JSON types, so `abacus_partial` is a real bool — but a hand-set
    metadata key could be the string `"false"`, which is truthy and would downgrade
    every verdict on the issue."""
    result = verdict.classify(
        issue(
            close_reason="accepted: done",
            metadata={"abacus_schema": 1, "abacus_partial": "false"},
        )
    )

    assert result["verdict"] == "accepted"
    assert result["partial"] is False


def test_a_missing_metadata_block_does_not_raise():
    result = verdict.classify({"id": "Proj-abc", "closed_at": "2026-09-15T08:35:23Z"})

    assert result["verdict"] == "unstated"
    assert result["capture_ok"] is False


# --------------------------------------------------------------------------
# Coverage — the headline number that gates the priority-1 measure
# --------------------------------------------------------------------------


def test_coverage_is_the_share_of_episodes_whose_outcome_is_stated():
    verdicts = [
        verdict.classify(issue(close_reason="accepted: a")),
        verdict.classify(issue(close_reason="rejected: b")),
        verdict.classify(issue(close_reason="Closed")),
        verdict.classify(issue(close_reason="Closed")),
    ]

    cov = verdict.coverage(verdicts)

    assert cov["coverage"] == 0.5
    assert cov["n"] == 4
    assert cov["stated"] == 2


def test_coverage_over_no_episodes_is_unknown_and_not_zero():
    """0/0 rendered as 0.0 is a claim that the user recorded no outcomes.

    The truth is that there is nothing to compute over, and a report saying "0%
    coverage" would send someone looking for a habit problem they do not have.
    """
    cov = verdict.coverage([])

    assert cov["coverage"] is None
    assert cov["n"] == 0
    assert cov["reason"]


def test_coverage_reports_the_basis_mix_so_a_weak_majority_is_visible():
    """60% coverage built entirely from the lexicon is a different fact from 60%
    built from structured prefixes, and the headline number cannot show that."""
    verdicts = [
        verdict.classify(issue(close_reason="accepted: a")),
        verdict.classify(issue(close_reason="all green now")),
        verdict.classify(issue(close_reason="Closed")),
    ]

    by_basis = verdict.coverage(verdicts)["by_basis"]

    assert by_basis["structured"] == 1
    assert by_basis["lexical"] == 1
    assert by_basis["unstated"] == 1


def test_a_partial_verdict_still_counts_as_stated():
    """Coverage asks whether the outcome was recorded, not whether it was a success."""
    verdicts = [
        verdict.classify(
            issue(
                close_reason="accepted: done",
                metadata={"abacus_schema": 1, "abacus_partial": True},
            )
        )
    ]

    assert verdict.coverage(verdicts)["coverage"] == 1.0


def test_every_rung_above_unstated_counts_toward_coverage():
    """One episode per stated basis, so dropping any single rung from the numerator
    shows up here.

    The rung most at risk is `lexical`: it is the weakest computational read, and a
    project whose reasons are all free text would fall under `min_verdict_coverage`
    and have tokens per outcome refused — reported as a habit problem when the habit
    was there and the counting was wrong.
    """
    verdicts = [
        verdict.classify(
            issue(metadata={"abacus_schema": 1, "hfit_verdict": "accepted"})
        ),
        verdict.classify(issue(close_reason="accepted: a")),
        verdict.classify(issue(close_reason="all green now")),
        verdict.classify(
            issue(
                metadata={
                    "abacus_schema": 1,
                    "hfit_verdict": "accepted",
                    "hfit_verdict_basis": "inferential",
                }
            )
        ),
    ]
    bases = [record["basis"] for record in verdicts]
    assert bases == ["declared", "structured", "lexical", "inferential"], bases

    assert verdict.coverage(verdicts)["coverage"] == 1.0


def test_every_basis_the_module_can_emit_is_declared():
    """`BASES` is what a report's vocabulary binds to, so an undeclared basis would
    reach a skill with no instruction covering it — the same failure mode `GAP_KINDS`
    exists to prevent."""
    reasons = [
        "accepted: a",
        "all green now",
        "Closed",
        "see the thread",
    ]
    seen = {verdict.classify(issue(close_reason=r))["basis"] for r in reasons}
    seen.add(
        verdict.classify(
            issue(metadata={"abacus_schema": 1, "hfit_verdict": "accepted"})
        )["basis"]
    )

    assert seen <= set(verdict.BASES)
    assert "inferential" in verdict.BASES


def test_every_verdict_the_module_can_emit_is_declared():
    assert set(verdict.VERDICTS) == {
        "accepted",
        "rejected",
        "abandoned",
        "superseded",
        "partial",
        "unstated",
    }


def test_the_ladder_order_is_declared_and_ranks_inference_below_the_lexicon():
    order = list(verdict.LADDER)

    assert order.index("declared") < order.index("structured")
    assert order.index("structured") < order.index("lexical")
    assert order.index("lexical") < order.index("inferential")
    assert order.index("inferential") < order.index("unstated")


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------


def test_classify_does_not_mutate_the_issue_it_was_given():
    """The same issue dict is read by several measures; a classifier that annotated
    it in place would make their results order-dependent."""
    original = issue(close_reason="accepted: done")
    snapshot = repr(original)

    verdict.classify(original)

    assert repr(original) == snapshot


def test_classify_touches_no_file_and_runs_no_process(stub_bin, tmp_path, monkeypatch):
    """Pure by requirement, not by accident: it is what lets the measure layer be
    driven from tables of records with no filesystem at all.

    Observed from a directory of its own — `tmp_path` itself holds the isolation
    fixtures' `home` and `stubbin`, so asserting on it would have failed whatever the
    module did.
    """
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    home = str(tmp_path / "home")
    before = entries_under(home)

    verdict.classify(issue())
    verdict.coverage([verdict.classify(issue())])

    assert stub_bin.calls() == []
    assert list(cwd.iterdir()) == []
    assert entries_under(home) == before, "nothing new may appear under HOME either"
