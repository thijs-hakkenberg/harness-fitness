"""Where harness-fitness state lives, and how it is written.

Writes are atomic (``mkstemp`` + ``os.replace``) and mode-restricted (0700 dirs,
0600 files), because a hook can be killed at any point and a half-written ledger
must not be readable as a complete one.

Reads are lossy-tolerant. A corrupt JSONL line is skipped rather than raised: the
alternative is that a single truncated line — the ordinary outcome of a crash
mid-append — permanently disables every measure that reads that file.

Nothing here creates a directory as a side effect of *resolving* a path. Path
resolution happens before the consent check, so a module that created its root
eagerly would write before the user had accepted anything.
"""

import datetime as dt
import json
import os
import re
import tempfile

import hfit_time

DIR_MODE = 0o700
FILE_MODE = 0o600

_DEFAULT_SUBDIR = os.path.join(".claude", "harness-fitness")
_RUN_OF_HYPHENS = re.compile(r"-+")


def state_root():
    """Root of all harness-fitness state. ``$HFIT_STATE_DIR`` overrides."""
    override = os.environ.get("HFIT_STATE_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), _DEFAULT_SUBDIR)


def project_slug(cwd):
    """Claude Code's directory-to-slug transform: every non-alphanumeric → ``-``.

    Measured across 99 real project directories: ``/`` and ``.`` both become
    ``-``. Whether *runs* of separators collapse is not determinable from any
    real path (none contains two adjacent non-alphanumerics), so this implements
    the simpler rule and `transcript_dir_for` disambiguates against the disk.
    """
    text = (cwd or "").rstrip("/")
    return "".join(c if (c.isalnum() and c.isascii()) else "-" for c in text)


def _slug_candidates(cwd):
    primary = project_slug(cwd)
    collapsed = _RUN_OF_HYPHENS.sub("-", primary)
    return [primary] if collapsed == primary else [primary, collapsed]


def transcript_dir_for(cwd):
    """The session-transcript directory for `cwd`, or ``None`` if absent.

    Returns ``None`` rather than a constructed path so a caller can flag
    ``transcript-unavailable`` instead of reading an empty directory and
    concluding there were no human interventions.
    """
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    for slug in _slug_candidates(cwd):
        candidate = os.path.join(base, slug)
        if os.path.isdir(candidate):
            return candidate
    return None


def project_dir(cwd):
    """This project's state directory. Does not create it."""
    return os.path.join(state_root(), "projects", project_slug(cwd))


def _ensure_dir(path):
    """Create `path` and any missing parents at 0700, chmod'ing only new dirs."""
    if not path or os.path.isdir(path):
        return
    missing = []
    current = path
    while current and not os.path.isdir(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory)
        except FileExistsError:
            continue
        os.chmod(directory, DIR_MODE)


def _write_text_atomic(path, text):
    """Replace `path` with `text` in one step. True on success, never raises.

    Write-to-temp-then-rename, so a reader either sees the previous complete file
    or the new complete file. A truncate-and-write would give it a third option:
    a valid-looking partial file.
    """
    directory = os.path.dirname(path) or "."
    tmp = None
    try:
        _ensure_dir(directory)
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=".hfit-", suffix=".tmp")
        with os.fdopen(handle, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def write_json_atomic(path, obj):
    """Write `obj` as JSON to `path` atomically. True on success, never raises."""
    try:
        payload = json.dumps(obj, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return _write_text_atomic(path, payload)


def read_json(path, default=None):
    """Parse the JSON at `path`, or return `default`. Never raises."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def append_jsonl(path, record):
    """Append one JSON object as one line. True on success, never raises."""
    try:
        line = json.dumps(record)
    except (TypeError, ValueError):
        return False
    try:
        _ensure_dir(os.path.dirname(path) or ".")
        with open(path, "a") as fh:
            fh.write(line + "\n")
        os.chmod(path, FILE_MODE)
        return True
    except OSError:
        return False


def read_jsonl(path):
    """Every well-formed JSON *object* line in `path`, in order.

    Corrupt lines, blank lines and non-object lines are skipped. A truncated
    final line — the ordinary result of a crash mid-append — costs exactly one
    record.
    """
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    out.append(parsed)
    except OSError:
        return out
    return out


def prune_jsonl(path, max_age_days, ts_key="ts"):
    """Drop records older than `max_age_days`. Returns the records kept.

    A record with no parseable timestamp is *kept*: dropping it would silently
    delete data whenever the record schema changed.
    """
    records = read_jsonl(path)
    if not records or not max_age_days:
        return records

    cutoff = hfit_time.now() - dt.timedelta(days=max_age_days)
    kept = []
    for record in records:
        when = hfit_time.parse_iso(record.get(ts_key))
        if when is None or when >= cutoff:
            kept.append(record)

    if len(kept) != len(records):
        # The records came out of this file, so they re-serialise. A failure here
        # leaves the unpruned file in place, which is the safe direction.
        _write_text_atomic(path, "".join(json.dumps(r) + "\n" for r in kept))
    return kept
