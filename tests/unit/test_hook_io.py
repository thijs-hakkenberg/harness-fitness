"""Hook stdin/stdout protocol and fail-open discipline.

A hook that crashes is worse than a hook that does nothing, because it prints a
traceback into the user's session and — depending on the event — can interrupt
work. So every hook body runs inside ``fail_open``: any exception becomes exit 0
with empty stdout *and empty stderr*.

Empty stderr matters as much as exit 0. Claude Code surfaces hook stderr, so a
swallowed exception that still prints is a visible failure of a measurement tool
the user did not ask to hear from. ``HFIT_DEBUG=1`` turns the swallowing off, so
the discipline does not also make the plugin undebuggable.
"""

import io
import json

import pytest

import hook_io


class TestReadPayload:
    def test_parses_a_json_object_from_stdin(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin", io.StringIO(json.dumps({"session_id": "s1", "cwd": "/tmp"}))
        )
        assert hook_io.read_payload() == {"session_id": "s1", "cwd": "/tmp"}

    def test_empty_stdin_is_an_empty_payload(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert hook_io.read_payload() == {}

    def test_corrupt_json_is_an_empty_payload(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
        assert hook_io.read_payload() == {}

    def test_a_json_scalar_or_array_is_an_empty_payload(self, monkeypatch):
        # Callers index the result. A list would raise on `payload.get`.
        monkeypatch.setattr("sys.stdin", io.StringIO("[1, 2, 3]"))
        assert hook_io.read_payload() == {}
        monkeypatch.setattr("sys.stdin", io.StringIO('"hello"'))
        assert hook_io.read_payload() == {}

    def test_an_unreadable_stdin_is_an_empty_payload(self, monkeypatch):
        class Exploding:
            def read(self):
                raise IOError("closed")

        monkeypatch.setattr("sys.stdin", Exploding())
        assert hook_io.read_payload() == {}


class TestSucceed:
    def test_exits_zero_with_no_output(self, capsys):
        with pytest.raises(SystemExit) as exc:
            hook_io.succeed()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestFailOpen:
    def test_a_clean_body_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            hook_io.fail_open(lambda: None)
        assert exc.value.code == 0
        assert capsys.readouterr().out == ""

    def test_an_exception_becomes_exit_zero_with_no_output_at_all(self, capsys):
        def boom():
            raise RuntimeError("this must never reach the user")

        with pytest.raises(SystemExit) as exc:
            hook_io.fail_open(boom)
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    @pytest.mark.parametrize(
        "exc",
        [
            KeyError("k"),
            ValueError("v"),
            OSError("disk"),
            TypeError("t"),
            AttributeError("a"),
        ],
    )
    def test_every_ordinary_exception_class_is_swallowed(self, exc, capsys):
        def boom():
            raise exc

        with pytest.raises(SystemExit) as got:
            hook_io.fail_open(boom)
        assert got.value.code == 0
        assert capsys.readouterr().err == ""

    def test_partial_stdout_is_suppressed_when_the_body_then_fails(self, capsys):
        # A half-emitted JSON response is a protocol violation, so output is
        # buffered and only released if the body completes.
        def half_then_boom():
            hook_io.emit({"hookSpecificOutput": {"partial": True}})
            raise RuntimeError("too late")

        with pytest.raises(SystemExit):
            hook_io.fail_open(half_then_boom)
        assert capsys.readouterr().out == ""

    def test_a_completed_body_releases_its_emitted_output(self, capsys):
        def ok():
            hook_io.emit({"continue": True})

        with pytest.raises(SystemExit) as exc:
            hook_io.fail_open(ok)
        assert exc.value.code == 0
        assert json.loads(capsys.readouterr().out) == {"continue": True}

    def test_a_deliberate_sys_exit_is_honoured_not_swallowed(self, capsys):
        def deliberate():
            raise SystemExit(2)

        with pytest.raises(SystemExit) as exc:
            hook_io.fail_open(deliberate)
        assert exc.value.code == 2

    def test_keyboard_interrupt_is_not_swallowed(self):
        def interrupted():
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            hook_io.fail_open(interrupted)

    def test_hfit_debug_re_raises_so_the_plugin_stays_debuggable(self, monkeypatch):
        monkeypatch.setenv("HFIT_DEBUG", "1")

        def boom():
            raise RuntimeError("visible under debug")

        with pytest.raises(RuntimeError, match="visible under debug"):
            hook_io.fail_open(boom)

    def test_hfit_debug_set_to_zero_still_swallows(self, monkeypatch, capsys):
        # "0" must mean off, or a stray export turns tracebacks on for everyone.
        monkeypatch.setenv("HFIT_DEBUG", "0")

        def boom():
            raise RuntimeError("hidden")

        with pytest.raises(SystemExit) as exc:
            hook_io.fail_open(boom)
        assert exc.value.code == 0
        assert capsys.readouterr().err == ""


class TestPayloadHelpers:
    def test_cwd_prefers_the_payload_over_the_environment(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/env/dir")
        assert hook_io.payload_cwd({"cwd": "/payload/dir"}) == "/payload/dir"

    def test_cwd_falls_back_to_claude_project_dir(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/env/dir")
        assert hook_io.payload_cwd({}) == "/env/dir"

    def test_cwd_falls_back_to_the_process_directory(self, monkeypatch):
        import os

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert hook_io.payload_cwd({}) == os.getcwd()

    def test_session_id_is_read_from_the_payload(self):
        assert hook_io.payload_session_id({"session_id": "abc"}) == "abc"

    def test_a_missing_session_id_is_none_not_a_placeholder(self):
        # A synthesised id would silently merge two sessions' events.
        assert hook_io.payload_session_id({}) is None

    @pytest.mark.parametrize("payload", [None, [], "text", 7])
    def test_a_non_object_payload_degrades_rather_than_raising(
        self, payload, monkeypatch
    ):
        # `read_payload` normalises to a dict, but these helpers are also called
        # with whatever a caller has in hand.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/env/dir")
        assert hook_io.payload_cwd(payload) == "/env/dir"
        assert hook_io.payload_session_id(payload) is None

    def test_a_blank_cwd_in_the_payload_is_not_used(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/env/dir")
        assert hook_io.payload_cwd({"cwd": ""}) == "/env/dir"
