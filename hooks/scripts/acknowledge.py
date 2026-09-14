#!/usr/bin/env python3
"""`acknowledge.py` — the only thing in this plugin that can open the write gate.

Every write is gated on an acknowledgement (ADR-014) and nothing else can produce
one, which makes this the shortest single point of total failure here: without it
the gate stays shut forever, every hook records nothing, and the only symptom is a
report saying `not-acknowledged` while naming a command that does not exist.

Three properties are load-bearing.

**Consent is never implied.** There is no default action: an invocation with no flag
prints usage and exits non-zero. A script whose default is "accept" has collected a
keystroke rather than a decision, and the gate reduces to whether the user happened
to type a word. For the same reason `--accept` is spelled out rather than offered as
a `-y`.

**`--show` writes nothing at all.** It is the command a cautious user runs *first*,
to find out what this thing would do, so it is the worst possible place to break the
promise that nothing is recorded before consent — and the easiest, because resolving
a path is one `makedirs` away from creating it. `state_store.state_root`,
`consent.ack_path` and `hfit_config.load` are all documented as resolve-only reads,
and a test asserts the state root does not *exist* afterwards rather than that it is
empty, because an empty directory is a trace too.

**The notice names the directory.** Consenting to "measurement" in the abstract is
not consenting, so `--show` prints where the files go and what is not in them. A user
who has to read the source to learn what they agreed to was never asked.

Unlike a hook, this is noisy: a mistyped flag exits non-zero and complains on stderr.
Silently ignoring `--acccept` and printing a friendly line would leave the user
believing they had consented when the gate was still shut.
"""

import argparse
import json
import os
import sys

_HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_HOOKS, "lib"))

import consent  # noqa: E402
import hfit_config  # noqa: E402
import state_store  # noqa: E402

# Wrapped at write time rather than at print time: a `textwrap` fill would reflow the
# path in the middle, and the path is the one line a reader may need to copy.
_NOTICE = """\
harness-fitness measures what your Claude Code harness is composed of, and how well
it performs, for each project you work in.

Nothing has been recorded yet, and nothing will be until you accept below.

What gets written, and where:

  %(root)s

  Everything lives in that directory as plain files you can read, diff and delete.
  Per project: the inventory of your enabled plugins, hooks, skills, agents and MCP
  servers, a digest identifying that composition, and a log of every change to it.

What is never written:

  - No hook or MCP command strings. They are reduced to a basename and a one-way
    digest, because a command line can contain an absolute path or a token.
  - No credentials. The `env` block of your settings files is never opened at all.
  - No prompt text. Only a length and a hash prefix.
  - No absolute paths from your machine.

Where it goes:

  Nowhere. There is no upload, no endpoint and no telemetry of any kind. Every
  artefact is a local file.

Your acknowledgement is fingerprinted over the settings that govern what gets
recorded (%(governing)s).
Change one of those and recording stops until you accept again; tuning a threshold
does not re-ask.
"""


def notice():
    return _NOTICE % {
        "root": state_store.state_root(),
        "governing": ", ".join(sorted(hfit_config.governing(hfit_config.load()))),
    }


def _show(cfg, as_json):
    status = consent.status(cfg)
    if as_json:
        return status, 0

    sys.stdout.write(notice())
    sys.stdout.write("\n")
    if status["accepted"]:
        sys.stdout.write(
            "Accepted on %s (fingerprint %s). Recording is active.\n"
            % (status["accepted_at"], status["fingerprint"])
        )
    elif status["accepted_at"]:
        # Distinguished from never-accepted on purpose. "You agreed to something
        # else" and "you have never been asked" have different remedies, and a user
        # told the second when the first is true goes looking for a bug.
        sys.stdout.write(
            "Not active: you accepted on %s, but a governing setting has changed "
            "since.\nRun `acknowledge.py --accept` to accept the current "
            "configuration.\n" % status["accepted_at"]
        )
    else:
        sys.stdout.write(
            "Not accepted. Nothing has been recorded.\n"
            "Run `acknowledge.py --accept` to consent, then start a session in a "
            "project.\n"
        )
    return status, 0


def _accept(cfg, as_json):
    ok = consent.record_acknowledgement(cfg)
    status = consent.status(cfg)
    if not ok or not status["accepted"]:
        # Loud, and non-zero. A consent script that fails to persist while printing
        # a confirmation is worse than one that crashes: the user believes the gate
        # is open and every later "nothing recorded" reads as a different bug.
        sys.stderr.write(
            "acknowledge.py: could not write %s — nothing was recorded.\n"
            % consent.ack_path()
        )
        return status, 1

    if not as_json:
        sys.stdout.write(
            "Accepted on %s (fingerprint %s).\n"
            "Recording starts at the next session in a project. Run "
            "`/hfit:composition` to see what was recorded.\n"
            "Withdraw at any time with `acknowledge.py --revoke`.\n"
            % (status["accepted_at"], status["fingerprint"])
        )
    return status, 0


def _revoke(cfg, as_json):
    removed = consent.revoke()
    if not as_json:
        if removed:
            sys.stdout.write(
                "Acknowledgement removed. Nothing further will be recorded.\n"
                "Files already written are left in place; delete %s to remove them.\n"
                % state_store.state_root()
            )
        else:
            # Not an error. A non-zero exit here reads as "the revoke did not work",
            # which is the one message in this script that must never be wrong.
            sys.stdout.write("Nothing to revoke: consent was not on record.\n")
    return consent.status(cfg), 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="acknowledge.py",
        description="Give, inspect or withdraw consent for harness-fitness to record.",
    )
    # Required and mutually exclusive: there is no default action, so a bare
    # invocation prints usage and exits non-zero rather than agreeing to anything.
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--accept", action="store_true", help="consent to recording, and start it"
    )
    action.add_argument(
        "--show", action="store_true", help="print the notice and the current status"
    )
    action.add_argument(
        "--revoke", action="store_true", help="withdraw consent; stops all recording"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the status object on stdout instead of prose",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    cfg = hfit_config.load()

    if args.accept:
        status, code = _accept(cfg, args.json)
    elif args.revoke:
        status, code = _revoke(cfg, args.json)
    else:
        status, code = _show(cfg, args.json)

    if args.json:
        json.dump(status, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
