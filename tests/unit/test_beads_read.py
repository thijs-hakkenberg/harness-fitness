"""Reading issues out of `bd` — the episode population.

An episode is one closed beads issue (§2 of the plan), so this module is where the
denominator of every measure comes from. That makes one distinction load-bearing
above all others: **`bd` failing is not `bd` reporting nothing.**

Three shapes were measured against `bd` 1.1.2 on a real database rather than
assumed, and each is a test here:

- `bd list --all --json` returns a **bare JSON array**, not an object with an
  `issues` key.
- `bd show <id> --json` returns a **single-element array**, not an object.
- With no database, `bd` exits **1 with empty stdout** and puts a hint on stderr.
  A reader that treats non-zero as "nothing found" reports a project with a
  hundred closed issues as a project with none — and every measure computed from
  that is a confident zero.

So the contract is `{"ok", "reason", "issues"}` with `issues` **`None` on any
failure and never `[]`**. `None` and `[]` are the whole point: one says the
question could not be answered, the other says the answer is nothing.
"""

import os

import pytest

import beads_read
import state_store

from conftest import entries_under


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
        "abacus_tokens_total": 8134206,
        "abacus_tool_calls": 57,
    },
}


def issue(**over):
    out = dict(ISSUE)
    out["metadata"] = dict(ISSUE["metadata"])
    for key, value in over.items():
        if key == "metadata" and value is not None:
            out["metadata"] = dict(value)
        else:
            out[key] = value
    return out


@pytest.fixture
def proj(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return str(path)


# --------------------------------------------------------------------------
# The distinction the whole module exists for
# --------------------------------------------------------------------------


def test_a_missing_database_is_not_an_empty_workspace(stub_bin, proj):
    """rc=1 with empty stdout, which is exactly what a missing database looks like.

    Read as `[]`, this turns every subsequent measure into a confident statement
    about work that was never looked at.
    """
    stub_bin.on(
        "bd",
        ["list"],
        rc=1,
        stdout="",
        stderr="Error: no beads database found\n",
    )

    result = beads_read.list_issues(proj)

    assert result["ok"] is False
    assert result["issues"] is None, "a failed read must never present as an empty one"
    assert result["reason"] == "no_database"


def test_a_broken_bd_is_distinguished_from_an_uninitialised_project(stub_bin, proj):
    """Both exit non-zero, and only one of them is the user's fault or worth saying.

    "This project does not use beads" is a benign, actionable sentence; "bd failed"
    is a defect. A single reason covering both would make the report either alarming
    or dismissive, and it would be wrong half the time.
    """
    stub_bin.on("bd", ["list"], rc=2, stdout="", stderr="panic: index out of range\n")

    assert beads_read.list_issues(proj)["reason"] == "bd_error"


def test_an_empty_database_is_distinguishable_from_a_failed_read(stub_bin, proj):
    """The other side of the same coin: rc=0 with `[]` really does mean nothing."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[])

    result = beads_read.list_issues(proj)

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["reason"] is None


# --------------------------------------------------------------------------
# The measured wire shapes
# --------------------------------------------------------------------------


def test_a_bare_array_is_the_list_shape(stub_bin, proj):
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue(), issue(id="Proj-def")])

    result = beads_read.list_issues(proj)

    assert result["ok"] is True
    assert [i["id"] for i in result["issues"]] == ["Proj-abc", "Proj-def"]


def test_show_unwraps_the_single_element_array(stub_bin, proj):
    """`bd show --json` wraps one issue in a list. Measured, not guessed."""
    stub_bin.on("bd", ["show"], rc=0, stdout=[issue()])

    result = beads_read.show(proj, "Proj-abc")

    assert result["ok"] is True
    assert isinstance(result["issue"], dict), "the array wrapper must not survive"
    assert result["issue"]["id"] == "Proj-abc"


def test_show_of_an_absent_issue_answers_rather_than_failing(stub_bin, proj):
    """An empty array is `bd` answering the question, so the read is `ok`.

    The caller needs these two apart: "the database says no such issue" is a fact
    it can act on, while "the database could not be read" is not.
    """
    stub_bin.on("bd", ["show"], rc=0, stdout=[])

    result = beads_read.show(proj, "Proj-nope")

    assert result["ok"] is True
    assert result["issue"] is None
    assert result["reason"] == "not_found"


def test_metadata_keeps_the_types_bd_gave_it(stub_bin, proj):
    """`abacus_tokens_total` is an int and `abacus_partial` a bool on the wire.

    A reader that stringified them would make `abacus_partial` truthy whatever it
    said, and `"false"` is exactly the value that must not downgrade a verdict.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    meta = beads_read.list_issues(proj)["issues"][0]["metadata"]

    assert meta["abacus_tokens_total"] == 8134206
    assert meta["abacus_partial"] is False
    assert meta["abacus_schema"] == 1


# --------------------------------------------------------------------------
# How `bd` is asked
# --------------------------------------------------------------------------


def test_the_whole_population_is_requested_not_just_the_open_issues(stub_bin, proj):
    """Every episode is a *closed* issue, so a default `bd list` returns none of them."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[])

    beads_read.list_issues(proj)

    argv = stub_bin.calls("bd")[0]
    assert "--all" in argv
    assert "--json" in argv


def test_bd_runs_in_the_project_directory(stub_bin, proj):
    """`bd` resolves which database to read from its working directory.

    Inheriting the caller's cwd would read whichever database sits above the
    session's process — a plausible list of issues belonging to another project,
    which is a wrong answer rather than an error.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout=[])

    beads_read.list_issues(proj)

    recorded = stub_bin.invocations("bd")[0]["cwd"]
    assert os.path.realpath(recorded) == os.path.realpath(proj)


def test_an_issue_id_reaches_bd_as_exactly_one_argument(stub_bin, proj):
    """Which is what `shell=False` buys, and the reason it is not optional here.

    Issue ids come from a `bd close` command line parsed out of a hook payload, so
    they are untrusted text. Under a shell, `x; rm -rf ~` would be two commands
    rather than one absurd id.
    """
    stub_bin.on("bd", ["show"], rc=0, stdout=[])
    hostile = "Proj-abc; touch pwned"

    beads_read.show(proj, hostile)

    argv = stub_bin.calls("bd")[0]
    assert hostile in argv, argv


# --------------------------------------------------------------------------
# Failing open, each failure named
# --------------------------------------------------------------------------


def test_bd_missing_from_path_is_reported_not_raised(monkeypatch, proj, tmp_path):
    """`bd` is optional until 0.2.0 and absent on plenty of machines."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    result = beads_read.list_issues(proj)

    assert result["ok"] is False
    assert result["issues"] is None
    assert result["reason"] == "bd_unavailable"


def test_a_timeout_is_not_an_empty_result(stub_bin, proj, monkeypatch):
    """A hung `bd` inside a hook budget must degrade to *unknown*, not to zero.

    The budget is squeezed rather than the stub slowed: no real interpreter starts,
    reads its rule file and prints inside a millisecond, so the deadline is missed
    by the process launch alone and the test does not spend a second sleeping.
    """
    monkeypatch.setattr(beads_read, "TIMEOUT_SECONDS", 0.001)
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])

    result = beads_read.list_issues(proj)

    assert result["ok"] is False
    assert result["issues"] is None
    assert result["reason"] == "timeout"


def test_unparseable_stdout_is_not_an_empty_result(stub_bin, proj):
    stub_bin.on("bd", ["list"], rc=0, stdout="Loading workspace...\nnot json at all\n")

    result = beads_read.list_issues(proj)

    assert result["ok"] is False
    assert result["issues"] is None
    assert result["reason"] == "unparseable"


def test_an_unexpected_top_level_shape_is_unparseable_rather_than_a_crash(
    stub_bin, proj
):
    """If a future `bd` wraps the array in an object, say so instead of guessing.

    Tolerating an unmeasured shape would mean asserting a behaviour nobody has
    observed; refusing it means the day `bd` changes, the reason field says which
    day that was.
    """
    stub_bin.on("bd", ["list"], rc=0, stdout={"issues": [issue()]})

    result = beads_read.list_issues(proj)

    assert result["ok"] is False
    assert result["reason"] == "unparseable"


def test_stderr_never_reaches_the_parser(stub_bin, proj):
    """`bd` prints hints to stderr on success too; they are not part of the JSON."""
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[issue()],
        stderr="Hint: run 'bd where' to inspect the resolved workspace\n",
    )

    result = beads_read.list_issues(proj)

    assert result["ok"] is True
    assert len(result["issues"]) == 1


def test_an_issue_with_no_id_is_dropped_rather_than_carried(stub_bin, proj):
    """An id-less record cannot be an episode: nothing can be recorded against it.

    Dropped rather than refused, matching `state_store.read_jsonl`. One malformed
    row must not cost the whole population.
    """
    stub_bin.on(
        "bd", ["list"], rc=0, stdout=[issue(), {"title": "no id"}, "a bare string"]
    )

    result = beads_read.list_issues(proj)

    assert result["ok"] is True
    assert [i["id"] for i in result["issues"]] == ["Proj-abc"]


# --------------------------------------------------------------------------
# The episode population
# --------------------------------------------------------------------------


def test_closed_issues_are_the_episode_population(stub_bin, proj):
    """`closed_at` is the only hard boundary in the system (§2)."""
    stub_bin.on(
        "bd",
        ["list"],
        rc=0,
        stdout=[
            issue(id="Proj-closed"),
            issue(id="Proj-open", status="open", closed_at=None, close_reason=None),
            issue(id="Proj-progress", status="in_progress", closed_at=None),
        ],
    )

    result = beads_read.closed_issues(proj)

    assert result["ok"] is True
    assert [i["id"] for i in result["issues"]] == ["Proj-closed"]


def test_a_status_of_closed_without_a_closed_at_is_not_an_episode(stub_bin, proj):
    """The boundary is the timestamp, not the label.

    Without `closed_at` there is no window to attribute anything to, so an episode
    built from it would carry measures over an undefined interval.
    """
    stub_bin.on(
        "bd", ["list"], rc=0, stdout=[issue(id="Proj-nots", closed_at=None)]
    )

    assert beads_read.closed_issues(proj)["issues"] == []


def test_a_failed_read_stays_failed_through_the_filter(stub_bin, proj):
    """The filter must not be the place the `None`/`[]` distinction gets lost."""
    stub_bin.on("bd", ["list"], rc=1, stdout="")

    result = beads_read.closed_issues(proj)

    assert result["ok"] is False
    assert result["issues"] is None


# --------------------------------------------------------------------------
# Reading is reading
# --------------------------------------------------------------------------


def test_reading_issues_writes_nothing(stub_bin, proj, isolated_home):
    """Asserted on *existence*, because a `makedirs` on the way to a read leaves a
    directory that no file listing can see (ADR-014)."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])
    root = state_store.state_root()

    beads_read.list_issues(proj)
    beads_read.closed_issues(proj)

    assert not os.path.exists(root), entries_under(os.path.dirname(root))


def test_no_bd_argv_mutates_anything(stub_bin, proj):
    """A reader that ever ran `bd update` or `bd close` would be a writer."""
    stub_bin.on("bd", ["list"], rc=0, stdout=[issue()])
    stub_bin.on("bd", ["show"], rc=0, stdout=[issue()])

    beads_read.list_issues(proj)
    beads_read.closed_issues(proj)
    beads_read.show(proj, "Proj-abc")

    for argv in stub_bin.calls("bd"):
        assert argv[0] in ("list", "show"), argv
        assert not any(
            arg.startswith("--set-metadata") or arg == "--reason" for arg in argv
        ), argv
