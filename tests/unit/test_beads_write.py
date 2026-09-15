"""`beads_write` — the bounded cross-machine index, and the namespace boundary.

The ledger is the store of record. These keys exist so that a verdict reached on this
machine Dolt-syncs to the others, the way abacus's token figures already do, without
the ledger having to sync at all. That makes this module a *write* to someone else's
data store, and the tests below are mostly about the ways such a write can be wrong
while still returning success.

**The namespace rule is the reason this module exists at all.**
`Plugin.ClaudeCode.TaskCostTracker/hooks/lib/attribution.py` is the only permitted
constructor of the `abacus_*` namespace. Two tools writing one namespace means
whichever ran last wins and neither knows it lost, so the check here **raises** rather
than refusing: a refusal is a value a caller can ignore, and an ignored refusal is a
silent overwrite of abacus's figures. An `abacus_` key is never a condition of the
environment — it is a defect in this repository, and it should behave like one.

**Bounded is enforced, not intended.** Only declared keys may be written. A per-event
or per-anything key would grow the metadata without limit on an issue abacus is also
writing to, so an undeclared key raises for the same reason a misnamespaced one does.

**Unknown is absent, not zero — and here that rule has teeth it does not have
elsewhere.** At this step, autonomy, catches and the token basis have no
implementation, so eight of the declared keys have no value. Writing `0` for them
would not merely produce a wrong local number: it would sync a fabricated fact to
every other machine sharing the database, where nothing records that it was invented.
An absent key is the only honest encoding, and it is also the one a later step can
fill in.
"""

import json

import pytest

import beads_read
import beads_write
import consent
import hfit_config
import verdict as verdict_lib


# A record as `reconcile._episode` builds one. The fields the index draws on are real
# values from a measured close; the rest are present because the writer must ignore
# them rather than because it reads them.
EPISODE = {
    "issue_id": "Proj-abc",
    "closed_at": "2026-09-15T08:35:23Z",
    "started_at": "2026-09-14T20:52:02Z",
    "issue_type": "task",
    "composition_digest": "a1b2c3d4e5f6",
    "env_hash": "f" * 64,
    "model_basis": "observed",
    "verdict": "accepted",
    "verdict_basis": "structured",
    "verdict_reason": None,
    "lexicon_version": "lexical-v1",
    "capture_ok": True,
    "partial": False,
    "tokens_total": 8134206,
    "tool_calls": 57,
}


def episode(**over):
    out = dict(EPISODE)
    out.update(over)
    return out


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


def written(stub_bin):
    """The `k=v` pairs of the single `bd update` this module is allowed to make."""
    calls = stub_bin.calls("bd")
    assert len(calls) == 1, calls
    argv = calls[0]
    pairs = {}
    for index, token in enumerate(argv):
        if token == "--set-metadata":
            key, _, value = argv[index + 1].partition("=")
            pairs[key] = value
    return pairs


# --------------------------------------------------------------------------------
# The namespace boundary. Raises, because a refusal can be ignored.
# --------------------------------------------------------------------------------


def test_writing_an_abacus_key_raises(stub_bin, accepted, proj):
    """The one rule this module exists to make unbreakable.

    `abacus_tokens_total` is a real key abacus maintains from its own snapshot diff.
    Overwriting it would corrupt cost attribution across every machine sharing the
    database, and the corruption would look like a number.
    """
    with pytest.raises(ValueError):
        beads_write.set_metadata(
            proj, "Proj-abc", {"abacus_tokens_total": 1}, cfg=accepted
        )


def test_the_abacus_prefix_is_refused_even_alongside_valid_keys(
    stub_bin, accepted, proj
):
    """A partial write would be worse than none: some keys landed, the caller saw
    an exception, and nothing records which half succeeded. So the check happens
    before the subprocess, over the whole set."""
    with pytest.raises(ValueError):
        beads_write.set_metadata(
            proj,
            "Proj-abc",
            {"hfit_verdict": "accepted", "abacus_models": "claude-opus-5"},
            cfg=accepted,
        )

    assert stub_bin.calls("bd") == []


def test_a_key_with_no_hfit_prefix_raises(stub_bin, accepted, proj):
    """Not only `abacus_` is forbidden — anything outside our own namespace is
    someone else's field, including one no tool claims yet."""
    with pytest.raises(ValueError):
        beads_write.set_metadata(proj, "Proj-abc", {"verdict": "accepted"}, cfg=accepted)


def test_an_undeclared_hfit_key_raises(stub_bin, accepted, proj):
    """What makes "bounded" a property rather than an intention.

    The prefix alone would permit an unbounded set — a per-event or per-tool key
    would pass a prefix check and grow the metadata on an issue abacus also writes.
    """
    with pytest.raises(ValueError):
        beads_write.set_metadata(
            proj, "Proj-abc", {"hfit_tool_call_42": 1}, cfg=accepted
        )


def test_every_key_this_module_can_write_is_declared_and_prefixed(
    stub_bin, accepted, proj
):
    assert beads_write.KEYS, "the vocabulary is empty"
    for key in beads_write.KEYS:
        assert key.startswith(beads_write.PREFIX), key
        assert not key.startswith("abacus_"), key
    assert len(set(beads_write.KEYS)) == len(beads_write.KEYS), "duplicate key"


# --------------------------------------------------------------------------------
# Unknown is absent. Here an invented zero syncs to other machines as a fact.
# --------------------------------------------------------------------------------


def test_a_measure_with_no_implementation_yet_is_absent_not_zero(
    stub_bin, accepted, proj
):
    """Autonomy, catches and the token basis arrive in later steps.

    Until then these keys must not appear at all. `hfit_hard_touches: 0` would read
    on another machine as a fully autonomous episode — the most flattering possible
    value — with nothing anywhere recording that no one had measured it.
    """
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(), cfg=accepted)

    pairs = written(stub_bin)
    for key in (
        "hfit_hard_touches",
        "hfit_touch_kinds",
        "hfit_soft_touches",
        "hfit_autonomy_confidence",
        "hfit_ff_catches",
        "hfit_fb_catches",
        "hfit_escapes",
        "hfit_tokens_basis",
    ):
        assert key not in pairs, "%s was written without a measure behind it" % key


def test_a_null_value_is_omitted_rather_than_written_as_null(
    stub_bin, accepted, proj
):
    """An episode in a project with no composition record yet has no digest.

    `hfit_composition_digest=None` would round-trip as the string "None" and group
    every such episode together under a digest that does not exist.
    """
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(composition_digest=None), cfg=accepted)

    assert "hfit_composition_digest" not in written(stub_bin)


def test_the_keys_that_are_known_at_this_step_are_all_written(
    stub_bin, accepted, proj
):
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(), cfg=accepted)

    pairs = written(stub_bin)
    assert pairs["hfit_composition_digest"] == "a1b2c3d4e5f6"
    assert pairs["hfit_env_hash"] == "f" * 64
    assert pairs["hfit_verdict"] == "accepted"
    assert pairs["hfit_verdict_basis"] == "structured"
    assert pairs["hfit_capture_ok"] == "true"


def test_the_schema_is_always_written(stub_bin, accepted, proj):
    """A reader on another machine needs to know which shape it is reading before it
    can trust any other key — including, later, which keys were absent by design."""
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(), cfg=accepted)

    assert written(stub_bin)["hfit_schema"] == str(beads_write.SCHEMA)


def test_the_model_basis_travels_with_the_env_hash(stub_bin, accepted, proj):
    """ADR-007: the basis is not optional context, it is what makes the hash usable.

    A reader with `hfit_env_hash` and no basis cannot apply the refusal rule the ADR
    obliges — and this index exists precisely for the cross-machine case, which is
    where a model difference is most likely and least visible.
    """
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(model_basis="declared"), cfg=accepted)

    assert written(stub_bin)["hfit_model_basis"] == "declared"


# --------------------------------------------------------------------------------
# What may not be written at all.
# --------------------------------------------------------------------------------


def test_a_verdict_outside_the_vocabulary_is_not_written(stub_bin, accepted, proj):
    """Beads metadata is hand-editable and synced from other machines, so a verdict
    can arrive that this version does not know. Writing it back would launder an
    unrecognised string into the index other machines read as authoritative."""
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(verdict="looks_fine"), cfg=accepted)

    assert "hfit_verdict" not in written(stub_bin)


def test_every_verdict_the_ladder_can_produce_is_writable(stub_bin, accepted, proj):
    """The complement of the test above: the filter must not reject real verdicts.

    A validator that is stricter than the producer would silently drop the index for
    a whole class of episodes — `partial` and `unstated` most likely, being the
    derived and the dominant cases.
    """
    for name in verdict_lib.VERDICTS:
        stub_bin.reset()
        stub_bin.on("bd", ["update"], rc=0)

        beads_write.write_episode(proj, episode(verdict=name), cfg=accepted)

        assert written(stub_bin).get("hfit_verdict") == name, "%s was dropped" % name


def test_no_title_or_close_reason_text_is_written(stub_bin, accepted, proj):
    """The index carries measurements. Round-tripping the issue's own prose back
    into its metadata would store a stale copy of a field bd already owns."""
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(
        proj,
        episode(verdict_reason="accepted: it works", issue_type="task"),
        cfg=accepted,
    )

    blob = " ".join(" ".join(argv) for argv in stub_bin.calls("bd"))
    assert "it works" not in blob


def test_no_value_carries_whitespace(stub_bin, accepted, proj):
    """Every field a full episode produces, checked as a set.

    This is the regression net rather than the proof: today each of these values is
    either vocabulary-constrained or a hex digest, so the property holds without the
    rendering rule doing any work. It earns its keep when a later release adds a field
    whose values are free text — the assertion is already here, and it is over
    *whatever* the writer emitted rather than over a list someone has to remember to
    extend. The rule itself is proved below.
    """
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(
        proj, episode(model_basis="observed", verdict_basis="structured"), cfg=accepted
    )

    for key, value in written(stub_bin).items():
        assert value == "_".join(value.split()), "%s carries whitespace: %r" % (
            key,
            value,
        )


def test_a_value_containing_whitespace_is_collapsed_rather_than_truncated(
    stub_bin, accepted, proj
):
    """Measured on bd 1.1.2: a value containing a space arrives as two words and the
    second is dropped. A silently truncated value is the worst outcome available — it
    is neither an error nor the datum, and nothing downstream can tell which.

    Asserted through `set_metadata` because that is where the rule is reachable.
    `write_episode` only emits vocabulary-constrained strings and hex digests today, so
    a test through it cannot distinguish a writer that collapses whitespace from one
    that does not. `set_metadata` is the public entry point the later measures write
    through, and their values — a touch-kind list, an autonomy grade, a token basis —
    are constrained by nothing at this layer.
    """
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.set_metadata(
        proj,
        "Proj-abc",
        {"hfit_autonomy_confidence": "low  confidence\tunverified"},
        cfg=accepted,
    )

    value = written(stub_bin)["hfit_autonomy_confidence"]
    assert value == "low_confidence_unverified", value


# --------------------------------------------------------------------------------
# The subprocess: one call, one argument, and the rc distinction.
# --------------------------------------------------------------------------------


def test_the_whole_index_is_one_bd_invocation(stub_bin, accepted, proj):
    """`--set-metadata` merges, so one call carries every pair.

    One process per key would put six subprocesses on a `Stop` hook, on every close,
    for no gain — and each one is an independent chance to half-write the index.
    """
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(), cfg=accepted)

    assert len(stub_bin.calls("bd")) == 1


def test_the_keys_are_written_in_a_stable_order(stub_bin, accepted, proj):
    """Not cosmetic: an argv that varies between runs cannot be asserted on, and an
    un-assertable command line is one nobody notices a new key appearing in."""
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(), cfg=accepted)

    argv = stub_bin.calls("bd")[0]
    keys = [
        argv[i + 1].partition("=")[0]
        for i, token in enumerate(argv)
        if token == "--set-metadata"
    ]
    assert keys == sorted(keys), keys


def test_a_hostile_issue_id_arrives_as_one_argument(stub_bin, accepted, proj):
    """An issue id reaches this module from a `bd close` command line parsed out of a
    hook payload, which is untrusted input. `shell=False` with an explicit argv is
    what makes the shell metacharacter inert rather than a command."""
    stub_bin.on("bd", ["update"], rc=0)
    hostile = "Proj-abc; touch pwned"

    beads_write.write_episode(proj, episode(issue_id=hostile), cfg=accepted)

    assert hostile in stub_bin.calls("bd")[0]


def test_the_write_happens_in_the_project_directory(stub_bin, accepted, proj):
    """`bd` resolves its database by walking up from the cwd, so a write from the
    wrong directory lands in whichever database sits above this process."""
    stub_bin.on("bd", ["update"], rc=0)

    beads_write.write_episode(proj, episode(), cfg=accepted)

    assert stub_bin.invocations("bd")[0]["cwd"] == proj


def test_a_missing_database_is_a_different_reason_from_a_broken_bd(
    stub_bin, accepted, proj
):
    """One is benign — a project that does not use beads — and the other is a
    defect. Reporting them alike would make the benign case indistinguishable from
    the one worth investigating."""
    stub_bin.on("bd", ["update"], rc=1, stderr="no beads database found")
    absent = beads_write.write_episode(proj, episode(), cfg=accepted)

    stub_bin.on("bd", ["update"], rc=1, stderr="panic: index out of range")
    broken = beads_write.write_episode(proj, episode(), cfg=accepted)

    assert absent["reason"] == "no_database"
    assert broken["reason"] == "bd_error"
    assert absent["ok"] is False and broken["ok"] is False


def test_the_count_written_is_unknown_on_a_failure_and_never_zero(
    stub_bin, accepted, proj
):
    """`bd update` is not atomic across keys as far as this side can tell, so a
    failure leaves the number written genuinely unknown. `0` would claim it wrote
    nothing, which is a stronger statement than the evidence supports."""
    stub_bin.on("bd", ["update"], rc=1, stderr="panic: index out of range")

    result = beads_write.write_episode(proj, episode(), cfg=accepted)

    assert result["keys_written"] is None


def test_a_successful_write_reports_what_it_wrote(stub_bin, accepted, proj):
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episode(proj, episode(), cfg=accepted)

    assert result["ok"] is True
    assert result["reason"] is None
    assert result["keys_written"] == len(written(stub_bin))


def test_every_reason_this_module_can_return_is_declared(stub_bin, accepted, proj):
    """The vocabulary a caller binds to. An undeclared reason reaching a report is a
    string with no rule covering it."""
    for reason in ("no_database", "bd_error", "not_acknowledged", "writes_disabled"):
        assert reason in beads_write.REFUSALS, reason


# --------------------------------------------------------------------------------
# Consent, and the switch that turns this off.
# --------------------------------------------------------------------------------


def test_bd_is_not_invoked_before_consent(stub_bin, cfg, proj):
    """Checked before the subprocess, not after. An unacknowledged install must not
    execute a process in the user's repository, let alone write to its database
    (ADR-014)."""
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episode(proj, episode(), cfg=cfg)

    assert result["ok"] is False
    assert result["reason"] == "not_acknowledged"
    assert stub_bin.calls("bd") == []


def test_disabling_metadata_writes_prevents_the_subprocess(stub_bin, cfg, proj):
    """The ledger is the store of record, so this whole module is optional. Someone
    who does not want a measurement tool writing to their issue tracker must be able
    to say so and have it mean no `bd` process at all, not a suppressed argument.

    The flag is set *before* acknowledgement because `beads` is part of the governing
    fingerprint — flipping it afterwards is a different event, asserted below.
    """
    cfg["beads"] = dict(cfg.get("beads") or {}, write_metadata=False)
    consent.record_acknowledgement(cfg)
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episode(proj, episode(), cfg=cfg)

    assert result["ok"] is False
    assert result["reason"] == "writes_disabled"
    assert result["keys_written"] is None
    assert stub_bin.calls("bd") == []


def test_turning_writes_off_after_acknowledging_reads_as_a_consent_change(
    stub_bin, accepted, proj
):
    """Which gate wins, asserted rather than assumed.

    `beads` is part of the governing fingerprint, so flipping `write_metadata` after
    acknowledgement invalidates consent — and consent is checked first. The outcome is
    the same silence either way, but the *reason* differs, and the remedies differ with
    it: one is re-acknowledging, the other is nothing at all.
    """
    accepted["beads"] = dict(accepted.get("beads") or {}, write_metadata=False)
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episode(proj, episode(), cfg=accepted)

    assert result["reason"] == "config_changed"
    assert stub_bin.calls("bd") == []


def test_an_episode_with_no_issue_id_is_refused_rather_than_written(
    stub_bin, accepted, proj
):
    """`bd update` with no id would either fail or, worse, match something else."""
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episode(proj, episode(issue_id=None), cfg=accepted)

    assert result["ok"] is False
    assert result["reason"] == "no_issue_id"
    assert stub_bin.calls("bd") == []


def test_an_episode_with_nothing_knowable_writes_no_command(
    stub_bin, accepted, proj
):
    """Every measure absent means there is nothing to index. A `bd update` carrying
    only `hfit_schema` would claim a record exists where none does."""
    stub_bin.on("bd", ["update"], rc=0)

    bare = dict((key, None) for key in EPISODE)
    bare["issue_id"] = "Proj-abc"

    result = beads_write.write_episode(proj, bare, cfg=accepted)

    assert result["ok"] is False
    assert result["reason"] == "nothing_to_write"
    assert stub_bin.calls("bd") == []


# --------------------------------------------------------------------------------
# Many episodes in one run, and the bound that keeps a hook inside its budget
# --------------------------------------------------------------------------------
#
# The first reconcile of a project with a closed backlog offers *every* issue as
# `new`. An unbounded loop would put one `bd update` per closed issue on a `Stop`
# hook with a 15 s budget, be killed partway through, and leave a partial index
# with nothing anywhere recording which half landed.
#
# Two rules bound it, and neither works without the other. The cap bounds the
# healthy case, where a write costs tens of milliseconds and twenty of them are
# nothing. The halt-on-environmental-failure rule bounds the pathological one,
# where a single write can cost `beads_read.TIMEOUT_SECONDS` — twenty of those
# would exceed the budget by an order of magnitude however small the cap was.


def all_written(stub_bin):
    """The issue ids of every `bd update` the run made, in order."""
    return [argv[1] for argv in stub_bin.calls("bd")]


def episodes(count, first=0):
    """`count` episodes, closed one minute apart, oldest first."""
    return [
        episode(
            issue_id="Proj-%03d" % index,
            closed_at="2026-09-15T08:%02d:00Z" % (index % 60),
        )
        for index in range(first, first + count)
    ]


def test_every_episode_offered_is_indexed(stub_bin, accepted, proj):
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(proj, episodes(3), cfg=accepted)

    assert result["ok"] is True
    assert result["written"] == 3
    assert sorted(all_written(stub_bin)) == ["Proj-000", "Proj-001", "Proj-002"]


def test_no_more_than_the_cap_reaches_bd_in_one_run(stub_bin, accepted, proj):
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(
        proj, episodes(beads_write.MAX_EPISODES_PER_RUN + 5), cfg=accepted
    )

    assert len(stub_bin.calls("bd")) == beads_write.MAX_EPISODES_PER_RUN
    assert result["written"] == beads_write.MAX_EPISODES_PER_RUN


def test_the_episodes_kept_when_the_cap_bites_are_the_newest_closed(
    stub_bin, accepted, proj
):
    """Which ones get dropped is a choice, so it is made deliberately. The newest
    closes are the ones a report is about to be run over; the far end of an old
    backlog is the least likely to be looked at."""
    stub_bin.on("bd", ["update"], rc=0)
    many = episodes(beads_write.MAX_EPISODES_PER_RUN + 3)

    beads_write.write_episodes(proj, many, cfg=accepted)

    newest = sorted(many, key=lambda record: record["closed_at"], reverse=True)
    expected = [record["issue_id"] for record in newest[: beads_write.MAX_EPISODES_PER_RUN]]
    assert sorted(all_written(stub_bin)) == sorted(expected)


def test_a_capped_run_says_that_it_was_capped(stub_bin, accepted, proj):
    """Otherwise the episodes past the cap are silently unindexed: a later
    reconcile reports them `unchanged`, so nothing ever offers them again."""
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(
        proj, episodes(beads_write.MAX_EPISODES_PER_RUN + 1), cfg=accepted
    )

    assert result["capped"] is True


def test_a_capped_run_counts_what_it_attempted_and_not_what_it_was_offered(
    stub_bin, accepted, proj
):
    """`episodes` is the size of the run, not the size of the batch. The caller
    already knows how many it offered; what it cannot know is how many the cap let
    through, and reporting the offered figure beside a `written` bounded by the cap
    would read as `MAX_EPISODES_PER_RUN` successes out of more attempts — a run that
    partly failed, rather than one that was deliberately bounded."""
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(
        proj, episodes(beads_write.MAX_EPISODES_PER_RUN + 3), cfg=accepted
    )

    assert result["episodes"] == beads_write.MAX_EPISODES_PER_RUN
    assert result["written"] == beads_write.MAX_EPISODES_PER_RUN
    assert result["refused"] == 0


def test_a_run_inside_the_cap_is_not_reported_as_capped(stub_bin, accepted, proj):
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(proj, episodes(2), cfg=accepted)

    assert result["capped"] is False


def test_the_cap_is_a_constant_and_not_a_setting(stub_bin, accepted, proj):
    """A bound that exists to keep a hook inside its timeout cannot be tunable
    without the timeout becoming a lie: a user who set it to 500 would turn every
    `Stop` into a hook killed partway through, leaving the partial index this cap
    exists to prevent. It is also not a governing config key, so making it one
    would not even cost a re-acknowledgement to notice."""
    assert isinstance(beads_write.MAX_EPISODES_PER_RUN, int)
    assert "max_episodes" not in json.dumps(hfit_config.DEFAULTS)


# --------------------------------------------------------------------------------
# Halting: the difference between one bad episode and a bad environment
# --------------------------------------------------------------------------------


def test_an_episode_the_writer_refuses_does_not_stop_the_others(
    stub_bin, accepted, proj
):
    """`no_issue_id` is a property of one record. Stopping on it would let a single
    malformed row in a Dolt-synced database prevent every later episode in the
    project from ever being indexed."""
    stub_bin.on("bd", ["update"], rc=0)
    offered = episodes(2) + [episode(issue_id=None)]

    result = beads_write.write_episodes(proj, offered, cfg=accepted)

    assert result["ok"] is True
    assert result["written"] == 2
    assert result["refused"] == 1
    assert len(stub_bin.calls("bd")) == 2


def test_a_project_with_no_database_stops_after_the_first_attempt(
    stub_bin, accepted, proj
):
    """`no_database` is a property of the project, not the episode, so every
    remaining write is already known to fail. Paying for them would be a hook
    that gets slower the more work the project has done."""
    stub_bin.on("bd", ["update"], rc=1, stderr="no beads database found")

    result = beads_write.write_episodes(proj, episodes(5), cfg=accepted)

    assert len(stub_bin.calls("bd")) == 1
    assert result["reason"] == "no_database"
    assert result["written"] == 0


def test_a_timeout_is_paid_once_and_not_once_per_episode(
    stub_bin, accepted, proj
):
    """The rule that makes the cap safe. A write can cost
    `beads_read.TIMEOUT_SECONDS`; the cap's worth of those would exceed the
    `Stop` budget many times over, whatever the cap was set to."""
    stub_bin.on("bd", ["update"], rc=124, stderr="timed out")

    result = beads_write.write_episodes(proj, episodes(5), cfg=accepted)

    assert len(stub_bin.calls("bd")) == 1
    assert result["reason"] in beads_read.REASONS


def test_a_halted_run_still_reports_what_it_managed_to_write(
    stub_bin, accepted, proj
):
    """`ok` says the counts can be believed, not that every write succeeded. The
    number written before a halt is genuinely known, and `None` would throw away
    an observation.

    `Proj-000` is the oldest close, so under the newest-first ordering it is the
    one reached last — which also pins that the halt happens where the failure is
    rather than at the end of the loop."""
    stub_bin.on("bd", ["update"], rc=0)
    stub_bin.on("bd", ["update", "Proj-000"], rc=1, stderr="no beads database found")

    result = beads_write.write_episodes(proj, episodes(3), cfg=accepted)

    assert result["ok"] is True
    assert result["written"] == 2
    assert result["reason"] == "no_database"


# --------------------------------------------------------------------------------
# The gate, and the empty cases
# --------------------------------------------------------------------------------


def test_the_whole_run_is_refused_before_consent(stub_bin, cfg, proj):
    """Checked once for the run rather than once per episode: an unacknowledged
    install must not run a process in the user's repository, and reporting the
    refusal as "three episodes refused" would name the wrong problem."""
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(proj, episodes(3), cfg=cfg)

    assert result["ok"] is False
    assert result["reason"] == "not_acknowledged"
    assert stub_bin.calls() == []


def test_disabling_metadata_writes_prevents_every_subprocess(stub_bin, cfg, proj):
    cfg["beads"]["write_metadata"] = False
    consent.record_acknowledgement(cfg)
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(proj, episodes(3), cfg=cfg)

    assert result["reason"] == "writes_disabled"
    assert stub_bin.calls() == []


def test_every_count_is_unknown_on_a_refused_run_and_never_zero(
    stub_bin, cfg, proj
):
    result = beads_write.write_episodes(proj, episodes(3), cfg=cfg)

    for key in ("episodes", "written", "refused", "capped"):
        assert result[key] is None, key


def test_being_offered_nothing_runs_no_command_and_is_not_a_failure(
    stub_bin, accepted, proj
):
    """Zero here is an observation rather than an unknown: we were offered an
    empty list and we know it."""
    result = beads_write.write_episodes(proj, [], cfg=accepted)

    assert result["ok"] is True
    assert result["episodes"] == 0
    assert result["capped"] is False
    assert stub_bin.calls() == []


def test_a_non_dict_in_the_list_is_dropped_rather_than_raising(
    stub_bin, accepted, proj
):
    """This list comes from `reconcile`, but a hook composes the two and a hook
    must not be the place a type error surfaces."""
    stub_bin.on("bd", ["update"], rc=0)

    result = beads_write.write_episodes(proj, [episode(), "Proj-abc", None], cfg=accepted)

    assert result["ok"] is True
    assert result["written"] == 1


def test_nothing_at_all_offered_is_not_a_crash(stub_bin, accepted, proj):
    result = beads_write.write_episodes(proj, None, cfg=accepted)

    assert result["ok"] is True
    assert result["episodes"] == 0
