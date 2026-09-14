#!/usr/bin/env python3
"""`SessionStart` — record what the harness is composed of, and say nothing.

This is the only writer of `compositions/` and `changes.jsonl`. It runs at the
start of every session, reads eight disk sources, and appends to the ledger only
when the harness's identity has actually moved.

**It emits no `additionalContext`.** A `SessionStart` response may inject text into
the context window, and this one deliberately does not: a tool whose purpose is to
measure what the harness costs must not become a line item in that cost (ADR-006).
Everything it learns goes to a file the user can read on demand.

**Nothing is written until consent is on record**, and the check runs before any
path is resolved as a directory, so an unacknowledged install leaves no trace at
all — assertable by looking at the filesystem rather than at a return value
(ADR-014).

**The composition file's `env_hash` is first-sighting provenance only.** The pin is
resolved here and stored, but composition files are content-addressed and never
rewritten; a later session under a different model produces the same digest, so
nothing is appended and the stored `env_hash` keeps its original value. That is
correct — the *harness* did not move — but it means an episode must resolve its own
`env_hash` at close time and must never read one off a composition record
(ADR-007).

Fail-open throughout: a measurement hook that can break a session has made the
session worse in order to describe it.
"""

import os
import sys

# Resolved from `__file__` rather than from `$CLAUDE_PLUGIN_ROOT`. The env var is
# what the manifest uses to *find* this script, but a script that has already been
# located knows where its own siblings are; trusting the variable a second time
# would let a stale or mis-set value import another copy of the library.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import composition  # noqa: E402
import env_pin  # noqa: E402
import hfit_config  # noqa: E402
import hook_io  # noqa: E402
import ledger  # noqa: E402


def main():
    payload = hook_io.read_payload()
    cwd = hook_io.payload_cwd(payload)
    home = os.path.expanduser("~")

    # Loaded once and threaded through. Both `composition.build` and
    # `ledger.record_composition` would otherwise each re-read the config file,
    # and a hook that measures latency should not spend two reads where one does.
    cfg = hfit_config.load()

    # Both halves of the pin, never just the hash. ADR-007 obliges the comparison
    # layer to refuse on a differing `model_basis` exactly as it refuses on a
    # differing `env_hash`, and that refusal has nothing to read if the basis is
    # dropped here. Nothing on this path consults an OTEL log, so the basis is
    # `declared` at best and `unknown` where no model resolves — which is precisely
    # why it must be recorded rather than assumed.
    pin = env_pin.build(home, cwd)
    record = composition.build(
        home,
        cwd,
        cfg=cfg,
        env_hash=pin.get("env_hash"),
        model_basis=pin.get("model_basis"),
    )
    ledger.record_composition(cwd, record, cfg=cfg)


if __name__ == "__main__":
    hook_io.fail_open(main)
