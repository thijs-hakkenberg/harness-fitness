#!/usr/bin/env python3
"""`outcome.py` — the one write surface a human drives, and the top verdict rung.

Everything below `declared` is derived: `structured` reads a prefix off
`close_reason`, `lexical` guesses from free text, `inferential` is an LLM backfill
ranked *below* the guess (ADR-013). `declared` is the one basis that means someone
said so, which makes the load-bearing property of this file a negative one: **the
verdict must not come from the model.** A skill that picks `accepted` because the
work looked finished turns the most trustworthy basis this system can print into an
inference wearing a declaration's badge, and nothing downstream can tell the two
apart. `argparse` cannot enforce that a human chose the value; what it can enforce
is enforced instead — the vocabulary is exactly `verdict.DECLARABLE`, so `partial`
and `unstated` are refused at the boundary rather than dropped later.

`beads_write` validates `hfit_verdict` against all six `VERDICTS`, because a
*stored* record can legitimately carry either of those two. Neither is declarable,
and for different reasons: `partial` is derived from `abacus_partial`, and a
hand-set one could claim an interrupted session that abacus recorded as clean —
the one direction of that flag nothing can check. `unstated` says nothing a missing
key does not. So the narrowing happens here or nowhere.

**Consent is the outer gate**, checked before `bd` runs at all — not before the
write is stored, before the process starts (ADR-014). `beads_write.set_metadata`
checks it too and that re-check is harmless, but it is not the gate: it does *not*
honour `beads.write_metadata`, which only `write_episode` and `write_episodes` do,
so this surface checks that toggle itself. A `/hfit:outcome` built naively on
`set_metadata` alone would write with the index switched off.

**A refusal is a report, not a crash.** Every documented refusal exits 0 with
`ok: false` on stdout and nothing on stderr, because a skill has to branch on it.
Only a mistyped argv exits non-zero, and then `argparse` owns the message.

**Absence is the encoding.** A refused run carries no `keys_written`, no
`recorded` and no `reconcile_reason` key at all, for the reason `fitness.py`
carries no `measures`: `keys_written: 0` would claim the write ran and wrote
nothing, and `recorded: false` would claim the ledger was read and found wanting.
Neither happened.

And one thing this surface adds, because it is the only place the two halves can
come apart: **the write and the ledger are separate facts.** `ok` says the
declaration reached beads; `recorded` says whether the ledger has read it back yet.
A write that lands while `bd list` fails is an honest partial success that the next
`Stop` hook finishes, so reporting it as a failure would send the user to redo work
that is already done.
"""

import argparse
import json
import os
import sys

_HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_HOOKS, "lib"))

import beads_write  # noqa: E402
import consent  # noqa: E402
import hfit_config  # noqa: E402
import ledger  # noqa: E402
import reconcile as reconcile_lib  # noqa: E402
import verdict as verdict_lib  # noqa: E402

SCHEMA = 1

# Resolved from `__file__` rather than `$CLAUDE_PLUGIN_ROOT`, for the same reason the
# `sys.path` bootstrap above is: an environment variable can be stale or absent, and
# a record that names the wrong version is worse than one that admits it does not
# know.
_MANIFEST = os.path.join(os.path.dirname(_HOOKS), ".claude-plugin", "plugin.json")

# The basis this surface writes, and the only one it may write. Named once so the
# pair below and the echoed envelope key cannot drift apart.
_DECLARED = "declared"

# One sentence per refusal, naming the remedy. `fitness.py` carries a list of
# `gaps[]` because a report can be short of several things at once; a single-purpose
# command has exactly one thing to say, so this is a single string.
_NOTES = {
    "not_acknowledged": (
        "Nothing was written: this install has not been acknowledged. Run "
        "`python3 \"$CLAUDE_PLUGIN_ROOT/hooks/scripts/acknowledge.py\" --accept` to "
        "consent, then declare the outcome again."
    ),
    "config_changed": (
        "A governing configuration key changed since you acknowledged, so writing "
        "has stopped. Review the change and acknowledge again with "
        "`acknowledge.py --accept`."
    ),
    "writes_disabled": (
        "Beads metadata writing is switched off for this project "
        "(`beads.write_metadata` is false), so the verdict was not indexed on the "
        "issue. The episode ledger is the store of record and reconciliation still "
        "reads it; set `beads.write_metadata` to true if you want the cross-machine "
        "index back."
    ),
    "no_database": (
        "No beads database was found here, so there is no issue to write the verdict "
        "to. Run this from a project with a `.beads` database."
    ),
    "bd_unavailable": (
        "`bd` could not be run, so the verdict was not written. Install beads and "
        "make sure `bd` is on your PATH."
    ),
    "bd_error": (
        "`bd` refused the update, so the verdict was not written. Check that the "
        "issue id exists with `bd show <id>`."
    ),
    "timeout": (
        "`bd` did not answer in time, so the verdict was not written. Nothing was "
        "half-applied: the keys travel in one invocation. Try again."
    ),
    "nothing_to_write": (
        "There was nothing to write, which should not be reachable from this "
        "command. Please report it."
    ),
}

# A refusal must always carry a sentence, including one added to a lower layer after
# this file was written. An unrecognised reason gets the reason itself rather than an
# empty string, because a skill printing a blank note would invent an explanation —
# the failure this whole surface exists to prevent.
_UNKNOWN_NOTE = (
    "The verdict was not written and the reason given was `{reason}`, which this "
    "version does not have a sentence for. Nothing was changed."
)


def plugin_version():
    """The version from the plugin manifest, or `"unknown"`.

    A stated absence rather than a fabricated `0.0.0`: this output gets pasted into
    an issue and read weeks later, and the reader has to be able to tell "the
    version could not be determined" from "the version was 0.0.0".
    """
    try:
        with open(_MANIFEST) as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return "unknown"
    if not isinstance(manifest, dict):
        return "unknown"
    version = manifest.get("version")
    return version if isinstance(version, str) and version else "unknown"


def project_root():
    """Which project's issue is being declared on.

    `$CLAUDE_PROJECT_DIR` first, then the process cwd — the same ordering
    `fitness.py` uses. It is load-bearing here in a way it is not there: `bd`
    resolves *which database* it reads and writes from its working directory, so a
    wrong answer would declare a verdict on another project's issue.
    """
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _envelope(cwd, issue_id, verdict_value):
    """The keys every run carries, whether the write succeeded or not.

    `issue_id` and `verdict` stay on both paths: a refusal a person reads has to
    say what it declined to do, and a skill retrying has to know what to retry.
    """
    return {
        "ok": None,
        "schema": SCHEMA,
        "plugin_version": plugin_version(),
        "project": cwd,
        "issue_id": issue_id,
        "verdict": verdict_value,
    }


def _refused(cwd, issue_id, verdict_value, reason):
    """A refused write: the envelope, a machine reason, and one sentence.

    Deliberately no `keys_written`, `recorded` or `reconcile_reason` — see the
    module docstring. The absence is the signal.
    """
    status = _envelope(cwd, issue_id, verdict_value)
    status["ok"] = False
    status["reason"] = reason
    status["note"] = _NOTES.get(reason) or _UNKNOWN_NOTE.format(reason=reason)
    return status


def _recorded(cwd, issue_id, cfg, reading):
    """Whether the ledger has read the declaration back yet.

    Answered from the *basis*, not from the value, and not from `moved`.

    Not the value, because `verdict.classify` downgrades a declared `accepted` to
    `partial` when `abacus_partial` is true while keeping the basis `declared`. The
    declaration was read back; a separate documented fact overrode what it said, and
    reporting `recorded: false` there would send the user to re-declare something
    that would change nothing.

    Not `moved`, because the ledger is append-only *on movement*: re-declaring the
    same verdict appends nothing, so `moved` comes back empty over a ledger that
    holds the record.

    Gated on `reconcile["ok"]` first, and that gate is what makes the basis test
    safe. A *prior* declaration already leaves `verdict_basis: "declared"` behind,
    so a second write that lands while `bd list` fails would otherwise report
    `recorded: true` beside a reconcile refusal — a false claim about a read that
    never happened.
    """
    if not reading["ok"]:
        return False
    for record in ledger.latest_episodes(cwd, cfg):
        if record.get("issue_id") == issue_id:
            return record.get("verdict_basis") == _DECLARED
    return False


def declare(cwd, issue_id, verdict_value, cfg):
    """Write the declaration, then read it back. In that order, and not the reverse.

    Reconciling first, or reconciling after a refused write, would append an episode
    carrying the *old* basis while the command still looked like it had done
    something: the ledger would gain a reading nobody asked for and the report would
    show the verdict unchanged.
    """
    allowed, reason = consent.require_consent(cfg)
    if not allowed:
        return _refused(cwd, issue_id, verdict_value, reason)

    # Checked here because `set_metadata` does not check it — only `write_episode`
    # and `write_episodes` do. The toggle has to mean no `bd` process at all rather
    # than a suppressed argument: the ledger is the store of record, so this whole
    # index is optional and saying no must mean it.
    if not (cfg.get("beads") or {}).get("write_metadata"):
        return _refused(cwd, issue_id, verdict_value, "writes_disabled")

    # `hfit_verdict_basis` is strictly redundant for this machine — `verdict.classify`
    # reaches the `declared` rung on `hfit_verdict` alone — and is written anyway,
    # because the record is Dolt-synced and the next reader has only the keys to go
    # on. `hfit_schema` travels beside two real keys, which is the condition
    # `write_episode` states for stamping it at all.
    written = beads_write.set_metadata(
        cwd,
        issue_id,
        {
            "hfit_verdict": verdict_value,
            "hfit_verdict_basis": _DECLARED,
            "hfit_schema": beads_write.SCHEMA,
        },
        cfg=cfg,
    )
    if not written["ok"]:
        return _refused(cwd, issue_id, verdict_value, written["reason"])

    reading = reconcile_lib.reconcile(cwd, cfg=cfg)

    status = _envelope(cwd, issue_id, verdict_value)
    status["ok"] = True
    status["basis"] = _DECLARED
    status["keys_written"] = written["keys_written"]
    status["recorded"] = _recorded(cwd, issue_id, cfg, reading)
    status["reconcile_reason"] = reading["reason"]
    return status


def render(status):
    """The default human form: one or two lines, no JSON.

    A skill passes `--json` and parses stdout. A person at a terminal gets prose,
    including on a refusal — which is why the refusal path prints the `note` rather
    than the machine `reason`: the sentence is the part that says what to do next.
    """
    if not status["ok"]:
        return "%s\n\n%s" % (
            "Not declared (%s)." % status["reason"],
            status["note"],
        )

    lines = [
        "Declared %s as %s (basis: declared, %d keys written)."
        % (status["issue_id"], status["verdict"], status["keys_written"])
    ]
    if status["recorded"]:
        lines.append("The episode ledger has read it back.")
    else:
        # Not a failure, and worth saying so in the same breath. The declaration is
        # in beads and the next `Stop` hook reconciles it.
        lines.append(
            "The declaration is stored, but the episode ledger has not read it back "
            "yet (%s). The next Stop hook will." % status["reconcile_reason"]
        )
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="outcome.py",
        description="Declare the outcome of one closed beads issue.",
    )
    parser.add_argument(
        "--issue",
        required=True,
        help="the beads issue id whose outcome is being declared",
    )
    # `choices` rather than a hand-written check: argparse names the offending value
    # *and* all four it would have taken, which is exactly what a skill correcting
    # itself and a person learning the vocabulary each need.
    parser.add_argument(
        "--verdict",
        required=True,
        choices=verdict_lib.DECLARABLE,
        help="the outcome a human is declaring for this issue",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the result as JSON on stdout instead of prose",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    cfg = hfit_config.load()
    status = declare(project_root(), args.issue, args.verdict, cfg)
    if args.json:
        # `sort_keys=False` so `ok` stays first, which is the order the contract is
        # stated in and the order a person reads a pasted result in.
        json.dump(status, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(status) + "\n")
    # Zero even on a refusal. A documented decision is a successful *report*, and a
    # skill that branched on the exit code would treat "you have not consented" as a
    # broken plugin.
    return 0


if __name__ == "__main__":
    sys.exit(main())
