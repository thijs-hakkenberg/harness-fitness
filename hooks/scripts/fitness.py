#!/usr/bin/env python3
"""`fitness.py --json` — the one read surface, and the only thing a skill parses.

Four properties are load-bearing, and each of them is a decision that could
plausibly have gone the other way.

**`ok` is answered before anything else, and a failed read carries no result keys
at all.** Not `measures: {}`, not `measures: null`, not `episodes_seen: 0` —
absent. `null` is already spoken for: at this version it is the *legitimate* value
of a successful read, because the measure layer does not exist yet. If a failed
read also said `null` the two would be indistinguishable, and a skill would report
"no measures available" where the honest answer is "consent was never given".
Absence of the key is the only encoding that cannot be confused with a result.

**Unknown is never zero, which is why `episodes_seen` is three-valued.** A number
that cannot be computed is `null` with a `gaps[]` entry saying why in a sentence a
person can act on. But `0` is not always the dishonest answer: an *absent* ledger
means nothing has been reconciled in this project, and reporting that as `0` is
exactly right, because the field counts what the ledger holds. A ledger that
*exists* and yields nothing is different — `state_store.append_jsonl` creates the
file only on an append that succeeded, so an empty read there is a fault on this
machine, and `0` would put that fault inside a number about the user's work where
nothing downstream could separate the two. That case is `null`.

`verdict_coverage` splits along the same seam and is worth stating separately,
because the two halves of it disagree: over an absent ledger `n` is `0` and correct,
while `coverage` is `null`, since a ratio over no denominator is not `0.0`. A `0.0`
there would send someone looking for a habit problem they do not have.

**This surface writes nothing, and that is a measurement property.** A reporting
command that creates state has changed the thing it reports on; before consent it
would also be creating that state in defiance of ADR-014. Every path below
resolves without creating: `ledger.root` is documented as resolve-only,
`state_store.project_dir` likewise, and `hfit_config.load` is a pure read.

**It does not read stdin.** Not a stylistic point: under a skill, stdin is a pipe
that is never closed, so a surface that reads it hangs the session until the hook
timeout and looks to the user like a plugin that has broken their session. The
tests close stdin rather than feeding it, so a regression here fails there.

Unlike a hook, this exits non-zero and complains on stderr when given something it
does not understand. A hook must be silent because its stdout is a protocol; this
is a human at a terminal, and silently ignoring a mistyped flag would print a full
report the reader takes as an answer to the question they thought they asked.
"""

import argparse
import json
import os
import sys

_HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_HOOKS, "lib"))

import consent  # noqa: E402
import hfit_config  # noqa: E402
import ledger  # noqa: E402
import verdict as verdict_lib  # noqa: E402

SCHEMA = 1

# Resolved from `__file__` rather than `$CLAUDE_PLUGIN_ROOT`, for the same reason
# the `sys.path` bootstrap above is: an environment variable can be stale or
# absent, and a report that names the wrong version is worse than one that admits
# it does not know.
_MANIFEST = os.path.join(os.path.dirname(_HOOKS), ".claude-plugin", "plugin.json")

# Every gap kind this version can emit, named once and used at the call sites below
# so the tuple and the calls cannot drift apart.
#
# The tuple exists for the skills. An undocumented kind does not fail anything: the
# model meets an unfamiliar string, writes a plausible sentence about it, and the user
# reads an invented explanation from a tool whose whole purpose is to not invent. So
# `test_skills.py` requires every member to be named in every SKILL.md, and this is
# what it binds to.
#
# Deliberately not enforced at runtime. A membership assertion in `_gap` would turn a
# typo into a traceback on a read surface that is obliged to be total.
_KIND_NOT_ACKNOWLEDGED = "not-acknowledged"
_KIND_CONFIG_CHANGED = "config-changed"
_KIND_NO_COMPOSITION = "no-composition-recorded"
_KIND_UNRESOLVED = "unresolved-composition"
_KIND_NO_EPISODES = "no-episodes-recorded"
_KIND_EPISODES_UNREADABLE = "episodes-unreadable"
_KIND_UNSTATED_VERDICT = "unstated-verdict"
_KIND_MEASURES = "measures-unavailable"

GAP_KINDS = (
    _KIND_NOT_ACKNOWLEDGED,
    _KIND_CONFIG_CHANGED,
    _KIND_NO_COMPOSITION,
    _KIND_UNRESOLVED,
    _KIND_NO_EPISODES,
    _KIND_EPISODES_UNREADABLE,
    _KIND_UNSTATED_VERDICT,
    _KIND_MEASURES,
)

_NOT_ACKNOWLEDGED = (
    "Nothing has been recorded yet: this install has not been acknowledged. Run "
    "`python3 \"$CLAUDE_PLUGIN_ROOT/hooks/scripts/acknowledge.py\" --accept` to "
    "consent, then start a session in this project."
)
_CONFIG_CHANGED = (
    "A governing configuration key changed since you acknowledged, so recording "
    "has stopped. Review the change and acknowledge again with "
    "`acknowledge.py --accept`."
)
_NO_COMPOSITION = (
    "No session has started in this project since the plugin was acknowledged, so "
    "there is no composition on record here yet. Open a session in this directory "
    "and this fills in."
)
_UNRESOLVED = (
    "The current composition's digest is known from the change log but its record "
    "file could not be read, so the profile and the environment pin are "
    "unavailable for it."
)
_NO_EPISODES = (
    "No episode has been reconciled in this project yet, so `episodes_seen` is 0 "
    "rather than unknown. An episode is one closed beads issue: close one with "
    "`bd close <id> --reason \"accepted: it works\"` and the next Stop hook records "
    "it."
)
_EPISODES_UNREADABLE = (
    "The episode ledger exists but yielded no records, so `episodes_seen` and "
    "`verdict_coverage` are null rather than 0. A ledger file is only created by an "
    "append that succeeded, so an empty read is a fault on this machine and not a "
    "fact about your work. Check the file's permissions and that it is a file."
)
# Interpolated, so the report states the number it is complaining about. A gap that
# said only "coverage is low" would leave the reader unable to tell a habit that is
# nearly there from one that has not started.
_UNSTATED_VERDICT = (
    "Only {actual}% of episodes here state an outcome, below the {minimum}% this "
    "project requires. Tokens per outcome is gated on that number (ADR-008) and "
    "will be refused until it rises, because an average over the minority of "
    "episodes whose success is known is not an average over the work. Close issues "
    "with `bd close <id> --reason \"accepted: …\"` — the `accepted:`, `rejected:`, "
    "`abandoned:` and `superseded:` prefixes are what make an outcome structured "
    "rather than guessed at. For an issue already closed without one, `/hfit:outcome` "
    "declares the verdict after the fact, which ranks above a prefix rather than "
    "merely repairing it."
)
_MEASURES = (
    "The four measures are not implemented at this version. This release records "
    "harness composition and episodes; the measures themselves arrive in 0.3.0 "
    "onwards."
)


def plugin_version():
    """The version from the plugin manifest, or `"unknown"`.

    A stated absence rather than a fabricated `0.0.0`: a report gets pasted into an
    issue and read weeks later, and the reader has to be able to tell "the version
    could not be determined" from "the version was 0.0.0".
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
    """Which project to report on.

    `$CLAUDE_PROJECT_DIR` first, then the process cwd — the same ordering
    `hook_io.payload_cwd` uses, minus the payload source that does not exist here.
    A skill running this has the variable set correctly; a person running it by
    hand from a subdirectory is better served by the project root than by where
    they happen to be standing, because the ledger is keyed per project and
    reporting on the wrong key looks exactly like reporting on an empty one.
    """
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _gap(kind, reason):
    return {"kind": kind, "reason": reason}


def _envelope(cwd):
    """The keys every report carries, whether the read succeeded or not."""
    return {
        "ok": None,
        "schema": SCHEMA,
        "plugin_version": plugin_version(),
        "project": cwd,
    }


def _refused(cwd, reason, gap):
    """A failed read: the envelope, a machine reason, and one gap. Nothing else.

    Deliberately no `current`, `changes`, `episodes_seen` or `measures` — see the
    module docstring. The absence is the signal.
    """
    report = _envelope(cwd)
    report["ok"] = False
    report["reason"] = reason
    report["gaps"] = [gap]
    return report


def _current(cwd, cfg, gaps):
    """The composition in force, or `None`, appending the gap that explains it."""
    digest = ledger.current_digest(cwd, cfg)
    if not digest:
        gaps.append(_gap(_KIND_NO_COMPOSITION, _NO_COMPOSITION))
        return None

    record = ledger.read_composition(cwd, digest, cfg)
    if not isinstance(record, dict):
        # The digest is real — it came out of the change log — so it is reported.
        # Everything that would have come out of the record file is `null` rather
        # than omitted or defaulted: a `profile` of `{}` here would read as a
        # harness with no components, which is a different and false claim.
        gaps.append(_gap(_KIND_UNRESOLVED, _UNRESOLVED))
        return {
            "digest": digest,
            "profile": None,
            "env_hash": None,
            "model_basis": None,
            "captured_at": None,
        }

    return {
        "digest": digest,
        "profile": record.get("profile"),
        # Both halves of the pin, never just the hash (ADR-007). A report showing
        # `env_hash` alone invites the reader to make a comparison the comparison
        # layer is obliged to refuse, because two pins that hash alike are
        # comparable only if they were resolved the same way.
        "env_hash": record.get("env_hash"),
        "model_basis": record.get("model_basis"),
        "captured_at": record.get("captured_at"),
    }


def _threshold(cfg):
    """`min_verdict_coverage` as a float, or `None` if it is unusable.

    A `bool` is excluded even though it is an `int`: `true` would compare as `1.0`
    and make every project fail the check, which is the one direction of a broken
    config that would look like a finding rather than like a fault.
    """
    minimum = cfg.get("min_verdict_coverage")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
        return None
    return float(minimum)


def _coverage(records, cfg, gaps):
    """`verdict_coverage` over the latest reading of each episode.

    The field name is translated on the way in. `verdict.coverage` reads `basis`
    because it is written to be driven from `verdict.classify` output, while an
    episode record stores the same value under `verdict_basis` — the ledger has other
    bases in it and an unqualified `basis` there would be ambiguous. Passing the
    records through untranslated would silently score every episode as unstated,
    which is the failure this whole surface exists to prevent: a plausible number,
    wrong, with nothing anywhere saying so.
    """
    reading = verdict_lib.coverage(
        [{"basis": record.get("verdict_basis")} for record in records]
    )

    share = reading["coverage"]
    minimum = _threshold(cfg)
    if share is not None and minimum is not None and share < minimum:
        # W2's mitigation, and the reason it is a gap and not a footnote: poor verdict
        # coverage is the single largest threat to every number this plugin will ever
        # print, so it is said on the report rather than in the README.
        gaps.append(
            _gap(
                _KIND_UNSTATED_VERDICT,
                _UNSTATED_VERDICT.format(
                    actual=int(round(share * 100)), minimum=int(round(minimum * 100))
                ),
            )
        )
    return reading


def _episodes(cwd, cfg, gaps):
    """`(episodes_seen, verdict_coverage)` — three-valued, see the module docstring.

    Counted over `latest_episodes` rather than over every line, because the ledger is
    append-only *on movement*: one issue holds several readings when a verdict is
    declared after the fact, and counting lines would report a project that revisits
    its outcomes as a project that did twice the work.
    """
    records = ledger.latest_episodes(cwd, cfg)
    if records:
        return len(records), _coverage(records, cfg, gaps)

    # `ledger.episodes` presents an absent ledger and an unreadable one alike as
    # `[]`, so the file itself is what separates them. Nothing is created by asking.
    if os.path.exists(ledger.episodes_path(cwd, cfg)):
        gaps.append(_gap(_KIND_EPISODES_UNREADABLE, _EPISODES_UNREADABLE))
        # Not `verdict.coverage([])`: that would answer `reason: "no_episodes"`, which
        # is a cause this read never established and the opposite of what happened.
        return None, None

    gaps.append(_gap(_KIND_NO_EPISODES, _NO_EPISODES))
    return 0, _coverage((), cfg, gaps)


def build_report(cwd, cfg):
    allowed, reason = consent.require_consent(cfg)
    if not allowed:
        if reason == "config_changed":
            return _refused(cwd, reason, _gap(_KIND_CONFIG_CHANGED, _CONFIG_CHANGED))
        return _refused(cwd, reason, _gap(_KIND_NOT_ACKNOWLEDGED, _NOT_ACKNOWLEDGED))

    gaps = []
    report = _envelope(cwd)
    report["ok"] = True
    report["current"] = _current(cwd, cfg, gaps)
    report["changes"] = ledger.changes(cwd, cfg)
    report["episodes_seen"], report["verdict_coverage"] = _episodes(cwd, cfg, gaps)
    # Still `null`, still with a gap, and now the only field in the report that is.
    # Not implemented is not the same as zero.
    report["measures"] = None
    gaps.append(_gap(_KIND_MEASURES, _MEASURES))
    report["gaps"] = gaps
    report["flags"] = list(cfg.get("flags") or ())
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        prog="fitness.py",
        description="Report harness composition and fitness for one project.",
    )
    # Required, so there is exactly one output contract to keep correct. A default
    # human format would be a second one, and every consumer at this version is a
    # skill that calls `json.loads` on stdout.
    parser.add_argument(
        "--json",
        action="store_true",
        required=True,
        help="emit the report as JSON on stdout (currently the only format)",
    )
    return parser


def main(argv=None):
    build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    cfg = hfit_config.load()
    report = build_report(project_root(), cfg)
    # `sort_keys=False` so `ok` stays first, which is the order the contract is
    # stated in and the order a person reads a pasted report in.
    json.dump(report, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
