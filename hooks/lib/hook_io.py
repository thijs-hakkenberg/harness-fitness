"""The hook stdin/stdout protocol, and the fail-open discipline around it.

A hook that crashes is worse than a hook that does nothing: Claude Code surfaces
hook stderr, so a measurement tool the user did not ask to hear from starts
printing tracebacks into their session. Every hook body therefore runs inside
``fail_open``, which turns any exception into exit 0 with empty stdout *and*
empty stderr.

Output is buffered rather than printed directly. A half-emitted JSON response is
a protocol violation, so the buffer is only released once the body has completed;
if the body fails after emitting, the partial response is discarded with it.

``HFIT_DEBUG`` turns the swallowing off, because a discipline that also makes the
plugin undebuggable does not survive contact with its first real bug.
"""

import json
import os
import sys

_buffer = []

_DEBUG_OFF = ("", "0", "false", "no")


def _debug():
    return os.environ.get("HFIT_DEBUG", "").strip().lower() not in _DEBUG_OFF


def read_payload():
    """The hook payload as a dict. Anything unusable reads as ``{}``.

    A JSON scalar or array becomes ``{}`` too: callers index the result, and a
    list would turn a malformed payload into an ``AttributeError`` deep inside a
    hook body rather than a no-op here.
    """
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def emit(response):
    """Queue a JSON response. Nothing reaches stdout until the body completes."""
    _buffer.append(response)


def _flush():
    for response in _buffer:
        try:
            sys.stdout.write(json.dumps(response))
        except (TypeError, ValueError):
            continue
    del _buffer[:]


def _discard():
    del _buffer[:]


def succeed():
    """Release any queued response and exit 0."""
    _flush()
    raise SystemExit(0)


def fail_open(body):
    """Run `body`, then exit 0. Any exception is swallowed silently.

    Only ``Exception`` is caught. ``SystemExit`` carries a deliberate exit code
    and ``KeyboardInterrupt`` is the user asking to stop; swallowing either would
    make this wrapper the thing that broke the session.
    """
    try:
        body()
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        _discard()
        if _debug():
            raise
        raise SystemExit(0)
    succeed()


def payload_cwd(payload):
    """The project directory: payload, then ``$CLAUDE_PROJECT_DIR``, then cwd."""
    if isinstance(payload, dict):
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    from_env = os.environ.get("CLAUDE_PROJECT_DIR")
    if from_env:
        return from_env
    return os.getcwd()


def payload_session_id(payload):
    """The session id, or ``None``.

    Never a synthesised placeholder: two sessions sharing a fabricated id would
    silently merge their events into one window.
    """
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None
