"""Test isolation and fixtures for harness-fitness.

Two guarantees are autouse, because a leak in either direction shows up as a
*passing* test rather than a failing one:

1. ``HOME`` is redirected into ``tmp_path``, so nothing reads or writes the
   developer's real ``~/.claude`` — which on a working machine contains live
   credentials and 99 real project histories.
2. ``bd`` and ``git`` resolve to recording stubs at the front of ``PATH``, so no
   test can mutate a real issue database or repository. The stubs record every
   argv, which is what lets a test assert on what *would* have been written.

Everything else is an explicit factory fixture. The three that matter —
``otel_log``, ``transcript`` and ``settings_tree`` — synthesise the three input
shapes this plugin reads, in the exact wire form measured on a live machine
rather than an idealised version of it.
"""

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_LIB = REPO_ROOT / "hooks" / "lib"
HOOKS_SCRIPTS = REPO_ROOT / "hooks" / "scripts"

# Hook scripts import their siblings by flat module name off `hooks/lib`. The
# suite imports them the same way so an import that works under pytest also
# works under a real hook invocation.
if str(HOOKS_LIB) not in sys.path:
    sys.path.insert(0, str(HOOKS_LIB))


# --------------------------------------------------------------------------
# Recording stubs for external binaries
# --------------------------------------------------------------------------

_STUB_SRC = '''#!/usr/bin/env python3
"""Recording stub. Appends its argv to $HFIT_STUB_RECORD and replies per rules."""
import json
import os
import sys

name = os.path.basename(sys.argv[0])
argv = sys.argv[1:]

record = os.environ.get("HFIT_STUB_RECORD")
if record:
    with open(record, "a") as fh:
        fh.write(json.dumps({"bin": name, "argv": argv}) + "\\n")

rc, out, err = 0, "", ""
rules_path = os.environ.get("HFIT_STUB_RULES")
if rules_path and os.path.exists(rules_path):
    try:
        with open(rules_path) as fh:
            rules = json.load(fh)
    except Exception:
        rules = []
    for rule in rules:
        if rule.get("bin") != name:
            continue
        match = rule.get("match") or []
        if argv[: len(match)] == match:
            rc = rule.get("rc", 0)
            out = rule.get("stdout", "")
            err = rule.get("stderr", "")
            break

sys.stdout.write(out)
sys.stderr.write(err)
sys.exit(rc)
'''


class Stubs:
    """Handle on the stubbed binaries: set replies, read back what was called."""

    def __init__(self, bindir, record_path, rules_path):
        self.bindir = Path(bindir)
        self.record_path = Path(record_path)
        self.rules_path = Path(rules_path)
        self._rules = []
        self._flush()

    def _flush(self):
        self.rules_path.write_text(json.dumps(self._rules))

    def on(self, binary, match, rc=0, stdout="", stderr=""):
        """Reply to `binary` when argv starts with `match`. Newest rule wins."""
        if isinstance(stdout, (dict, list)):
            stdout = json.dumps(stdout)
        self._rules.insert(
            0,
            {
                "bin": binary,
                "match": list(match),
                "rc": rc,
                "stdout": stdout,
                "stderr": stderr,
            },
        )
        self._flush()
        return self

    def add(self, binary):
        """Install an additional stubbed binary name."""
        target = self.bindir / binary
        target.write_text(_STUB_SRC)
        target.chmod(0o755)
        return self

    def calls(self, binary=None):
        """Every recorded invocation, oldest first, as a list of argv lists."""
        if not self.record_path.exists():
            return []
        out = []
        for line in self.record_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if binary is None or rec.get("bin") == binary:
                out.append(rec.get("argv") or [])
        return out

    def reset(self):
        if self.record_path.exists():
            self.record_path.unlink()
        self._rules = []
        self._flush()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Redirect HOME into tmp_path and clear every env var we honour."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # harmless on posix, correct on win
    for var in (
        "HFIT_STATE_DIR",
        "HFIT_NOW",
        "HFIT_DEBUG",
        "HFIT_REAL_BD_TESTS",
        "CLAUDE_PROJECT_DIR",
        "CLAUDE_PLUGIN_ROOT",
        "BEADS_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture(autouse=True)
def stub_bin(tmp_path, monkeypatch):
    """Put recording stubs for `bd` and `git` at the front of PATH."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    record = tmp_path / "stub-calls.jsonl"
    rules = tmp_path / "stub-rules.json"

    monkeypatch.setenv("HFIT_STUB_RECORD", str(record))
    monkeypatch.setenv("HFIT_STUB_RULES", str(rules))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))

    stubs = Stubs(bindir, record, rules)
    for binary in ("bd", "git"):
        stubs.add(binary)
    return stubs


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """A tmp project directory, exported as CLAUDE_PROJECT_DIR."""
    proj = tmp_path / "project"
    proj.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    return proj


@pytest.fixture
def frozen_now(monkeypatch):
    """Freeze time. Call with an ISO string; defaults to a fixed instant."""

    def _freeze(when="2026-09-14T12:00:00Z"):
        monkeypatch.setenv("HFIT_NOW", when)
        return when

    return _freeze


# --------------------------------------------------------------------------
# settings_tree — layered settings, installed plugins, MCP config, plugin dirs
# --------------------------------------------------------------------------


class SettingsTree:
    def __init__(self, home, project):
        self.home = Path(home)
        self.project = Path(project) if project else None
        self.plugin_dirs = {}


@pytest.fixture
def settings_tree(isolated_home, tmp_path):
    """Build a layered settings tree under the isolated HOME.

    `plugins` maps a plugin key to a spec::

        {"abacus@abacus": {
            "version": "0.3.1",
            "gitCommitSha": "abc123",
            "scope": "user",
            "marketplace": "abacus",
            "hooks": {"PreToolUse": [{"matcher": "Edit|Write",
                                      "hooks": [{"type": "command",
                                                 "command": "/x/gate.py"}]}]},
            "skills": ["audit"],
            "agents": ["abacus-auditor"],
            "mcp": {"servers": {...}},
        }}

    Directories are created on disk, because the inventory reads them there
    rather than trusting the manifest.

    **There is one HOME per test, so repeat calls overwrite rather than
    accumulate.** A machine has one `~/.claude`, and the fixture models that
    faithfully — but it means two `settings_tree()` calls in a single test do not
    give you two trees to compare. Build a tree and read it before building the
    next one::

        # Right — build-then-read, twice, in one expression each.
        assert digest_for(settings_tree(user=A)) != digest_for(settings_tree(user=B))

        # Wrong — both reads see tree B.
        a = settings_tree(user=A)
        b = settings_tree(user=B)
        assert digest_for(a) != digest_for(b)

    The wrong form is dangerous in both directions. A "this moves" assertion fails
    for a reason that looks like an implementation bug, which is merely annoying;
    a "this holds still" assertion *passes without comparing anything*, which is
    how a hash ends up with no guard at all against spurious movement. That
    happened once, in `test_env_pin.py`, and was only caught because the failures
    sat next to the vacuous passes. Python evaluates the operands of `!=`
    left-to-right, so keeping each side a single build-and-read expression makes
    the ordering correct by construction.
    """

    def _build(
        user=None,
        user_local=None,
        project=None,
        project_local=None,
        plugins=None,
        marketplaces=None,
        mcp=None,
        project_root=None,
    ):
        home = isolated_home
        claude = home / ".claude"
        claude.mkdir(parents=True, exist_ok=True)

        proj_root = Path(project_root) if project_root else (tmp_path / "project")
        proj_root.mkdir(parents=True, exist_ok=True)

        tree = SettingsTree(home, proj_root)

        plugins = plugins or {}
        installed = {}
        plugins_dir = claude / "plugins"
        plugins_dir.mkdir(exist_ok=True)

        for key, spec in plugins.items():
            spec = dict(spec or {})
            name = key.split("@")[0]
            pdir = plugins_dir / "repos" / key.replace("@", "__") / name
            pdir.mkdir(parents=True, exist_ok=True)
            tree.plugin_dirs[key] = pdir

            manifest = {"name": name, "version": spec.get("version", "0.0.1")}
            manifest.update(spec.get("manifest") or {})
            (pdir / ".claude-plugin").mkdir(exist_ok=True)
            (pdir / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(manifest, indent=2)
            )

            if spec.get("hooks"):
                (pdir / "hooks").mkdir(exist_ok=True)
                (pdir / "hooks" / "hooks.json").write_text(
                    json.dumps({"hooks": spec["hooks"]}, indent=2)
                )
            for skill in spec.get("skills") or []:
                sdir = pdir / "skills" / skill
                sdir.mkdir(parents=True, exist_ok=True)
                (sdir / "SKILL.md").write_text(
                    "---\nname: %s\ndescription: test skill\n---\n\nbody\n" % skill
                )
            for agent in spec.get("agents") or []:
                adir = pdir / "agents"
                adir.mkdir(parents=True, exist_ok=True)
                (adir / ("%s.md" % agent)).write_text(
                    "---\nname: %s\ndescription: test agent\n---\n\nbody\n" % agent
                )
            if spec.get("mcp"):
                (pdir / ".mcp.json").write_text(json.dumps(spec["mcp"], indent=2))

            entry = {
                "installPath": str(pdir),
                "version": spec.get("version", "0.0.1"),
                "scope": spec.get("scope", "user"),
            }
            if "gitCommitSha" in spec:
                entry["gitCommitSha"] = spec["gitCommitSha"]
            if "installedAt" in spec:
                entry["installedAt"] = spec["installedAt"]
            if "lastUpdated" in spec:
                entry["lastUpdated"] = spec["lastUpdated"]
            installed[key] = entry

        if plugins:
            (plugins_dir / "installed_plugins.json").write_text(
                json.dumps({"plugins": installed}, indent=2)
            )
        if marketplaces is not None:
            (plugins_dir / "known_marketplaces.json").write_text(
                json.dumps(marketplaces, indent=2)
            )

        if user is not None:
            (claude / "settings.json").write_text(json.dumps(user, indent=2))
        if user_local is not None:
            (claude / "settings.local.json").write_text(json.dumps(user_local, indent=2))

        pclaude = proj_root / ".claude"
        if project is not None:
            pclaude.mkdir(parents=True, exist_ok=True)
            (pclaude / "settings.json").write_text(json.dumps(project, indent=2))
        if project_local is not None:
            pclaude.mkdir(parents=True, exist_ok=True)
            (pclaude / "settings.local.json").write_text(
                json.dumps(project_local, indent=2)
            )
        if mcp is not None:
            (proj_root / ".mcp.json").write_text(json.dumps(mcp, indent=2))

        return tree

    return _build


# --------------------------------------------------------------------------
# otel_log — OTLP-JSON log records in the shape a live machine emits
# --------------------------------------------------------------------------


def _otlp_value(v):
    """Wrap a Python scalar the way OTLP-JSON does.

    int64 is JSON-encoded as a *string* per the proto3 JSON mapping, which is
    the trap this wrapper exists to reproduce faithfully.
    """
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


@pytest.fixture
def otel_log(isolated_home):
    """Write OTLP-JSON log lines to the path Claude Code exports to.

    Each record spec is ``{"event": "tool_decision", "session": "...",
    "ts": <unix seconds>, "attrs": {...}}``. Resource-level and common
    attributes are filled in so the record matches a real one, including the
    dotted ``session.id`` key that a naive reader looks for as ``session_id``.
    """

    logdir = isolated_home / ".claude" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "claude-code-events.jsonl"

    def _write(records, session="test-session", app_version="2.1.270", path=path):
        lines = []
        for i, spec in enumerate(records):
            event = spec["event"]
            ts_ns = int(float(spec.get("ts", 1789000000 + i)) * 1_000_000_000)
            attrs = {
                "session.id": spec.get("session", session),
                "app.version": app_version,
                "app.entrypoint": "cli",
                "event.name": event,
                "event.sequence": i,
                "event.timestamp": ts_ns,
            }
            attrs.update(spec.get("attrs") or {})
            record = {
                "timeUnixNano": str(ts_ns),
                "observedTimeUnixNano": str(ts_ns),
                "body": {"stringValue": "claude_code.%s" % event},
                "attributes": [
                    {"key": k, "value": _otlp_value(v)} for k, v in attrs.items()
                ],
            }
            lines.append(
                json.dumps(
                    {
                        "resourceLogs": [
                            {
                                "resource": {
                                    "attributes": [
                                        {
                                            "key": "service.name",
                                            "value": {"stringValue": "claude-code"},
                                        },
                                        {
                                            "key": "service.version",
                                            "value": {"stringValue": app_version},
                                        },
                                    ]
                                },
                                "scopeLogs": [
                                    {
                                        "scope": {
                                            "name": "com.anthropic.claude_code.events",
                                            "version": app_version,
                                        },
                                        "logRecords": [record],
                                    }
                                ],
                            }
                        ]
                    }
                )
            )
        Path(path).write_text("\n".join(lines) + "\n")
        return Path(path)

    _write.path = path
    return _write


# --------------------------------------------------------------------------
# transcript — session JSONL, including the real wrapper shapes
# --------------------------------------------------------------------------

INTERRUPT_LITERAL = "[Request interrupted by user for tool use]"


@pytest.fixture
def transcript(isolated_home):
    """Write a session transcript under the isolated HOME.

    A plain string becomes a user message; a dict is written through verbatim so
    a test can reproduce the wrapper shapes that a naive human-turn filter
    mistakes for real prompts — ``<local-command-caveat>``, ``<command-name>``,
    ``<local-command-stdout>``, ``isMeta`` and ``isSidechain``.
    """

    def _write(lines, slug="-tmp-project", session="test-session"):
        d = isolated_home / ".claude" / "projects" / slug
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("%s.jsonl" % session)
        out = []
        for i, item in enumerate(lines):
            if isinstance(item, str):
                item = {
                    "type": "user",
                    "message": {"role": "user", "content": item},
                }
            item.setdefault("sessionId", session)
            item.setdefault("uuid", "uuid-%d" % i)
            out.append(json.dumps(item))
        path.write_text("\n".join(out) + "\n")
        return path

    return _write


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture
def run_hook():
    """Run a hook script as a real subprocess with JSON on stdin.

    In-process calls would not catch an import error, a stdout-protocol
    violation or a non-zero exit — which are exactly the failure modes that make
    a hook indistinguishable from a plugin that does nothing.
    """
    import subprocess

    def _run(script, payload=None, raw_stdin=None, env=None, cwd=None, timeout=60):
        path = HOOKS_SCRIPTS / script
        stdin = raw_stdin if raw_stdin is not None else json.dumps(payload or {})
        proc_env = dict(os.environ)
        proc_env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
        proc_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env:
            proc_env.update({k: str(v) for k, v in env.items()})
        return subprocess.run(
            [sys.executable, str(path)],
            input=stdin,
            env=proc_env,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    return _run


def files_under(root):
    """Every file below `root`, as a sorted list of relative posix paths."""
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(root).as_posix()) for p in root.rglob("*") if p.is_file()
    )
