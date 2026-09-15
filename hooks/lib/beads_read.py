"""Read-only access to the beads issue database — the episode population.

An episode is one closed beads issue, so every measure in this plugin ultimately
divides by something this module returned. That makes one distinction the module's
whole reason to exist: **`bd` failing is not `bd` reporting nothing.**

Three wire shapes were measured against `bd` 1.1.2 on a real database rather than
inferred from another plugin's source:

- `bd list --all --json` prints a **bare JSON array**. There is no object wrapper
  and no `issues` key.
- `bd show <id> --json` prints a **single-element array**, not the object.
- With no database, `bd` exits **1 with empty stdout**, putting the explanation on
  stderr. A reader that mapped non-zero to "no issues" would report a project with
  a hundred closed episodes as a project with none, and every number computed from
  that would be a confident zero about work nobody looked at.

So each function returns `{"ok", "reason", "issues"}` (or `"issue"`), and on any
failure the payload is `None` — **never `[]`**. `None` says the question could not
be answered; `[]` says the answer is nothing. Collapsing them is the single bug
this file is shaped to make impossible.

Nothing here writes: no state, no metadata, no `bd` subcommand other than `list`
and `show`. Writing is `beads_write`'s job and is gated separately.
"""

import json
import os
import subprocess


BD = "bd"

# Comfortably inside the 20 s `PostToolUse` budget, and far outside any healthy
# `bd` read. A slow answer is worth waiting for; a hung one must not take the
# session's hook slot with it.
TIMEOUT_SECONDS = 10

# Measured on stderr when `bd` cannot resolve a database. Matched only to tell a
# benign "this project does not use beads" from a genuine failure, because those
# two want different sentences in a report. If the wording drifts, the read
# degrades to `bd_error` — a less specific reason, never a wrong answer.
_NO_DB_MARKER = "no beads database"

REASONS = (
    "no_database",
    "bd_unavailable",
    "bd_error",
    "timeout",
    "unparseable",
    "not_found",
)


def _run(cwd, args):
    """Invoke `bd` in `cwd` and return `(records, reason)`, exactly one of them set.

    The `cwd` is load-bearing rather than incidental: `bd` resolves *which*
    database it reads from its working directory. Inheriting the caller's would
    read whichever database sits above the session's process — producing a
    plausible list of another project's issues, which is a wrong answer and not an
    error.
    """
    argv = [BD] + list(args)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (OSError, ValueError):
        # FileNotFoundError for an absent `bd`, PermissionError for one that cannot
        # be executed, NotADirectoryError for a project path that has gone away.
        return None, "bd_unavailable"

    if proc.returncode == 127:
        return None, "bd_unavailable"

    stderr = _text(proc.stderr)
    if proc.returncode != 0:
        if _NO_DB_MARKER in stderr.lower():
            return None, "no_database"
        return None, "bd_error"

    # stderr is captured to keep it off the terminal and is never fed to the
    # parser: `bd` prints hints there on success too.
    parsed = _parse(proc.stdout)
    if parsed is None:
        return None, "unparseable"
    return parsed, None


def _text(raw):
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw or ""


def _parse(raw):
    """Return the top-level array, or `None` if stdout was not one.

    A non-array top level is refused rather than accommodated. Reaching into a
    hypothetical `{"issues": [...]}` wrapper would mean implementing a shape nobody
    has observed; refusing it means that the day `bd` changes, the reason field
    says which day that was instead of a measure silently changing value.
    """
    text = _text(raw).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None
    return payload


def _issues(records):
    """Keep the records that could be an episode, dropping the rest.

    An id-less record cannot be one — nothing can be recorded against it and it
    cannot be deduplicated in `episodes.jsonl` — so it is dropped rather than
    refused, matching `state_store.read_jsonl`. One malformed row must not cost the
    whole population.
    """
    out = []
    for record in records:
        if isinstance(record, dict) and record.get("id"):
            out.append(record)
    return out


def list_issues(cwd):
    """Every issue in the project's database, open and closed.

    `--all` is not a nicety: `bd list` defaults to open issues, and every episode
    is a closed one, so the default would return none of the population.
    """
    records, reason = _run(cwd, ["list", "--all", "--json"])
    if reason is not None:
        return {"ok": False, "reason": reason, "issues": None}
    return {"ok": True, "reason": None, "issues": _issues(records)}


def closed_issues(cwd):
    """The episode population: issues carrying a `closed_at`.

    The boundary is the timestamp and not the `status` label, because an episode
    without `closed_at` has no window to attribute anything to — measures over it
    would cover an undefined interval.
    """
    result = list_issues(cwd)
    if not result["ok"]:
        return result
    result["issues"] = [i for i in result["issues"] if i.get("closed_at")]
    return result


def show(cwd, issue_id):
    """One issue by id, unwrapped from the single-element array `bd` prints.

    A missing issue is `ok` with `issue: None` and `reason: "not_found"`, because
    `bd` answered: the database says no such id. That is a fact a caller can act
    on, unlike "the database could not be read", and the two must not arrive
    looking alike.
    """
    records, reason = _run(cwd, ["show", issue_id, "--json"])
    if reason is not None:
        return {"ok": False, "reason": reason, "issue": None}
    issues = _issues(records)
    if not issues:
        return {"ok": True, "reason": "not_found", "issue": None}
    return {"ok": True, "reason": None, "issue": issues[0]}


def available():
    """Whether a `bd` binary is on PATH at all.

    Separate from a read so a report can say "beads is not installed" rather than
    attributing the absence of episodes to the user having done no work.
    """
    path = os.environ.get("PATH") or ""
    for directory in path.split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, BD)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return False
