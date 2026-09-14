"""Whether two measurements are comparable at all.

`composition_digest` says *what* is being compared; `env_hash` says whether the
comparison is legitimate (ADR-007). A model swap that halves tokens per outcome
must not read as a harness win, and this hash is the only thing between that
mistake and a report. So the two identities are computed separately, carried
together, and a differing `env_hash` is *refused* rather than reported.

Three constraints, none of them a style choice:

- **The field set is the rig's five**, transcribed from `rig/rig/env_pin.py`.
  Matching it is the entire point: it keeps the continuous detector and the
  controlled confirmer talking about the same environment, so a signal found here
  can be handed to the rig there and mean the same thing.
- **The hash is a full 64 hex.** `rig/schemas/result.schema.json` types `env_hash`
  as `^[0-9a-f]{64}$`, so a truncated one fails `--rig-handoff`. It is a different
  width from `composition_digest`'s twelve on purpose — that asymmetry is a
  handoff constraint, not an inconsistency to harmonise away.
- **An absent field is `None`, never a default.** The rig's `child_env` defaults
  `max_retries` to `2` because it is *setting* the value; we are observing one,
  and substituting a default would let two machines with genuinely different
  retry behaviour hash alike.

**The model is not declarable, and that was measured rather than assumed.** On
the machine this was written on, `settings.json` said `model: "opus"`,
`env.ANTHROPIC_MODEL` said `claude-fable-5-innovation`, and every one of 389 OTEL
`api_request` events reported `claude-opus-5`. No precedence rule over the two
declared fields produces the observed answer — the settings alias won, the
environment variable was inert, and the resolved id appears in neither file. So a
disk-resolved model is labelled `declared` and never presented as the model that
ran; an observation supersedes it; and `model_basis` travels with every record.

This is the one module that opens the `env` block, because the base URL, the
cache flag and the retry count live nowhere else. It takes those three keys by
name and nothing else — `~/.claude/settings.json` is where a live credential
sits, and a record that carried it would be a leak in a file users are invited to
read and attach to bug reports. `composition` reads no `env` at all.
"""

import hashlib
import json
import platform as platform_mod
import sys
from urllib.parse import urlsplit, urlunsplit

import settings_read

# Bumped when the record shape changes. Not hashed — see `_HASH_BASIS`.
SCHEMA = 1

# Transcribed from `rig/rig/env_pin.py::_PINNED_FIELDS`, in its order. The five
# fields that decide whether two runs are comparable.
PINNED_FIELDS = (
    "model",
    "effort",
    "anthropic_base_url",
    "cache_policy",
    "max_retries",
)

# Substrate versions. Hashed beside the pinned fields, as the rig hashes its
# probed `versions`: a Claude Code upgrade changes what a fixed config does.
_VERSION_FIELDS = ("claude_code_version", "agent_sdk_version")

# Reported for the handoff, deliberately *not* hashed. The interpreter running a
# measurement hook is not part of the environment being measured, and a brew
# upgrade must not split an episode population. `platform` follows the rig, which
# also keeps it out of its hash.
_LOCAL_FIELDS = ("platform", "python_version")

# Forced by `rig/schemas/result.schema.json`, which types `env_hash` as exactly
# 64 hex. Not truncated to match `composition.DIGEST_LEN`.
HASH_LEN = 64

# Hashed as a constant. Unlike `composition.SCHEMA` — which tracks record shape
# and is excluded precisely so a bump cannot re-digest an unchanged harness —
# this marker changes only if the *hashed field set* changes, at which point the
# hash means something different and should move. It also keeps a hash produced
# here from colliding with one the rig computed over its own payload.
_HASH_BASIS = "hfit-env-1"

# Values `ENABLE_PROMPT_CACHING_1H` may take to mean yes. Anything else — most
# importantly `"0"` and `"false"` — is the default policy.
_TRUTHY = ("1", "true", "yes", "on")

# When the model is resolved from disk rather than observed. Aliases are how
# Claude Code is normally configured (`opus` → `claude-opus-5`), so a declaration
# is routinely *compatible with* rather than equal to what ran.
_ALIAS_HINT = "declared"


def hash_payload(env):
    """Exactly the fields the hash is computed over, flat.

    Flat rather than nested so `"python_version" not in hash_payload(env)` is a
    real assertion about the hash rather than a statement about one nesting level.
    Accepts either a resolved pinned dict or a full env block; every field is
    read with `.get`, so a partial input hashes as a partial environment instead
    of raising.
    """
    payload = {field: env.get(field) for field in PINNED_FIELDS}
    payload.update({field: env.get(field) for field in _VERSION_FIELDS})
    payload["basis"] = _HASH_BASIS
    return payload


def env_hash(env):
    """A full 64-hex sha256 over the hashed field set.

    `sort_keys=True` is what makes the hash independent of the order the caller
    happened to build the dict in; `separators` removes the whitespace a
    pretty-printer would otherwise fold in. `default=str` keeps an unexpected
    value from raising on the `SessionStart` path.
    """
    blob = json.dumps(
        hash_payload(env), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def resolve(home, project_root, observed=None):
    """`(env_block, model_declared, model_basis, unreadable)` from disk.

    `home` and `project_root` are passed in rather than derived, for the same
    reason `composition.inventory` takes them: two sources of truth for the same
    two paths would let `$CLAUDE_PROJECT_DIR` steer a read whose caller already
    knew which project it meant.
    """
    layers, unreadable = settings_read.read_layers(home, project_root)

    # The one place the `env` block is opened, and only by key name.
    env = settings_read.merged_mapping(layers, "env")

    declared = {
        "settings": _text(settings_read.merged_scalar(layers, "model")),
        "env": _text(env.get("ANTHROPIC_MODEL")),
    }
    model, basis = _model(declared, observed)

    block = {
        "model": model,
        "effort": _text(settings_read.merged_scalar(layers, "effortLevel")),
        "anthropic_base_url": _endpoint(env.get("ANTHROPIC_BASE_URL")),
        "cache_policy": _cache_policy(env.get("ENABLE_PROMPT_CACHING_1H")),
        "max_retries": _int(env.get("CLAUDE_CODE_MAX_RETRIES")),
    }
    block.update(_versions(observed))
    block.update(_local())

    return block, declared, basis, unreadable


def _model(declared, observed):
    """The model, and the basis on which we claim it.

    Observation wins outright. Between the two declarations, the top-level
    `model` setting wins over `env.ANTHROPIC_MODEL` — the opposite of the
    env-vars-override convention, and chosen because the convention was tested
    and found wrong on a live machine (see the module docstring).
    """
    seen = _text(isinstance(observed, dict) and observed.get("model"))
    if seen:
        return seen, "observed"

    for source in ("settings", "env"):
        if declared[source]:
            return declared[source], _ALIAS_HINT

    # Not a default and not an empty string. Nothing on disk names a model, and
    # a fabricated one would let two unlike environments hash alike.
    return None, "unknown"


def _versions(observed):
    """Substrate versions, from observation only.

    `"unknown"` rather than `None` because the rig's result schema types these as
    non-nullable strings, so a null would fail `--rig-handoff` validation. The
    sentinel is a stated absence, not an invented version — and it hashes
    distinctly from any real one.
    """
    observed = observed if isinstance(observed, dict) else {}
    return {
        "claude_code_version": _text(observed.get("app_version")) or "unknown",
        "agent_sdk_version": _text(observed.get("sdk_version")) or "unknown",
    }


def _local():
    """Where the measurement ran. Reported for the handoff, never hashed."""
    return {
        "platform": sys.platform,
        "python_version": platform_mod.python_version(),
    }


def _cache_policy(value):
    """`"1h"` or `"default"` — never null.

    Absence here is a determinate state: no 1h flag means the default cache
    policy is in force. Reporting `null` would claim we could not tell, which is
    the one thing that is not true. `max_retries` reads the opposite way — see
    `_int`.
    """
    return "1h" if _text(value).lower() in _TRUTHY else "default"


def _int(value):
    """An integer, or `None` when undeclared or unparseable.

    `None` here means "whatever Claude Code's built-in default is", which is not
    knowable from disk and is not representable any other way: the rig's schema
    types this field `["integer", "null"]`, so the honest answer has to be null
    rather than a named default.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _endpoint(value):
    """The endpoint, with any credential removed.

    A base URL is one of the five pinned fields and is stored verbatim by the
    rig's env block — but a URL can carry userinfo or an `?api_key=`, and this
    record is a file a user may attach to a bug report.

    Sanitised *before* hashing, not after, so rotating an embedded token does not
    move `env_hash` and refuse comparison against yesterday's episodes. The
    endpoint is what decides comparability; the credential is not.
    """
    raw = _text(value)
    if not raw:
        return None

    try:
        parts = urlsplit(raw)
    except ValueError:
        return None

    netloc = _netloc(parts)
    if not parts.scheme and not netloc:
        # No scheme and no authority means no userinfo either, so only the query
        # could carry a secret. Keep the value, drop the query and fragment.
        return urlunsplit(("", "", parts.path, "", "")) or None

    return urlunsplit((parts.scheme, netloc, parts.path, "", "")) or None


def _netloc(parts):
    """Host and port only — userinfo dropped rather than trusted to be absent."""
    try:
        host = parts.hostname
        port = parts.port
    except ValueError:
        return ""
    if not host:
        return ""
    return "%s:%d" % (host, port) if port else host


def flags(declared, basis, model, unreadable):
    """What a report has to disclose about how well this pin is known."""
    found = []

    for scope, reason in unreadable or ():
        # A layer that would not parse may be the layer that set the base URL, so
        # the hash can be computed over less than the real environment.
        found.append({"kind": "settings-unreadable", "detail": scope, "reason": reason})

    settings_model = declared.get("settings")
    env_model = declared.get("env")

    if settings_model and env_model and not _compatible(settings_model, env_model):
        # Both files name a model and they disagree. The resolution is stated
        # (settings wins) but the disagreement itself is worth surfacing: one of
        # the two is a stale line someone believes is in force.
        found.append(
            {
                "kind": "model-declaration-ambiguous",
                "detail": settings_model,
                "other": env_model,
            }
        )

    if basis == "observed":
        for source in ("settings", "env"):
            name = declared.get(source)
            if name and not _compatible(name, model):
                found.append(
                    {
                        "kind": "model-declaration-stale",
                        "detail": name,
                        "scope": source,
                        "observed": model,
                    }
                )

    if basis == "unknown":
        found.append({"kind": "model-unknown", "detail": None})

    return found


def _compatible(declared_name, other):
    """Whether a declared name plausibly *is* `other`, allowing for aliases.

    `opus` and `claude-opus-5` are the same model configured two ways, and
    flagging that pairing would fire on the normal case and train a reader to
    ignore the flag. `claude-fable-5-innovation` against `claude-opus-5` is a
    genuine contradiction.

    A substring test is a heuristic, and it is confined to deciding whether to
    raise a *flag* — never to choosing a value. A wrong guess here costs one
    line of disclosure, not a wrong `env_hash`.
    """
    left = _text(declared_name).lower()
    right = _text(other).lower()
    if not left or not right:
        return True
    return left in right or right in left


def build(home, project_root, observed=None):
    """The full environment pin: the hash, the block, the basis and the flags.

    `model_basis` is *not* in the hash. Folding it in would mean every user who
    gains an OTEL log re-hashes an unchanged environment, splitting one
    comparable population into two that each fall below
    `min_episodes_per_digest` — the same spurious-split failure
    `composition.canonical` is built to avoid. The protection lives instead where
    it already exists: the comparison layer refuses on a differing `env_hash`,
    and ADR-007 records that it must refuse on a differing `model_basis` too.
    """
    block, declared, basis, unreadable = resolve(home, project_root, observed)

    return {
        "schema": SCHEMA,
        "env_hash": env_hash(block),
        "env": block,
        # Both declarations are kept even when one is superseded, so a reader can
        # see which file misled them rather than only that something did.
        "model_declared": dict(declared),
        "model_basis": basis,
        "flags": flags(declared, basis, block["model"], unreadable),
    }


def _text(value):
    """A stripped string, or `""` for anything that is not usable text."""
    return value.strip() if isinstance(value, str) else ""
