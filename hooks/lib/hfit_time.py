"""Time handling for harness-fitness.

Every timestamp this plugin writes is UTC and ``Z``-suffixed, and every parse is
total: an unparseable input returns ``None`` rather than raising, because most
timestamps arrive from somewhere else (beads, an OTLP record, a previous
version's ledger) and none of them may be able to kill a hook.

``$HFIT_NOW`` freezes the clock for tests, mirroring ``$ABACUS_NOW``.
"""

import datetime as dt
import os

UTC = dt.timezone.utc

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now():
    """Current instant as a tz-aware UTC datetime, honouring ``$HFIT_NOW``."""
    override = os.environ.get("HFIT_NOW")
    if override:
        parsed = parse_iso(override)
        if parsed is not None:
            return parsed
        # A malformed override falls through to the real clock rather than
        # raising: a stray export must not disable every hook.
    return dt.datetime.now(UTC)


def iso(when=None):
    """Canonical wire form: UTC, second precision, ``Z``-suffixed."""
    if when is None:
        when = now()
    if not isinstance(when, dt.datetime):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).strftime(_ISO_FMT)


def parse_iso(raw):
    """Parse an ISO-8601 instant, or return ``None``.

    Accepts the ``Z`` suffix (which ``fromisoformat`` rejects before 3.11) and
    naive strings, which are assumed UTC because that is what beads writes.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def unix_nano_to_dt(raw):
    """Convert an OTLP ``timeUnixNano`` to a datetime, or ``None``.

    The field is a JSON *string* on the wire per the proto3 JSON mapping, so the
    int coercion here is required rather than defensive.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        nanos = int(raw)
    except (TypeError, ValueError):
        return None
    try:
        return dt.datetime.fromtimestamp(nanos / 1_000_000_000.0, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def days_between(start, end):
    """Signed fractional days from `start` to `end`, or ``None``."""
    if not isinstance(start, dt.datetime) or not isinstance(end, dt.datetime):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - start).total_seconds() / 86400.0
