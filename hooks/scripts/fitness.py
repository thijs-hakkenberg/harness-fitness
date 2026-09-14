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

**Unknown is never zero.** Where a number cannot be computed, the field is `null`
and a `gaps[]` entry says why in a sentence a person can act on. `episodes_seen: 0`
would be a claim about the user's work — that we counted their closed issues and
found none — when the truth is that no episode store is written until 0.2.0.

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
_KIND_MEASURES = "measures-unavailable"

GAP_KINDS = (
    _KIND_NOT_ACKNOWLEDGED,
    _KIND_CONFIG_CHANGED,
    _KIND_NO_COMPOSITION,
    _KIND_UNRESOLVED,
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
_MEASURES = (
    "The four measures are not implemented at this version. This release records "
    "harness composition only; episodes, verdicts and measures arrive in 0.2.0 "
    "and 0.3.0."
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
    # Both `null`, both with a gap. Not implemented is not the same as zero, and at
    # this version it is the only honest thing either field can say.
    report["episodes_seen"] = None
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
