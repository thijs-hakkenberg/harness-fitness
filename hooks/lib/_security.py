"""Path refusal and one-way reduction of sensitive strings.

Two rules, both because of what this plugin reads:

``transcript_path`` arrives inside a hook payload we do not control, so it is
refused unless its *realpath* resolves under ``~/.claude``. Realpath rather than
a prefix check, because a symlink placed inside ``~/.claude`` would otherwise be
enough to make this plugin read an arbitrary file.

Hook and MCP command strings are read from settings on every snapshot. They
contain absolute home paths and sometimes credentials, so they are reduced to a
basename plus a digest and the original is never returned, logged or stored.
"""

import hashlib
import os

# Tokens that identify an interpreter or shell plumbing rather than the hook
# itself. `command -v python3 ... && python3 <script>` is the portable
# invocation form, so the first token is almost never the informative one.
_NOISE = frozenset(
    {
        "command",
        "-v",
        "&&",
        "||",
        ";",
        "exec",
        "env",
        "python",
        "python3",
        "sh",
        "bash",
        "zsh",
        "node",
        "npx",
        "deno",
        "bun",
        "uv",
        "uvx",
        "ruby",
        "perl",
    }
)

_SCRIPT_SUFFIXES = (
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".rb",
    ".pl",
    ".exs",
)

_UNKNOWN = "unknown"


def _claude_root():
    return os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude"))


def _resolve_under_claude(path):
    """The realpath of `path` if it is strictly inside ``~/.claude``, else ``None``.

    Realpath rather than a prefix check on the given string: a symlink placed
    inside ``~/.claude`` and pointing at ``~/.ssh/id_rsa`` would otherwise pass.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        resolved = os.path.realpath(path)
        root = _claude_root()
    except (OSError, ValueError):
        # A path with an embedded null byte raises rather than returning.
        return None
    if resolved == root or not resolved.startswith(root + os.sep):
        return None
    return resolved


def is_path_under_claude(path):
    """True only if `path` resolves to something strictly inside ``~/.claude``."""
    return _resolve_under_claude(path) is not None


def safe_transcript_path(path):
    """The resolved transcript path, or ``None`` if it may not be read."""
    resolved = _resolve_under_claude(path)
    if resolved is None or not os.path.isfile(resolved):
        return None
    return resolved


def _strip_quotes(token):
    for quote in ('"', "'"):
        if len(token) >= 2 and token.startswith(quote) and token.endswith(quote):
            return token[1:-1]
    return token.strip("\"'")


def _command_basename(command):
    """The most informative filename in a command string, without its path."""
    tokens = [_strip_quotes(t) for t in command.split()]
    tokens = [t for t in tokens if t]

    for token in tokens:
        base = os.path.basename(token.rstrip("/"))
        if base.lower().endswith(_SCRIPT_SUFFIXES):
            return base

    for token in tokens:
        if token in _NOISE or token.startswith("-"):
            continue
        # Redirections and anything carrying an `=` (an inline env assignment,
        # which is where a token would hide) are not names.
        if any(ch in token for ch in ">|<=") or token.startswith("$"):
            continue
        base = os.path.basename(token.rstrip("/"))
        if base:
            return base

    return _UNKNOWN


def hash_command(command):
    """Reduce a command string to ``{cmd_basename, cmd_digest}``.

    The key set is exactly these two. Returning the original under any key —
    including for debugging — puts a credential in a record on disk.
    """
    if not isinstance(command, str) or not command.strip():
        return {"cmd_basename": _UNKNOWN, "cmd_digest": hash_text("", 12)}
    return {
        "cmd_basename": _command_basename(command),
        "cmd_digest": hash_text(command, 12),
    }


def hash_text(text, length=12):
    """A truncated sha256 of `text`, or ``None`` if `text` is ``None``."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:length]
