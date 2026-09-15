#!/usr/bin/env python3
"""`PostToolUse:Bash` and `Stop` — turn closed issues into episodes, and say nothing.

One script for two events, because the work is identical and reconciliation is
*discovery* rather than tracking: it reads the whole closed population and the ledger
appends only where a record moved, so running it twice costs a `bd list` and missing
the moment of a close costs nothing at all. Two triggers exist to make a miss unlikely,
not because either is authoritative.

**What differs between the two is only whether there is a command to inspect.** A
`Stop` reconciles unconditionally. A tool event reconciles only when its command
actually closed an issue — `PostToolUse:Bash` sees every shell call a session makes, and
a `bd list` on each one is a cost the split between this and `reconcile.is_close_command`
exists to avoid.

That decision is taken from the *presence of a command*, not from `hook_event_name`, and
the asymmetry of the two mistakes is why. Reading an absent or renamed event name as
"reconcile" would put `bd list` behind every shell call — the one cost this hook is
built to avoid. Reading it as "do nothing" would make the plugin silently stop recording
episodes, with no artefact anywhere saying so. Keying on the field the branch actually
needs avoids both, and survives Claude Code renaming an event.

**The read and the write are two calls, deliberately.** `reconcile` is read-only, which
is what lets it hang off a `Stop` on every turn; it reports which episodes *moved* and
this script hands those to the index. An unchanged episode is absent from that list, so
the steady state runs no `bd update` at all.

Fail-open throughout: a measurement hook that can break a session has made the session
worse in order to describe it.
"""

import os
import sys

# Resolved from `__file__` rather than from `$CLAUDE_PLUGIN_ROOT`, for the reason given
# in `snapshot_composition.py`: a script that has already been located knows where its
# own siblings are.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import beads_write  # noqa: E402
import hfit_config  # noqa: E402
import hook_io  # noqa: E402
import reconcile as reconcile_lib  # noqa: E402


def main():
    payload = hook_io.read_payload()

    if _stop_loop(payload):
        return

    command = _command(payload)
    if command is not None and not reconcile_lib.is_close_command(command):
        # A shell call that changed nothing about the backlog. No subprocess.
        return

    cwd = hook_io.payload_cwd(payload)

    # Loaded once and threaded through both calls, which would otherwise each re-read
    # the config file and re-check the same consent record. A cost optimisation with no
    # behaviour attached: passing `cfg=None` here would make each callee `load()` for
    # itself and produce an identical config, so no test distinguishes the two. That is
    # a mutation survivor by construction rather than a missing assertion.
    cfg = hfit_config.load()

    result = reconcile_lib.reconcile(cwd, cfg=cfg)
    if not result["ok"]:
        # A refusal, not an empty population — no database, no `bd`, no consent. The
        # ledger holds nothing new, so there is nothing to index either.
        #
        # Defence in depth, and knowingly so: `reconcile._refusal` sets `moved` to
        # `None`, so `if moved:` below is already falsey on every refusal and no input
        # distinguishes removing this guard. It stays because it is the semantically
        # primary check — deleting it would make this hook's correctness depend on a
        # choice made in another module, which nothing at this layer would protect if
        # it changed. The coupling itself is asserted where it belongs, at
        # `tests/unit/test_reconcile.py::test_moved_is_unknown_on_a_failed_read_and_never_an_empty_list`.
        return

    moved = result["moved"]
    if moved:
        beads_write.write_episodes(cwd, moved, cfg=cfg)


def _command(payload):
    """The Bash command this event reports, or `None` if this is not a tool event.

    `None` and `""` are different answers and the caller branches on the difference:
    a tool event whose command is missing or unusable must *not* reconcile, or the
    `is_close_command` filter is bypassed by exactly the malformed payloads it should
    be strictest about. So an unusable `tool_input` yields `""`, which the predicate
    rejects, and only a genuinely absent one yields `None`.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def _stop_loop(payload):
    """Is this a `Stop` re-entry?

    This hook cannot cause the loop `stop_hook_active` exists to break, because it
    emits nothing and can never block a stop. Honouring the flag is therefore not
    about correctness but about cost: a re-entrant `Stop` would pay for a second
    `bd list` that is guaranteed to report every episode `unchanged`.
    """
    return bool(payload.get("stop_hook_active"))


if __name__ == "__main__":
    hook_io.fail_open(main)
