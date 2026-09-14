"""Path refusal and command hashing.

Two attack surfaces, both real:

``transcript_path`` arrives inside an untrusted hook payload. Nothing stops a
payload naming ``~/.ssh/id_rsa``, so the path is refused unless its *realpath*
resolves under ``~/.claude`` — realpath, because a symlink inside ``~/.claude``
pointing anywhere else would otherwise pass a prefix check.

Hook and MCP command strings are read from settings on every snapshot. On this
machine ``~/.claude/settings.json`` holds a live credential in ``env``, and a
command string routinely embeds an absolute home path. So a command is reduced
to a basename plus a digest, and the original never reaches a record.
"""

import os

import pytest

import _security


@pytest.fixture
def claude_home(isolated_home):
    return isolated_home / ".claude"


class TestIsPathUnderClaude:
    def test_a_file_directly_under_claude_is_accepted(self, claude_home):
        p = claude_home / "projects" / "-x" / "s.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("{}\n")
        assert _security.is_path_under_claude(str(p)) is True

    def test_traversal_out_of_claude_is_refused(self, isolated_home, claude_home):
        secret = isolated_home / ".ssh" / "id_rsa"
        secret.parent.mkdir(parents=True)
        secret.write_text("PRIVATE KEY")
        sneaky = str(claude_home / ".." / ".ssh" / "id_rsa")
        assert _security.is_path_under_claude(sneaky) is False

    def test_a_symlink_inside_claude_pointing_outside_is_refused(
        self, isolated_home, claude_home
    ):
        # A prefix check on the *given* path would accept this. Only realpath
        # resolution catches it.
        secret = isolated_home / "elsewhere" / "id_rsa"
        secret.parent.mkdir(parents=True)
        secret.write_text("PRIVATE KEY")
        link = claude_home / "innocent.jsonl"
        os.symlink(str(secret), str(link))
        assert _security.is_path_under_claude(str(link)) is False

    def test_a_path_that_merely_shares_a_prefix_is_refused(self, isolated_home):
        # "~/.claude-evil" starts with "~/.claude" as a string but is not under it.
        evil = isolated_home / ".claude-evil" / "s.jsonl"
        evil.parent.mkdir(parents=True)
        evil.write_text("{}\n")
        assert _security.is_path_under_claude(str(evil)) is False

    def test_claude_itself_is_not_a_file_under_claude(self, claude_home):
        assert _security.is_path_under_claude(str(claude_home)) is False

    @pytest.mark.parametrize("raw", [None, "", 42, {}, []])
    def test_non_string_input_is_refused_without_raising(self, raw):
        assert _security.is_path_under_claude(raw) is False


class TestPathResolutionFailure:
    def test_a_path_that_cannot_be_resolved_is_refused(self):
        # An embedded null byte makes realpath raise rather than return. The
        # payload this arrives in is not ours to trust.
        assert _security.is_path_under_claude("\x00") is False
        assert _security.safe_transcript_path("\x00") is None


class TestSafeTranscriptPath:
    def test_returns_the_resolved_path_for_a_legitimate_transcript(self, claude_home):
        p = claude_home / "projects" / "-x" / "s.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("{}\n")
        assert _security.safe_transcript_path(str(p)) == os.path.realpath(str(p))

    def test_returns_none_for_a_path_outside_claude(self, isolated_home):
        outside = isolated_home / "notes.jsonl"
        outside.write_text("{}\n")
        assert _security.safe_transcript_path(str(outside)) is None

    def test_returns_none_when_the_file_does_not_exist(self, claude_home):
        assert _security.safe_transcript_path(str(claude_home / "missing.jsonl")) is None

    @pytest.mark.parametrize("raw", [None, "", 42, {}])
    def test_returns_none_for_junk(self, raw):
        assert _security.safe_transcript_path(raw) is None


# A synthetic credential, shaped like the real one this module exists to keep out
# of records: `~/.claude/settings.json` holds a Databricks PAT in
# `env.ANTHROPIC_AUTH_TOKEN`, and a test that plants a value of a different shape
# would not exercise the same code path.
#
# Assembled from parts rather than written as one literal. GitHub's secret
# scanning matches on file content, so the contiguous form gets a public push
# rejected — and the remedy on offer is to allowlist the pattern, which would
# mean a repository that reports a secret it does not have. The assembled value
# is byte-identical, so every assertion below means exactly what it did before.
PLANTED = "dapi" + "0123456789abcdef" * 2


class TestHashCommand:
    def test_returns_a_basename_and_a_twelve_hex_digest(self):
        got = _security.hash_command(
            "/Users/someone/.claude/plugins/abacus/hooks/scripts/gate.py"
        )
        assert got["cmd_basename"] == "gate.py"
        assert len(got["cmd_digest"]) == 12
        assert all(c in "0123456789abcdef" for c in got["cmd_digest"])

    def test_is_deterministic(self):
        cmd = "python3 /x/y/z.py --flag"
        assert _security.hash_command(cmd) == _security.hash_command(cmd)

    def test_different_commands_get_different_digests(self):
        a = _security.hash_command("python3 /x/a.py")
        b = _security.hash_command("python3 /x/b.py")
        assert a["cmd_digest"] != b["cmd_digest"]

    def test_the_basename_is_the_script_not_the_interpreter(self):
        # A command is usually "python3 <path>"; the informative token is the
        # path, so a naive split()[0] would label every hook "python3".
        got = _security.hash_command(
            'command -v python3 >/dev/null 2>&1 && python3 "/p/hooks/scripts/snap.py" || python "/p/hooks/scripts/snap.py"'
        )
        assert got["cmd_basename"] == "snap.py"

    def test_never_echoes_a_planted_credential(self):
        got = _security.hash_command(
            "curl -H 'Authorization: Bearer %s' https://example.invalid" % PLANTED
        )
        assert PLANTED not in repr(got)
        for value in got.values():
            assert PLANTED not in str(value)

    def test_never_echoes_an_absolute_home_path(self, isolated_home):
        home = str(isolated_home)
        got = _security.hash_command("%s/.claude/hooks/auto-test.sh" % home)
        assert home not in repr(got)
        assert got["cmd_basename"] == "auto-test.sh"

    def test_the_returned_keys_are_exactly_the_two_permitted_ones(self):
        # A regression that adds `cmd` back to the record would leak on every
        # snapshot, so the key set is asserted rather than the absence of a leak.
        got = _security.hash_command("python3 /x/a.py")
        assert set(got) == {"cmd_basename", "cmd_digest"}

    @pytest.mark.parametrize("raw", [None, "", 42, {}])
    def test_junk_input_yields_an_unknown_basename_not_an_exception(self, raw):
        got = _security.hash_command(raw)
        assert set(got) == {"cmd_basename", "cmd_digest"}
        assert got["cmd_basename"] == "unknown"

    def test_a_flag_is_never_the_basename(self):
        # `pipefail` is a poor label but an honest one — it is a token from the
        # command. The rule only has to guarantee the *flag* is never picked;
        # recognising shell option words would need a table nobody maintains.
        got = _security.hash_command("bash -euo pipefail")
        assert not got["cmd_basename"].startswith("-")

    def test_a_command_of_nothing_but_flags_has_no_name(self):
        assert _security.hash_command("bash -euo")["cmd_basename"] == "unknown"

    def test_an_inline_env_assignment_is_never_the_basename(self):
        # This is precisely where a credential hides: `TOKEN=dapi… run.sh`. If
        # the assignment were picked as the name, the token would be the record.
        got = _security.hash_command("ANTHROPIC_AUTH_TOKEN=%s ./run" % PLANTED)
        assert got["cmd_basename"] == "run"
        assert PLANTED not in repr(got)

    def test_a_redirection_is_never_the_basename(self):
        got = _security.hash_command(">/dev/null 2>&1 tidy")
        assert got["cmd_basename"] == "tidy"

    def test_a_variable_reference_is_never_the_basename(self):
        # `${CLAUDE_PLUGIN_ROOT}/x.py` is the real form, so a bare `$VAR` with no
        # recognisable script beside it has no name we can honestly report.
        assert _security.hash_command("$MY_HOOK")["cmd_basename"] == "unknown"

    def test_a_quoted_script_path_is_unquoted(self):
        got = _security.hash_command("python3 '/p/hooks/scripts/snap.py'")
        assert got["cmd_basename"] == "snap.py"


class TestHashText:
    def test_prefix_length_is_configurable_and_deterministic(self):
        assert _security.hash_text("hello", 8) == _security.hash_text("hello", 8)
        assert len(_security.hash_text("hello", 8)) == 8
        assert len(_security.hash_text("hello", 12)) == 12

    def test_does_not_contain_the_input(self):
        assert PLANTED not in _security.hash_text(PLANTED, 12)

    def test_none_hashes_to_none(self):
        assert _security.hash_text(None, 12) is None

    def test_a_non_string_is_coerced_rather_than_raising(self):
        # Settings values are not type-checked by Claude Code, so an integer or
        # a list can arrive where a command string was expected.
        assert len(_security.hash_text(42, 12)) == 12
        assert _security.hash_text(42, 12) == _security.hash_text("42", 12)
