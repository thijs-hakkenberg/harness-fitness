"""`reconcile` — turning closed beads issues into episodes, from four triggers.

Discovery, not tracking. There is no session state to keep in step with and no
moment that must be caught: `reconcile` reads the whole closed population, hands
each issue to the ledger, and the ledger's append-on-movement rule makes running it
again free. Missing the instant of a `bd close` therefore costs nothing, which is
the property that lets this be wired to four hooks instead of one.

Three obligations meet in this module, and each is a test below.

**The `env_hash` is resolved at close time and never read off a composition
record** (ADR-007). `env_pin`'s own docstring records why: a model swap does not move
the `composition_digest`, so nothing is appended and the stored composition keeps its
original `env_hash`. An episode that inherited that value would claim the environment
the harness was *first* seen under, and the comparison layer's refusal on a differing
`env_hash` — the thing standing between "we changed a plugin" and "we changed the
model" — would be reading a number that had gone stale without saying so.

**`abacus_models` is the observed model.** abacus has already recorded, per episode,
which models actually ran, so the pin can be resolved on the strongest basis
available without touching OTEL. It is comma-joined and can name more than one, and
a multi-model episode is passed through whole rather than reduced to one of them:
picking would pool a mixed environment with the single-model population and move the
delta silently, where passing it through splits the population and fails loudly as
`insufficient_n`. Same asymmetry the digest is built on — over-splitting is loud,
under-splitting is a wrong number.

**A `bd` failure is not an empty population.** `beads_read` already refuses to
return `[]` for it; this layer must not undo that by reporting `0` episodes. Every
count is `None` on a failed read, because zero here is a claim about the project.
"""

import os

import pytest

import beads_read
import consent
import env_pin
import hfit_config
import hfit_time
import ledger
import reconcile as reconcile_lib
import state_store
import verdict as verdict_lib

from conftest import files_under


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
    """One issue as `bd list --all --json` really returns it (measured, bd 1.1.2)."""
    out = dict(ISSUE)
    out["metadata"] = dict(ISSUE["metadata"])
    for key, value in over.items():
        if key == "metadata":
            out["metadata"] = dict(value) if value is not None else None
        else:
            out[key] = value
    return out


def at(text):
    """`now` is a datetime throughout, matching `hfit_time.iso`."""
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


def only(cwd, cfg):
    records = ledger.latest_episodes(cwd, cfg)
    assert len(records) == 1, records
    return records[0]


# --------------------------------------------------------------------------
# Idempotence — the property that lets this hang off four triggers
# --------------------------------------------------------------------------


def test_a_closed_issue_becomes_an_episode(stub_bin, accepted, proj):
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["ok"] is True
    assert result["reason"] is None
    assert result["episodes"] == 1
    assert result["new"] == 1
    assert only(proj, accepted)["issue_id"] == "Proj-abc"


def test_running_it_again_over_the_same_state_appends_nothing(
    stub_bin, accepted, proj
):
    """Four triggers, and `Stop` fires on every turn.

    This is the whole reason `reconcile` can be wired anywhere convenient rather
    than to exactly the moment of a close: a second run is a no-op, so missing the
    moment costs nothing and catching it twice costs nothing either.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])
    reconcile_lib.reconcile(proj, cfg=accepted)

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["episodes"] == 1
    assert result["new"] == 0
    assert result["unchanged"] == 1
    assert len(ledger.episodes(proj, accepted)) == 1


def test_a_verdict_stated_after_the_close_is_picked_up_on_the_next_run(
    stub_bin, accepted, proj
):
    """The `/hfit:outcome` path end to end, which is W2's only mitigation.

    An episode reconciled at `Stop` is usually `unstated`. If the later reading
    never reached the ledger, `verdict_coverage` could not improve and the measure
    it gates would stay refused forever.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue(close_reason="Closed")])
    reconcile_lib.reconcile(proj, cfg=accepted)
    assert only(proj, accepted)["verdict"] == "unstated"

    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[
            issue(
                close_reason="Closed",
                metadata=dict(ISSUE["metadata"], hfit_verdict="accepted"),
            )
        ],
    )
    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["updated"] == 1
    latest = only(proj, accepted)
    assert latest["verdict"] == "accepted"
    assert latest["verdict_basis"] == "declared"


def test_only_closed_issues_become_episodes(stub_bin, accepted, proj):
    """`closed_at` is the boundary, not `status` — the same rule as `beads_read`.

    An open issue has no window to attribute anything to, and recording one would
    also make it eligible to be re-offered as a *different* episode every time
    someone touched it.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[issue(), issue(id="Proj-open", status="open", closed_at=None)],
    )

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["episodes"] == 1
    assert [r["issue_id"] for r in ledger.latest_episodes(proj, accepted)] == [
        "Proj-abc"
    ]


def test_every_closed_issue_is_reconciled_not_just_the_first(
    stub_bin, accepted, proj
):
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[
            issue(id="Proj-aaa"),
            issue(id="Proj-bbb", closed_at="2026-09-15T09:00:00Z"),
            issue(id="Proj-ccc", closed_at="2026-09-15T10:00:00Z"),
        ],
    )

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["new"] == 3
    assert len(ledger.latest_episodes(proj, accepted)) == 3


# --------------------------------------------------------------------------
# ADR-007 — the pin is resolved now, not inherited
# --------------------------------------------------------------------------


def test_the_env_hash_is_resolved_at_close_time(stub_bin, accepted, proj):
    """Not read off the composition record, however convenient that would be.

    A model swap leaves the `composition_digest` unmoved, so nothing is appended
    and the stored composition keeps the `env_hash` it was first seen under. An
    episode inheriting it would report an environment that had gone stale — and the
    comparison layer's `env_hash` refusal, which is what separates "we changed a
    plugin" from "we changed the model", would be checking a fossil.
    """
    ledger.record_composition(
        proj,
        {"digest": "a" * 12, "env_hash": "d" * 64},
        cfg=accepted,
    )
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=accepted)

    fresh = env_pin.build(os.path.expanduser("~"), proj, observed={"model": "claude-opus-5"})
    stored = only(proj, accepted)
    assert stored["env_hash"] == fresh["env_hash"]
    assert stored["env_hash"] != "d" * 64, "the composition's hash must not be inherited"


def test_the_model_basis_travels_beside_the_hash(stub_bin, accepted, proj):
    """ADR-007: the model is not declarable, so the basis must be stored.

    The comparison layer is required to refuse on a differing `model_basis` exactly
    as it refuses on a differing `env_hash`, and it can only do that if every
    episode carries one.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=accepted)

    assert only(proj, accepted)["model_basis"] in env_pin.MODEL_BASES


def test_the_model_abacus_observed_is_the_one_the_pin_is_built_from(
    stub_bin, accepted, proj
):
    """`abacus_models` is a measured fact about the episode, and it outranks both
    declarations.

    Measured on this machine, the two declared sources disagreed with each other
    *and* with reality: `settings.json` said `opus`, `env.ANTHROPIC_MODEL` said
    `claude-fable-5-innovation`, and every one of 389 OTEL `api_request` events
    reported `claude-opus-5`. No precedence rule over the declarations produces the
    right answer, so where an observation exists it is used.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[
            issue(metadata=dict(ISSUE["metadata"], abacus_models="claude-haiku-4-5"))
        ],
    )

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    expected = env_pin.build(
        os.path.expanduser("~"), proj, observed={"model": "claude-haiku-4-5"}
    )
    assert stored["model_basis"] == "observed"
    assert stored["env_hash"] == expected["env_hash"]


def test_an_episode_that_ran_on_two_models_is_not_reduced_to_one_of_them(
    stub_bin, accepted, proj
):
    """`abacus_models` is comma-joined, so a mixed environment is a real case.

    Choosing one would pool it with that model's single-model episodes and move the
    delta with nothing saying so. Passed through whole it lands in a population of
    its own, which is small and therefore refused as `insufficient_n` — the same
    over-split-loudly-rather-than-under-split-silently rule the composition digest
    is built on.
    """
    mixed = "claude-opus-5,claude-haiku-4-5"
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[issue(metadata=dict(ISSUE["metadata"], abacus_models=mixed))],
    )

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    solo = env_pin.build(
        os.path.expanduser("~"), proj, observed={"model": "claude-opus-5"}
    )
    assert stored["env_hash"] != solo["env_hash"], (
        "a mixed episode must not hash as though it ran on one of its models"
    )
    assert stored["model_basis"] == "observed"


def test_an_episode_with_no_observed_model_still_gets_a_pin(
    stub_bin, accepted, proj
):
    """An unattributed close, or one from before abacus was installed.

    The pin falls back to whatever is declared and says so through `model_basis`,
    rather than skipping the episode — the outcome is still known even where the
    environment is only claimed.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue(metadata={})])

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    assert stored["env_hash"]
    assert stored["model_basis"] != "observed"


def test_the_composition_digest_is_the_one_the_project_was_last_seen_under(
    stub_bin, accepted, proj
):
    ledger.record_composition(proj, {"digest": "c" * 12}, cfg=accepted)
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=accepted)

    assert only(proj, accepted)["composition_digest"] == "c" * 12


def test_an_episode_in_a_project_with_no_composition_yet_is_still_recorded(
    stub_bin, accepted, proj
):
    """`SessionStart` may never have run here — the plugin could have been installed
    mid-session, or this could be the `PostToolUse` of the very first turn.

    A `None` digest is honest and groupable-as-unknown. Refusing the episode would
    lose an outcome permanently to fix a field that the layer above already knows
    how to exclude.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["new"] == 1
    assert only(proj, accepted)["composition_digest"] is None


# --------------------------------------------------------------------------
# What lands in the record
# --------------------------------------------------------------------------


def test_the_verdict_and_its_basis_come_from_the_ladder(stub_bin, accepted, proj):
    stub_bin.on(
        "bd", ["list"], rc=0, stdout=[issue(close_reason="accepted: it works")]
    )

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    assert stored["verdict"] == "accepted"
    assert stored["verdict_basis"] == "structured"
    assert stored["capture_ok"] is True
    assert stored["partial"] is False


def test_a_lexical_verdict_carries_the_lexicon_version_that_produced_it(
    stub_bin, accepted, proj
):
    """A number computed under v1 has to stay attributable to v1 rather than be
    silently reinterpreted when the lexicon changes."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue(close_reason="all green, shipped")])

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    assert stored["verdict_basis"] == "lexical"
    assert stored["lexicon_version"] == verdict_lib.LEXICON_VERSION


def test_the_unstated_reason_is_carried_so_a_report_can_name_the_silence(
    stub_bin, accepted, proj
):
    """`bd_default` means teach the `accepted:` prefix; `no_signal` means people are
    writing reasons the lexicon cannot read. Different advice, so different names."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue(close_reason="Closed")])

    reconcile_lib.reconcile(proj, cfg=accepted)

    assert only(proj, accepted)["verdict_reason"] == "bd_default"


def test_the_token_and_tool_call_figures_come_from_abacus(stub_bin, accepted, proj):
    """Read-only, under their own names. This plugin never writes `abacus_*`."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    assert stored["tokens_total"] == 8134206
    assert stored["tool_calls"] == 57


def test_a_missing_token_figure_is_unknown_and_not_zero(stub_bin, accepted, proj):
    """An unattributed episode has no token figure, and `0` would be a real one.

    Summed into the numerator of tokens per outcome, a fabricated zero makes the
    harness look more efficient exactly where the measurement failed.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue(metadata={})])

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    assert stored["tokens_total"] is None
    assert stored["tool_calls"] is None
    assert stored["capture_ok"] is False


def test_a_string_valued_token_figure_is_refused_rather_than_summed(
    stub_bin, accepted, proj
):
    """Metadata is hand-editable and Dolt-synced from other machines.

    A string in this field would propagate to the numerator and fail there, far from
    the issue that caused it — or worse, concatenate.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[issue(metadata=dict(ISSUE["metadata"], abacus_tokens_total="lots"))],
    )

    reconcile_lib.reconcile(proj, cfg=accepted)

    assert only(proj, accepted)["tokens_total"] is None


def test_a_boolean_token_figure_does_not_arrive_as_a_count_of_one(
    stub_bin, accepted, proj
):
    """`bool` is a subclass of `int`, so an `isinstance` check alone lets it through.

    This is the one bad value that would not look bad: `true` becomes `1`, which sums
    into the numerator of tokens per outcome without any type ever being wrong. A
    string is refused loudly; this would be a plausible small number, and the episode
    it came from would look like the most efficient one in the population.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[
            issue(
                metadata=dict(
                    ISSUE["metadata"], abacus_tokens_total=True, abacus_tool_calls=True
                )
            )
        ],
    )

    reconcile_lib.reconcile(proj, cfg=accepted)

    record = only(proj, accepted)
    assert record["tokens_total"] is None
    assert record["tool_calls"] is None


def test_the_window_and_the_issue_type_are_carried(stub_bin, accepted, proj):
    """`started_at` bounds the window every OTEL-derived measure is computed over,
    and `issue_type` is what makes the `issue_type_mix_shifted` confounder
    detectable."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=accepted)

    stored = only(proj, accepted)
    assert stored["started_at"] == "2026-09-14T20:52:02Z"
    assert stored["closed_at"] == "2026-09-15T08:35:23Z"
    assert stored["issue_type"] == "task"


def test_no_title_or_close_reason_text_is_stored(stub_bin, accepted, proj):
    """The ledger holds measurements, not a copy of the issue tracker.

    A title is the most likely place for something a user would not expect to find
    in a file they forgot exists, and the verdict already carries everything a
    measure needs from the close reason.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[issue(title="migrate the SECRET_customer_list", close_reason="accepted: done")],
    )

    reconcile_lib.reconcile(proj, cfg=accepted)

    blob = open(ledger.episodes_path(proj, accepted)).read()
    assert "SECRET_customer_list" not in blob
    assert "accepted: done" not in blob


def test_reconcile_writes_nothing_back_to_beads(stub_bin, accepted, proj):
    """Reading and writing are separate commits and separate modules.

    A reconcile that also wrote metadata would put a `bd` write on the `Stop` path
    of every turn, and would make the read side untestable without a writable
    database.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=accepted)

    for argv in stub_bin.calls():
        assert argv[:1] == ["list"], argv


# --------------------------------------------------------------------------
# A failed read is not an empty population
# --------------------------------------------------------------------------


def test_a_missing_database_is_reported_and_nothing_is_written(
    stub_bin, accepted, proj
):
    """Benign: this project simply does not use beads.

    Distinguished from `bd_error` because the advice differs — one is "run `bd
    init`" and the other is "something is broken".
    """
    stub_bin.on("bd", ["list"], rc=1, stdout="", stderr="no beads database found\n")

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["ok"] is False
    assert result["reason"] == "no_database"
    assert not os.path.exists(ledger.episodes_path(proj, accepted))


def test_a_broken_bd_is_a_different_reason_from_a_missing_one(
    stub_bin, accepted, proj
):
    stub_bin.on("bd", ["list"], rc=2, stdout="", stderr="panic: index out of range\n")

    assert reconcile_lib.reconcile(proj, cfg=accepted)["reason"] == "bd_error"


def test_every_count_is_unknown_on_a_failed_read_and_never_zero(
    stub_bin, accepted, proj
):
    """The `beads_read` `None`-not-`[]` discipline, one layer up.

    `episodes: 0` says this project has closed no work. Read off a `bd` that could
    not answer, that is the confident-zero failure the whole contract exists to
    prevent — and it is indistinguishable from the true answer for a new project.
    """
    stub_bin.on("bd", ["list"], rc=1, stdout="", stderr="no beads database found\n")

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    for key in ("episodes", "new", "updated", "unchanged", "skipped"):
        assert result[key] is None, key


def test_a_failed_read_leaves_episodes_already_recorded_alone(
    stub_bin, accepted, proj
):
    """`bd` becoming unreadable is not evidence that past work did not happen."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])
    reconcile_lib.reconcile(proj, cfg=accepted)
    stub_bin.on("bd", ["list"], rc=1, stdout="", stderr="no beads database found\n")

    reconcile_lib.reconcile(proj, cfg=accepted)

    assert len(ledger.latest_episodes(proj, accepted)) == 1


def test_every_reason_reconcile_can_return_is_one_beads_read_declares(
    stub_bin, accepted, proj
):
    """Same binding as `BASES` and `GAP_KINDS`: a reason with no declared name
    reaches a report that has no sentence for it."""
    for rc, stderr, expected in (
        (1, "no beads database found\n", "no_database"),
        (2, "panic\n", "bd_error"),
    ):
        stub_bin.on("bd", ["list"], rc=rc, stdout="", stderr=stderr)
        reason = reconcile_lib.reconcile(proj, cfg=accepted)["reason"]
        assert reason == expected
        assert reason in beads_read.REASONS


# --------------------------------------------------------------------------
# What is skipped rather than fatal
# --------------------------------------------------------------------------


def test_one_unusable_issue_costs_one_episode_not_the_run(stub_bin, accepted, proj):
    """An issue with no id, which is dropped a layer below and must not abort here.

    Aborting on a malformed row would mean one bad record in a Dolt-synced database
    silently stops every measure in the project from ever updating again. The
    assertion is that the *good* issue still lands — the id-less one is `beads_read`'s
    to drop, and this test's job is to prove that dropping it costs nothing else.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[issue(id=None), issue(id="Proj-good")],
    )

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["ok"] is True
    assert result["new"] == 1
    assert [r["issue_id"] for r in ledger.latest_episodes(proj, accepted)] == [
        "Proj-good"
    ]


def test_a_non_dict_in_the_issue_list_does_not_raise(stub_bin, accepted, proj):
    """`bd`'s output is JSON from another machine's version of the tool.

    `reconcile` carries no `isinstance` guard of its own, because `beads_read` drops
    every non-dict before this layer sees one. This test is what makes that division
    safe: it fails here the moment that filter is loosened, rather than at whichever
    `.get()` a string first reaches.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=["not an issue", issue()])

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["ok"] is True
    assert result["new"] == 1


def test_an_episode_the_ledger_would_not_store_is_counted_rather_than_lost(
    stub_bin, accepted, proj
):
    """A write that fails is the one refusal `beads_read`'s filters cannot pre-empt.

    `episodes` counts what the ledger actually holds, so on a total write failure it
    is `0` — which on its own would be the confident zero this design refuses
    everywhere else. `skipped` is what makes the two distinguishable: `episodes: 0,
    skipped: 2` says the reconcile could not store anything, where `episodes: 0,
    skipped: 0` says the project has closed nothing.
    """
    path = ledger.episodes_path(proj, accepted)
    os.makedirs(path)  # a directory where the ledger expects a file
    stub_bin.on(
        "bd", ["list"], rc=0, stdout=[issue(id="Proj-aaa"), issue(id="Proj-bbb")]
    )

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["ok"] is True
    assert result["episodes"] == 0
    assert result["skipped"] == 2


def test_a_project_with_no_closed_issues_is_a_successful_read_of_nothing(
    stub_bin, accepted, proj
):
    """`ok` with zero episodes — the answer `bd` actually gave, unlike the failure
    cases above where the counts are `None`."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[])

    result = reconcile_lib.reconcile(proj, cfg=accepted)

    assert result["ok"] is True
    assert result["reason"] is None
    assert result["episodes"] == 0


# --------------------------------------------------------------------------
# Consent and time
# --------------------------------------------------------------------------


def test_nothing_is_written_before_consent(stub_bin, cfg, proj):
    """Asserted on the filesystem, because a module that created its root while
    resolving a path passes every other test here and still breaks the promise
    (ADR-014)."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    result = reconcile_lib.reconcile(proj, cfg=cfg)

    assert result["ok"] is False
    assert result["reason"] == "not_acknowledged"
    assert not os.path.exists(state_store.state_root()), files_under(
        os.path.dirname(state_store.state_root())
    )


def test_bd_is_not_even_consulted_before_consent(stub_bin, cfg, proj):
    """The consent gate is checked before the subprocess, not after it.

    Running `bd` first would mean an unacknowledged install still executed a
    process in the user's repository on every `Stop` — which is not "nothing
    happens", whatever the return value said.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, cfg=cfg)

    assert stub_bin.calls() == []


def test_the_supplied_clock_reaches_the_stored_record(stub_bin, accepted, proj):
    """`hfit_time.iso` returns `None` for anything that is not a datetime, so a
    reconcile passing the wrong type through would store `ts: null` on every
    episode and nothing would fail."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    reconcile_lib.reconcile(proj, now=at("2026-09-15T09:00:00Z"), cfg=accepted)

    assert ledger.episodes(proj, accepted)[0]["ts"] == "2026-09-15T09:00:00Z"
