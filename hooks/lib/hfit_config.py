"""Configuration: defaults, the merge, and the governing subset.

Two things here are decisions rather than preferences.

``min_episodes_per_digest`` equals ``mad_min_n`` on purpose. If they diverge, a
digest group can clear the comparison gate while being too small for the outlier
rule to run, which produces a delta nobody guarded.

``governing()`` is the subset of configuration that determines *what is collected
and where it is written*. Consent is fingerprinted over exactly that, so changing
what gets written re-asks, and tuning a threshold does not — a consent prompt the
user learns to click through protects nothing.
"""

import copy
import json
import os

CONFIG_FILENAME = "config.json"

DEFAULTS = {
    # Measure gates.
    "min_verdict_coverage": 0.6,
    "min_episodes_per_digest": 5,
    "mad_min_n": 5,
    "mad_threshold": 3.0,
    # ADR-005: a PreToolUse deny prevents the action rather than detecting it,
    # so it counts as a feedforward catch. The list lives here so that
    # judgement stays inspectable and reversible.
    "ff_hook_events": [
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
    ],
    # Storage. `in_repo` is false because writing a directory into a repository
    # the user may not own is something to be asked for, not fallen into.
    "ledger": {"in_repo": False},
    "beads": {"write_metadata": True},
    "otel": {"enabled": True, "tail_bytes": 4 * 1024 * 1024},
    "events_retention_days": 30,
    # An LLM call must never happen merely because someone installed a plugin.
    "inference": {"enabled": False},
}

# Dotted paths into the config whose values decide what is collected and where.
_GOVERNING_KEYS = (
    "ledger.in_repo",
    "beads.write_metadata",
    "otel.enabled",
    "inference.enabled",
    "events_retention_days",
)


def config_path():
    """Absolute path of the config file. Resolving it creates nothing."""
    import state_store

    return os.path.join(state_store.state_root(), CONFIG_FILENAME)


def _deep_merge(base, overlay):
    """Recursively overlay `overlay` onto `base`, in place.

    Recursive rather than ``dict.update`` because a user who sets only
    ``otel.enabled`` must not silently lose ``otel.tail_bytes``.
    """
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load():
    """Defaults deep-merged with the on-disk config. Never raises.

    Unknown keys are preserved, so a file written by a newer version survives a
    downgrade instead of being silently trimmed.
    """
    cfg = copy.deepcopy(DEFAULTS)
    flags = []

    path = config_path()
    if os.path.exists(path):
        raw = None
        try:
            with open(path) as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            flags.append("config-unreadable")
        if raw is not None:
            if isinstance(raw, dict):
                _deep_merge(cfg, raw)
            else:
                flags.append("config-unreadable")

    cfg["flags"] = flags
    return cfg


def _dotted(cfg, path):
    """Read `path` from `cfg`, falling back to the same path in DEFAULTS."""
    for source in (cfg, DEFAULTS):
        node = source
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            return node
    return None


def governing(cfg):
    """The flat, dotted subset of `cfg` that consent is fingerprinted over.

    Flat and scalar-only so it canonicalises stably: a nested dict would make
    the fingerprint depend on the shape of the config file rather than on the
    values that matter.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    return {key: _dotted(cfg, key) for key in _GOVERNING_KEYS}
