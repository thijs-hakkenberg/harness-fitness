"""`skills/*/SKILL.md` — the surface a user actually invokes, read as data.

A skill is prose, and prose is the one artefact in a plugin that no test drives by
accident. Every failure below is invisible from an otherwise green suite, and from
inside a session each is indistinguishable from a plugin that does nothing:

- a frontmatter `name` that does not match its directory, so Claude Code loads
  nothing at all;
- a documented command naming a script that does not exist, or spelled in a way that
  cannot run — the command is the whole interface, and it is never executed by any
  other test;
- **a gap kind the CLI can emit that the skill has no instruction for.** This one is
  worse than the others, because nothing breaks: the model meets an unfamiliar
  `kind`, writes a plausible sentence about it, and the user reads an invented
  explanation from a tool whose entire purpose is to not invent numbers.

So the documented command is extracted and actually run, and the gap kinds are bound
to the CLI's own declaration rather than to a list retyped here.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from conftest import HOOKS_SCRIPTS, REPO_ROOT

if str(HOOKS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(HOOKS_SCRIPTS))

import fitness  # noqa: E402

SKILLS = REPO_ROOT / "skills"

_FENCE = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)


def skill_dirs():
    if not SKILLS.is_dir():
        return []
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


def _frontmatter(text):
    """The `---` block at the head of a skill, as a dict of top-level scalars.

    Hand-rolled rather than via PyYAML: the suite's only dependencies are pytest and
    pytest-bdd, and a skill's frontmatter is three scalar keys and a list. A parser
    that cannot read a nested structure is the right parser here — if one appears,
    this raises rather than silently reading half of it.
    """
    if not text.startswith("---\n"):
        raise AssertionError("no frontmatter block")
    end = text.index("\n---\n", 3)
    out = {}
    key = None
    for line in text[4:end].split("\n"):
        if not line.strip():
            continue
        if line.startswith("  - ") and key:
            out.setdefault(key, []).append(line[4:].strip())
        elif ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            out[key] = value if value else []
        else:
            raise AssertionError("unparseable frontmatter line: %r" % line)
    return out


def _commands(text):
    return [m.group(1).strip() for m in _FENCE.finditer(text)]


def _skill_text(path):
    with open(path / "SKILL.md") as fh:
        return fh.read()


# Parametrised over the directory listing rather than over a hard-coded name, so the
# next four skills inherit every assertion here the moment they are created.
@pytest.fixture(params=[p.name for p in skill_dirs()] or [pytest.param(None)])
def skill(request):
    if request.param is None:
        pytest.fail("no skills are defined")
    return SKILLS / request.param


class TestItLoads:
    def test_the_directory_has_a_manifest(self, skill):
        assert (skill / "SKILL.md").is_file()

    def test_the_name_matches_the_directory(self, skill):
        # Claude Code resolves a skill by its directory. A mismatch here does not
        # produce an error anywhere; the skill simply never appears.
        front = _frontmatter(_skill_text(skill))

        assert front["name"] == skill.name

    def test_the_description_says_what_it_reports(self, skill):
        # The description is the only thing the model sees when deciding whether
        # this skill is the right one. A stub of a few words gets it invoked for
        # questions it cannot answer.
        front = _frontmatter(_skill_text(skill))

        assert len(front["description"]) >= 40

    def test_it_declares_a_trigger(self, skill):
        front = _frontmatter(_skill_text(skill))

        assert front["triggers"], "a skill nobody can invoke by name"


class TestTheDocumentedCommand:
    def test_it_names_a_script_that_exists(self, skill):
        # `${CLAUDE_PLUGIN_ROOT}` is the only correct way to name a script from a
        # skill — a relative path resolves against the user's cwd, which is not the
        # plugin. Checked by resolving it, so a typo in the filename fails here
        # rather than in a session.
        commands = _commands(_skill_text(skill))
        assert commands, "a skill with no documented command"

        scripts = 0
        for cmd in commands:
            for match in re.finditer(r"\$\{CLAUDE_PLUGIN_ROOT\}(/[^\"'\s]+)", cmd):
                target = REPO_ROOT / match.group(1).lstrip("/")
                assert target.is_file(), "%s names %s" % (skill.name, target)
                scripts += 1
        assert scripts, "no ${CLAUDE_PLUGIN_ROOT} script in any command block"

    def test_it_runs_and_prints_one_parseable_json_object(self, skill, isolated_home):
        # The assertion this file exists for. A skill's command is prose until
        # something runs it, and every other test in the suite invokes the CLI by
        # its own argv rather than by the string a user's session will execute.
        #
        # Run unacknowledged on purpose: the refusal is the path most first-time
        # readers hit, it must still exit 0 with parseable JSON, and it needs no
        # fixture to set up — so this cannot pass because a fixture happened to
        # leave the right state behind.
        commands = _commands(_skill_text(skill))
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)

        ran = 0
        for cmd in commands:
            if "${CLAUDE_PLUGIN_ROOT}" not in cmd:
                continue
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, env=env, timeout=60,
            )
            assert result.returncode == 0, "%s: %s" % (cmd, result.stderr)
            assert result.stderr == ""
            payload = json.loads(result.stdout)
            assert payload["ok"] is False
            ran += 1
        assert ran


class TestItCannotInventAnExplanation:
    def test_every_gap_kind_the_cli_can_emit_is_named(self, skill):
        # Bound to the CLI's own declaration, not to a list retyped here, so adding
        # a kind without telling the skill about it fails. The failure mode this
        # guards is silent: an undocumented kind does not error, it gets narrated.
        text = _skill_text(skill)

        missing = [k for k in fitness.GAP_KINDS if k not in text]
        assert missing == [], "gap kinds with no instruction: %s" % missing

    def test_it_tells_the_reader_to_check_ok_first(self, skill):
        # The one instruction shared by every skill in this plugin. A skill that
        # reads `current` before `ok` reports "no composition recorded" for an
        # install that was never acknowledged, which sends the user looking for a
        # bug instead of a decision they have not made.
        text = _skill_text(skill)

        assert "`ok`" in text

    def test_it_forbids_filling_in_a_null(self, skill):
        # `measures: null` is the normal state at 0.1.0, and a helpful model asked
        # about harness fitness will happily estimate one. The skill has to say not
        # to, in words, because nothing else can stop it.
        #
        # Deliberately not "the words `null` and `never` both appear somewhere":
        # every skill here says `never` about half a dozen things, so that version
        # passes for a document with no such instruction at all — and it passes
        # unchanged when a prohibition is flipped into a permission. Mutation-tested
        # both ways. So the assertion is on a negated verb *of fabrication*, on one
        # line, which is the instruction rather than its vocabulary.
        text = _skill_text(skill).lower()
        assert "null" in text, "a skill that never mentions the value it will meet"

        forbids = [
            line
            for line in text.replace("*", "").replace("`", "").split("\n")
            if any(n in line for n in ("do not", "don't", "never"))
            and any(v in line for v in ("invent", "estimate", "guess", "fill in", "fabricat"))
        ]
        assert forbids, "no instruction against inventing a number"
