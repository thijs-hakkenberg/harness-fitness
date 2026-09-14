"""Consent gates every write.

This plugin reads settings files, enumerates installed plugins and inspects hook
configuration. None of it is recorded anywhere until the user has run
``acknowledge.py --accept``.

The acknowledgement is fingerprinted over the *governing* configuration, so
enabling OTEL reading or turning on the in-repo ledger re-asks, while tuning a
threshold does not. Nothing in this module creates the state directory as a side
effect of being asked whether writing is allowed — that is what makes the promise
in the README testable.
"""

import hashlib
import json
import os

import hfit_config
import hfit_time
import state_store

ACK_FILENAME = "acknowledged.json"
SCHEMA = 1


def ack_path():
    """Absolute path of the acknowledgement file. Creates nothing."""
    return os.path.join(state_store.state_root(), ACK_FILENAME)


def fingerprint(cfg):
    """A 12-hex digest of the governing configuration.

    Canonicalised with sorted keys and no whitespace, so the fingerprint depends
    on the governing *values* and not on how the config file happened to be
    written or in what order it was merged.
    """
    canonical = json.dumps(
        hfit_config.governing(cfg), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _read_ack():
    """The acknowledgement record, or ``None``. A corrupt file reads as absent."""
    record = state_store.read_json(ack_path())
    if not isinstance(record, dict):
        return None
    return record


def is_acknowledged(cfg):
    """True only if a stored acknowledgement matches the current governing config."""
    record = _read_ack()
    if record is None:
        return False
    return record.get("fingerprint") == fingerprint(cfg)


def require_consent(cfg):
    """``(allowed, reason)``. Callers must not write when `allowed` is False.

    The two refusals are distinguished because they need different remedies: a
    first-time install has never been asked, whereas a changed governing config
    means the user consented to something else.
    """
    record = _read_ack()
    if record is None or not record.get("fingerprint"):
        return (False, "not_acknowledged")
    if record.get("fingerprint") != fingerprint(cfg):
        return (False, "config_changed")
    return (True, None)


def record_acknowledgement(cfg):
    """Store consent for the current governing config. Idempotent.

    Re-accepting an already-matching fingerprint leaves `accepted_at` alone, so
    the record keeps saying when the user actually agreed.
    """
    current = fingerprint(cfg)
    record = _read_ack()
    if record is not None and record.get("fingerprint") == current:
        return True
    return state_store.write_json_atomic(
        ack_path(),
        {
            "schema": SCHEMA,
            "accepted_at": hfit_time.iso(),
            "fingerprint": current,
        },
    )


def revoke():
    """Remove the acknowledgement. False if there was nothing to remove."""
    try:
        os.unlink(ack_path())
        return True
    except OSError:
        return False


def status(cfg):
    """What `acknowledge.py --show` reports.

    `fingerprint` is the *current* governing fingerprint, not the stored one, so
    a stale acknowledgement shows up as accepted-but-not-matching rather than as
    a match against itself.
    """
    record = _read_ack() or {}
    return {
        "accepted": is_acknowledged(cfg),
        "accepted_at": record.get("accepted_at"),
        "fingerprint": fingerprint(cfg),
    }
