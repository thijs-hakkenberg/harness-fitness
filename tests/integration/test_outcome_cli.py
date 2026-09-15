"""`outcome.py` — the one write surface a human drives, as a real subprocess.

This is the top rung of the verdict ladder, and the only rung a person can reach.
Everything below it is derived: `structured` reads a prefix off `close_reason`,
`lexical` guesses from free text, `inferential` is an LLM backfill ranked *below*
the guess (ADR-013). `declared` is the one basis that means someone said so.

Which makes the load-bearing property of this file a negative one: **the verdict
must not come from the model.** If a skill picks `accepted` because the work looked
finished, the `declared` rung becomes an inference wearing a declaration's badge,
and there is nothing downstream that can tell the two apart — `by_basis` would
report the most trustworthy mix this system can print over the least trustworthy
evidence it holds. `argparse` cannot enforce that a human chose the value, so what
it can enforce is asserted instead: the vocabulary is exactly `verdict.DECLARABLE`,
and `partial` and `unstated` are refused at the boundary rather than dropped later.

Three further disciplines, all inherited rather than invented here:

- **Consent is the outer gate**, and it is checked before `bd` runs at all — not
  before the write is *stored*, before the process starts (ADR-014).
- **A refusal is a report, not a crash.** Every documented refusal exits 0 with
  `ok: false` on stdout and nothing on stderr, because a skill has to branch on it;
  only a mistyped argv exits non-zero.
- **Absence is the encoding.** A refused run carries no `keys_written` and no
  `recorded` key at all, for the reason `fitness.py` carries no `measures`: both
  `null` and `0` are legitimate values of a successful run.

And one this surface adds, because it is the only place the two halves can come
apart: **the write and the ledger are separate facts.** `ok` says the declaration
reached beads; `recorded` says whether the ledger has read it back yet. A write
that lands while `bd list` fails is an honest partial success and the next `Stop`
hook finishes it, so reporting that as a failure would send the user to fix
something that is already done.
"""

import json
import os

import pytest

import consent
import hfit_config
import ledger
import state_store

SCRIPT = "outcome.py"
SNAPSHOT = "snapshot_composition.py"

# Measured against `bd` 1.1.2: `bd list --all --json` prints a bare array of this
# shape, with abacus's keys already on the issue's `metadata`.
CLOSED = {
    "id": "Proj-abc",
    "title": "a closed thing",
    "status": "closed",
    "issue_type": "task",
    "started_at": "2026-09-14T20:52:02Z",
    "closed_at": "2026-09-15T08:35:23Z",
    "close_reason": "Closed",
    "metadata": {
        "abacus_schema": 1,
        "abacus_partial": False,
        "abacus_tokens_total": 8134206,
        "abacus_tool_calls": 57,
    },
}

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
}

ENABLED_ONE = {"enabledPlugins": {"abacus@abacus": True}}


@pytest.fixture
def cfg():
    return hfit_config.load()


@pytest.fixture
def accepted(cfg):
    consent.record_acknowledgement(cfg)
    return cfg


def closed(**over):
    """One closed issue, overridable field by field."""
    out = dict(CLOSED)
    out["metadata"] = dict(CLOSED["metadata"])
    out.update(over)
    return out


def declared(value="accepted", **over):
    """The same issue as `bd` would return it *after* a declaration landed.

    The stub does not persist a `--set-metadata`, so this stands in for beads
    having stored one. It is what makes the read-back half of the surface testable
    at all: `recorded` is answered from the ledger, and the ledger is filled by a
    reconcile that reads `bd`.
    """
    issue = closed(**over)
    issue["metadata"]["hfit_verdict"] = value
    return issue


def payload(cwd):
    return {"cwd": str(cwd), "hook_event_name": "SessionStart", "source": "startup"}


def declare(run_cli, cwd, issue="Proj-abc", verdict="accepted", extra=(), env=None):
    """Run the CLI and parse its stdout, failing loudly on either count."""
    args = ["--issue", issue, "--verdict", verdict, "--json"] + list(extra)
    result = run_cli(SCRIPT, args, cwd=cwd, env=env)
    assert result.returncode == 0, result.stderr
    # A refusal is a report: stderr stays empty even when `ok` is false, because a
    # skill reads stdout and a person reading stderr would take a documented
    # decision for a fault.
    assert result.stderr == ""
    return json.loads(result.stdout)


def updates(stub_bin):
    """Every recorded `bd update` argv."""
    return [argv for argv in stub_bin.calls("bd") if argv[:1] == ["update"]]


def lists(stub_bin):
    return [argv for argv in stub_bin.calls("bd") if argv[:1] == ["list"]]


def metadata_pairs(argv):
    """The `k=v` arguments of one recorded `bd update`, as a dict."""
    out = {}
    for i, token in enumerate(argv):
        if token == "--set-metadata":
            key, _, value = argv[i + 1].partition("=")
            out[key] = value
    return out


@pytest.fixture
def project(run_hook, settings_tree, accepted):
    """An acknowledged project with a composition on record.

    Built by running the real `SessionStart` hook: an episode carries the
    `composition_digest` in force, and a fabricated record would let the two drift
    apart while both suites stayed green.
    """
    tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
    run_hook(SNAPSHOT, payload(tree.project))
    return tree


class TestWhatItWrites:
    def test_it_writes_the_verdict_through_bd(self, run_cli, stub_bin, project):
        stub_bin.on("bd", ["list"], stdout=[declared()])

        out = declare(run_cli, project.project)

        assert out["ok"] is True
        assert metadata_pairs(updates(stub_bin)[0])["hfit_verdict"] == "accepted"

    def test_the_basis_it_writes_is_declared(self, run_cli, stub_bin, project):
        # The whole point of the surface. `verdict.classify` reaches the `declared`
        # rung on `hfit_verdict` alone, so writing the basis is strictly redundant
        # for this machine — and it is written anyway, because the record is
        # Dolt-synced and the next reader has only the keys to go on.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        out = declare(run_cli, project.project)

        pairs = metadata_pairs(updates(stub_bin)[0])
        assert pairs["hfit_verdict_basis"] == "declared"
        assert out["basis"] == "declared"

    def test_it_never_writes_the_inferential_basis(self, run_cli, stub_bin, project):
        # `classify` sends `hfit_verdict` with `hfit_verdict_basis: "inferential"`
        # down a *lower* rung than a lexical guess (ADR-013). A human declaration
        # arriving with that hint would be silently demoted below the classifier it
        # was meant to override.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        declare(run_cli, project.project)

        assert "inferential" not in " ".join(updates(stub_bin)[0])

    def test_it_stamps_the_schema_beside_the_keys(self, run_cli, stub_bin, project):
        # `write_episode` stamps `hfit_schema` "only once there is something for it
        # to describe" — and here there is. Alone it would claim an index exists
        # where none does; beside two real keys it says which shape they are.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        out = declare(run_cli, project.project)

        assert metadata_pairs(updates(stub_bin)[0])["hfit_schema"] == "1"
        assert out["keys_written"] == 3

    def test_it_writes_in_one_invocation(self, run_cli, stub_bin, project):
        # `--set-metadata` merges, so three keys are three arguments and not three
        # processes. Each extra process is an independent chance to half-write the
        # index with nothing recording which half landed.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        declare(run_cli, project.project)

        assert len(updates(stub_bin)) == 1

    def test_it_echoes_what_it_was_asked_to_declare(self, run_cli, stub_bin, project):
        stub_bin.on("bd", ["list"], stdout=[declared()])

        out = declare(run_cli, project.project)

        assert out["issue_id"] == "Proj-abc"
        assert out["verdict"] == "accepted"
        assert out["project"] == str(project.project)

    @pytest.mark.parametrize("value", ["accepted", "rejected", "abandoned", "superseded"])
    def test_every_declarable_verdict_is_accepted(
        self, run_cli, stub_bin, project, value
    ):
        stub_bin.on("bd", ["list"], stdout=[declared(value)])

        out = declare(run_cli, project.project, verdict=value)

        assert out["ok"] is True
        assert metadata_pairs(updates(stub_bin)[0])["hfit_verdict"] == value


class TestTheVocabularyIsNarrowerThanTheLedgers:
    @pytest.mark.parametrize("value", ["partial", "unstated"])
    def test_a_derived_verdict_is_refused_at_the_boundary(
        self, run_cli, stub_bin, project, value
    ):
        # `beads_write` validates `hfit_verdict` against `VERDICTS`, all six, because
        # a *stored* record can legitimately carry either. Neither is declarable, and
        # for different reasons: `partial` is derived from `abacus_partial` and a
        # hand-set one could claim an interrupted session that abacus recorded as
        # clean — the one direction of that flag nothing can check. `unstated` says
        # nothing a missing key does not. So the narrowing happens here or nowhere.
        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", value, "--json"),
            cwd=project.project,
        )

        assert result.returncode != 0
        assert value in result.stderr
        assert updates(stub_bin) == []

    def test_a_nonsense_verdict_is_refused_too(self, run_cli, project):
        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", "great", "--json"),
            cwd=project.project,
        )

        assert result.returncode != 0

    def test_the_refusal_names_the_four_it_would_have_taken(self, run_cli, project):
        # A skill reading this back has to be able to correct itself, and a person
        # typing it has to learn the vocabulary from the error rather than the docs.
        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", "great", "--json"),
            cwd=project.project,
        )

        for value in ("accepted", "rejected", "abandoned", "superseded"):
            assert value in result.stderr


class TestTheLedgerReadBack:
    def test_a_landed_declaration_reports_recorded(self, run_cli, stub_bin, project):
        stub_bin.on("bd", ["list"], stdout=[declared()])

        out = declare(run_cli, project.project)

        assert out["recorded"] is True
        assert out["reconcile_reason"] is None

    def test_the_ledger_carries_the_declared_basis(
        self, run_cli, stub_bin, project, accepted
    ):
        # The end-to-end claim, and the only assertion that proves the write and the
        # classifier agree: a key written by this surface has to come back off
        # `verdict.classify` on the top rung, not one below it.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        declare(run_cli, project.project)

        records = ledger.latest_episodes(str(project.project), accepted)
        assert [r["verdict_basis"] for r in records] == ["declared"]
        assert [r["verdict"] for r in records] == ["accepted"]

    def test_re_declaring_the_same_verdict_still_reports_recorded(
        self, run_cli, stub_bin, project
    ):
        # The ledger is append-only *on movement*, so the second declaration appends
        # nothing and `moved` comes back empty. Inferring `recorded` from `moved`
        # would report the ledger as not holding a record it does hold — and the
        # user would declare a third time to fix a problem that never existed.
        stub_bin.on("bd", ["list"], stdout=[declared()])
        declare(run_cli, project.project)

        out = declare(run_cli, project.project)

        assert out["recorded"] is True

    def test_a_write_that_lands_while_the_read_fails_is_an_honest_partial(
        self, run_cli, stub_bin, project
    ):
        # `ok` is about the write and `recorded` is about the ledger, and this is the
        # case that forces them apart. The declaration is in beads; the next `Stop`
        # hook will reconcile it. Reporting this as a failure would send the user to
        # redo work that is already done.
        stub_bin.on("bd", ["list"], rc=1, stderr="no beads database found")

        out = declare(run_cli, project.project)

        assert out["ok"] is True
        assert out["keys_written"] == 3
        assert out["recorded"] is False
        assert out["reconcile_reason"] == "no_database"


class TestItReconcilesOnlyAfterItWrites:
    def test_a_refused_write_reconciles_nothing(self, run_cli, stub_bin, project):
        # Order, and it is not cosmetic. Reconciling after a refused write would
        # append an episode carrying the *old* basis while the command still looked
        # like it had done something, so the ledger would gain a reading nobody
        # asked for and the report would show the verdict unchanged.
        stub_bin.on("bd", ["update"], rc=1, stderr="no beads database found")

        out = declare(run_cli, project.project)

        assert out["ok"] is False
        assert out["reason"] == "no_database"
        assert lists(stub_bin) == []

    def test_a_bd_error_on_the_write_is_reported_not_raised(
        self, run_cli, stub_bin, project
    ):
        stub_bin.on("bd", ["update"], rc=2, stderr="something else went wrong")

        out = declare(run_cli, project.project)

        assert out["ok"] is False
        assert out["reason"] == "bd_error"

    def test_a_refused_write_carries_no_result_keys(self, run_cli, stub_bin, project):
        # Absent, not zeroed. `keys_written: 0` would be a claim — that the write ran
        # and wrote nothing — and `recorded: false` would say the ledger was read and
        # found wanting. Neither happened.
        stub_bin.on("bd", ["update"], rc=1, stderr="no beads database found")

        out = declare(run_cli, project.project)

        assert "keys_written" not in out
        assert "recorded" not in out
        assert "reconcile_reason" not in out


class TestConsentIsTheOuterGate:
    def test_an_unacknowledged_install_refuses(self, run_cli, stub_bin, tmp_path, cfg):
        out = declare(run_cli, tmp_path)

        assert out["ok"] is False
        assert out["reason"] == "not_acknowledged"

    def test_it_runs_no_bd_process_before_consent(
        self, run_cli, stub_bin, tmp_path, cfg
    ):
        # The stronger property, and the one ADR-014 actually asks for: not that the
        # write is discarded, that no process runs in the user's repository at all.
        declare(run_cli, tmp_path)

        assert stub_bin.calls("bd") == []

    def test_it_writes_nothing_before_consent(self, run_cli, stub_bin, tmp_path, cfg):
        # An empty directory is a trace. Asserted on existence rather than on a
        # listing, so a leaked `mkdir` of the very tree this guards cannot satisfy it.
        declare(run_cli, tmp_path)

        assert not os.path.exists(state_store.state_root())

    def test_the_refusal_says_what_to_run(self, run_cli, stub_bin, tmp_path, cfg):
        # The first-run experience. Without it, `/hfit:outcome` printing a bare
        # `not_acknowledged` reads as a broken plugin rather than an unmade decision.
        out = declare(run_cli, tmp_path)

        assert "acknowledge" in out["note"].lower()

    def test_a_moved_governing_key_refuses_distinctly(
        self, run_cli, stub_bin, project, accepted
    ):
        # `not_acknowledged` and `config_changed` present as the same silence and
        # have different remedies, which is why they are two reasons and not one.
        path = hfit_config.config_path()
        with open(path, "w") as fh:
            json.dump({"beads": {"write_metadata": True}, "otel": {"enabled": False}}, fh)

        out = declare(run_cli, project.project)

        assert out["ok"] is False
        assert out["reason"] == "config_changed"


class TestTheWriteToggleIsHonoured:
    def test_disabled_metadata_writes_refuse(self, run_cli, stub_bin, project):
        # `beads_write.set_metadata` does not consult this toggle — only the episode
        # writers do — so honouring it is this surface's own obligation. The
        # alternative reading, that an explicit human declaration outranks the
        # automatic index, was rejected: it would leave `beads.write_metadata`
        # meaning two different things depending on who asked.
        path = hfit_config.config_path()
        with open(path, "w") as fh:
            json.dump({"beads": {"write_metadata": False}}, fh)
        consent.record_acknowledgement(hfit_config.load())

        out = declare(run_cli, project.project)

        assert out["ok"] is False
        assert out["reason"] == "writes_disabled"

    def test_disabled_metadata_writes_run_no_bd_process(
        self, run_cli, stub_bin, project
    ):
        # "No `bd` process at all, not a suppressed argument. The ledger is the store
        # of record, so this whole index is optional and saying no must mean it."
        path = hfit_config.config_path()
        with open(path, "w") as fh:
            json.dump({"beads": {"write_metadata": False}}, fh)
        consent.record_acknowledgement(hfit_config.load())

        declare(run_cli, project.project)

        assert stub_bin.calls("bd") == []

    def test_consent_outranks_the_toggle(self, run_cli, stub_bin, tmp_path):
        # Both are refusals and only one of them is the user's next action. An
        # unacknowledged install that also has writes off must say
        # `not_acknowledged`, or the first-run message names a setting the user never
        # touched instead of the decision they have not made.
        path = hfit_config.config_path()
        # Created by hand here, and only here. The two tests above get the state
        # directory from `record_acknowledgement`; this one must not acknowledge, and
        # a config file is exactly what a user editing settings before consenting
        # would leave behind.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"beads": {"write_metadata": False}}, fh)

        out = declare(run_cli, tmp_path)

        assert out["reason"] == "not_acknowledged"


class TestNothingLeaks:
    def test_no_abacus_key_is_ever_written(self, run_cli, stub_bin, project):
        # The boundary that is asserted rather than assumed:
        # `TaskCostTracker/hooks/lib/attribution.py` is the only permitted
        # constructor of that namespace.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        declare(run_cli, project.project)

        for argv in stub_bin.calls("bd"):
            assert "abacus_" not in " ".join(argv)

    def test_every_key_it_writes_carries_the_prefix(self, run_cli, stub_bin, project):
        stub_bin.on("bd", ["list"], stdout=[declared()])

        declare(run_cli, project.project)

        for key in metadata_pairs(updates(stub_bin)[0]):
            assert key.startswith("hfit_")

    def test_a_hostile_issue_id_is_not_a_command(self, run_cli, stub_bin, project):
        # An id reaches this surface from a human or a model, and `shell=False` with
        # an explicit argv is what makes a metacharacter in it an inert string.
        stub_bin.on("bd", ["list"], stdout=[declared()])
        hostile = "Proj-abc; touch pwned"

        declare(run_cli, project.project, issue=hostile)

        written = updates(stub_bin)
        # Asserted before the loop: a `for` over an empty list passes vacuously and
        # would let a surface that ran no write at all look like a hardened one.
        assert len(written) == 1
        assert hostile in written[0]
        assert not os.path.exists(os.path.join(str(project.project), "pwned"))

    def test_it_writes_no_issue_text(self, run_cli, stub_bin, project):
        # The ledger holds measurements, not a copy of the issue tracker. The verdict
        # already carries everything a measure needs from the text.
        stub_bin.on("bd", ["list"], stdout=[declared(close_reason="secret rationale")])

        declare(run_cli, project.project)

        blob = json.dumps(ledger.episodes(str(project.project)))
        assert "secret rationale" not in blob
        assert "a closed thing" not in blob


class TestTheCliContract:
    def test_the_issue_is_required(self, run_cli, project):
        result = run_cli(SCRIPT, ("--verdict", "accepted", "--json"), cwd=project.project)

        assert result.returncode != 0
        assert "--issue" in result.stderr

    def test_the_verdict_is_required(self, run_cli, project):
        result = run_cli(SCRIPT, ("--issue", "Proj-abc", "--json"), cwd=project.project)

        assert result.returncode != 0
        assert "--verdict" in result.stderr

    def test_an_unknown_flag_is_refused_loudly(self, run_cli, project):
        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", "accepted", "--nope"),
            cwd=project.project,
        )

        assert result.returncode != 0
        assert "--nope" in result.stderr

    def test_json_is_optional_and_prose_is_the_default(
        self, run_cli, stub_bin, project
    ):
        # Unlike `fitness.py`, whose every consumer is a skill calling `json.loads`.
        # This one is typed by a person as often as it is called, and a wall of JSON
        # in answer to "mark this accepted" is a worse answer than a sentence.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", "accepted"),
            cwd=project.project,
        )

        assert result.returncode == 0
        assert "accepted" in result.stdout
        assert "Proj-abc" in result.stdout

    def test_a_refusal_is_prose_too_and_still_exits_zero(
        self, run_cli, stub_bin, tmp_path, cfg
    ):
        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", "accepted"), cwd=tmp_path
        )

        assert result.returncode == 0
        assert result.stderr == ""
        assert "acknowledge" in result.stdout.lower()

    def test_it_does_not_block_on_stdin(self, run_cli, stub_bin, project):
        # `run_cli` closes stdin, so a surface that read it would return here — but
        # under a skill stdin is a pipe that never closes, and the same code hangs
        # until the timeout and looks like a broken session.
        stub_bin.on("bd", ["list"], stdout=[declared()])

        result = run_cli(
            SCRIPT, ("--issue", "Proj-abc", "--verdict", "accepted", "--json"),
            cwd=project.project, timeout=20,
        )

        assert result.returncode == 0

    def test_the_envelope_names_the_version_that_produced_it(
        self, run_cli, stub_bin, project
    ):
        stub_bin.on("bd", ["list"], stdout=[declared()])

        out = declare(run_cli, project.project)

        assert out["plugin_version"]
        assert out["schema"] == 1
