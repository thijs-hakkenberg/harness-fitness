"""The repo's own specification layout, enforced. There is no external authority.

Four pillars plus a machine-readable manifest is a layout this repo imposes on
itself, so this module is the only thing that holds it. That bounds what a pass
means, and the bound is worth stating: these assertions check that the artefacts
**agree with each other**, never that they still describe the software. Nothing
here can tell you an ADR has gone stale — that is a human reviewer's job.

What it does catch is the class of error that lives in the gap between two files,
where no reader is looking:

- **a reference to an ADR that does not exist.** Shipped library modules, hook
  scripts, contracts and tests in this repo all cite ADR numbers by hand. A
  citation is a promise that a decision was recorded and can be read; a dangling
  one is worse than no citation at all, because it reads as verified. In a public
  repo it is a broken link a stranger finds before you do.
- **a link left as a placeholder** — `[ADR-008](.)` renders as a link, is
  clickable, and goes nowhere. It survives every other test in the suite.
- an interface in `spec.manifest.yaml` naming a script or a contract that was
  renamed, so the manifest describes a plugin that no longer exists.
- an `sla.latency_p99_ms` that no longer matches the timeout in `hooks.json`.
  Two numbers in two files with nothing keeping them together.
- a release where `plugin.json`, `marketplace.json` and `CHANGELOG.md` disagree
  about what version shipped.

The manifest is read with PyYAML, which is a test-only dependency the hooks may
not use. That import is deliberately *not* at module scope: an `importorskip` up
here would take `test_the_ci_workflow_installs_every_test_dependency` with it, and
a conformance module that silently skips in CI is the exact failure it exists to
prevent.
"""

import json
import re

import pytest

from conftest import REPO_ROOT

ADR_DIR = REPO_ROOT / "adr"
MANIFEST = REPO_ROOT / "spec.manifest.yaml"

# ADR numbers the plan allocates but has not reached yet. Listed so that a *gap*
# is a decision rather than an accident: 002 disk-is-authority-OTEL-is-stream,
# 009 two-numerator-bases, 012 tokens-per-outcome-keeps-the-rig's-definition,
# 015 published-as-an-open-source-plugin, 016 behaviour-is-inherited-code-is-not.
#
# Asserted in both directions. A number missing from `adr/` and absent here fails,
# and so does a number present in both — so writing 002 forces its removal from
# this tuple instead of leaving a reservation nobody can trust.
RESERVED = (2, 9, 12, 15, 16)

# `# ADR-NNN — Title`, then a metadata list, then exactly these three sections in
# this order. Transcribed from the six ADRs written before this test existed
# rather than invented here, so the test enforces the established shape.
ADR_SECTIONS = ("## Context", "## Decision", "## Consequences")

_ADR_FILENAME = re.compile(r"^(\d{3})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
_ADR_TITLE = re.compile(r"^# ADR-(\d{3}) — \S")

# Every way this repo cites a decision: `ADR-014` in prose, `adr/004` in a comment
# or a JSON `_comment`, `adr/005-a-pretooluse-....md` in a link.
_ADR_CITATION = re.compile(r"(?:ADR-|adr/)(\d{3})")

# A markdown link whose target is a bare directory or `.`, left behind when an ADR
# was cited before it was written. Renders and clicks; goes nowhere.
_PLACEHOLDER_LINK = re.compile(r"\[ADR-\d{3}\]\((?:\.|adr/|\.\.?/?)\)")

# Text files worth scanning for citations. Extensions rather than a directory walk
# with an exclude list, because the hazard is a citation *anywhere* — a test
# comment and a JSON `_comment` are both places one has already been left.
_SCANNED_SUFFIXES = (".md", ".py", ".json", ".yaml", ".yml", ".feature", ".ini", ".cfg")
_SKIPPED_DIRS = frozenset(
    (".git", "__pycache__", "htmlcov", ".beads", ".dolt", ".venv", "node_modules")
)


def _scanned_files():
    out = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        if _SKIPPED_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        out.append(path)
    return sorted(out)


def _adrs():
    return sorted(ADR_DIR.glob("*.md"))


def _numbers():
    return {int(p.name[:3]) for p in _adrs()}


def _rel(path):
    return str(path.relative_to(REPO_ROOT))


# ── Pillar 1: the ADRs, and whether every citation of one resolves ───────────


class TestEveryCitedDecisionIsReadable:
    def test_every_adr_number_cited_anywhere_in_the_repo_exists(self):
        # The assertion this module was written for. A dangling citation is
        # invisible from a green suite and from a session; it is found by a
        # stranger reading the repo, which is the worst possible discoverer.
        present = _numbers()
        dangling = {}
        for path in _scanned_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for number in sorted({int(m) for m in _ADR_CITATION.findall(text)}):
                if number not in present:
                    dangling.setdefault(number, []).append(_rel(path))

        assert dangling == {}, "citations of ADRs that do not exist:\n  %s" % "\n  ".join(
            "ADR-%03d cited by %s" % (n, ", ".join(sites))
            for n, sites in sorted(dangling.items())
        )

    def test_no_adr_link_is_left_as_a_placeholder(self):
        # `[ADR-008](.)` was written when 008 did not exist yet. It is a link, so
        # nothing reports it as missing — it just navigates the reader to the
        # directory listing and lets them conclude the decision was never made.
        offenders = []
        for path in _scanned_files():
            if path.suffix != ".md":
                continue
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _PLACEHOLDER_LINK.search(line):
                    offenders.append("%s:%d" % (_rel(path), line_no))

        assert offenders == [], "placeholder ADR links: %s" % offenders

    def test_every_relative_link_to_an_adr_file_resolves(self):
        # The other half: a link that names a real file, spelled wrong. Resolved
        # against the linking file's own directory, the way a reader's client does.
        broken = []
        for path in _scanned_files():
            if path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(((?:adr/)?\d{3}-[\w.-]+\.md)\)", text):
                if not (path.parent / target).is_file():
                    broken.append("%s → %s" % (_rel(path), target))

        assert broken == [], "links to ADR files that do not exist: %s" % broken


class TestTheAdrsAreWellFormed:
    def test_the_pillar_is_populated(self):
        assert _adrs(), "adr/ is empty; the decisions this plugin makes are unrecorded"

    @pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.name)
    def test_the_filename_follows_the_convention(self, path):
        assert _ADR_FILENAME.match(path.name), (
            "%s must be NNN-lowercase-hyphenated-title.md with no 'ADR-' prefix on "
            "the filename" % path.name
        )

    @pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.name)
    def test_the_title_number_matches_the_filename(self, path):
        # The mistake that happens when several ADRs are written in one sitting:
        # a copied header keeps the number of the file it was copied from, and
        # every citation of one of the two numbers then points at the wrong text.
        first = path.read_text(encoding="utf-8").splitlines()[0]
        match = _ADR_TITLE.match(first)
        assert match, "%s: first line must be '# ADR-NNN — Title', got %r" % (
            path.name,
            first,
        )
        assert match.group(1) == path.name[:3], "%s is titled ADR-%s" % (
            path.name,
            match.group(1),
        )

    @pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.name)
    def test_the_metadata_block_states_status_and_date(self, path):
        # An undated decision cannot be read against the version of Claude Code it
        # was made about, which is most of what makes these worth keeping.
        text = path.read_text(encoding="utf-8")
        for key in ("- **Status:**", "- **Date:**"):
            assert key in text, "%s has no %s line" % (path.name, key)

    @pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.name)
    def test_the_three_sections_are_present_and_in_order(self, path):
        text = path.read_text(encoding="utf-8")
        positions = []
        for section in ADR_SECTIONS:
            assert ("\n%s\n" % section) in text, "%s has no %r section" % (
                path.name,
                section,
            )
            positions.append(text.index("\n%s\n" % section))
        assert positions == sorted(positions), (
            "%s: sections out of order — context, then decision, then what it "
            "costs" % path.name
        )

    @pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.name)
    def test_the_consequences_section_is_not_empty(self, path):
        # An ADR with no stated consequences records a preference, not a decision.
        text = path.read_text(encoding="utf-8")
        tail = text[text.index("\n## Consequences\n") + len("\n## Consequences\n") :]
        assert len(tail.strip()) >= 80, "%s states no consequences" % path.name


class TestTheNumbering:
    def test_no_two_adrs_share_a_number(self):
        names = [p.name for p in _adrs()]
        assert len(_numbers()) == len(names), "duplicate ADR numbers in %s" % names

    def test_every_gap_in_the_sequence_is_a_reserved_number(self):
        present = _numbers()
        gaps = set(range(1, max(present) + 1)) - present

        assert gaps <= set(RESERVED), (
            "ADR numbers %s are skipped and not reserved — either write them or "
            "add them to RESERVED with the title they are held for"
            % sorted(gaps - set(RESERVED))
        )

    def test_no_reserved_number_has_already_been_written(self):
        # The other direction, so a reservation cannot outlive the file it reserved.
        taken = sorted(_numbers() & set(RESERVED))
        assert taken == [], (
            "ADR %s exists but is still listed as RESERVED; delete the reservation"
            % taken
        )


class TestTheAnalysisCompanions:
    @pytest.mark.parametrize(
        "path", sorted((ADR_DIR / "analysis").glob("*.md")), ids=lambda p: p.name
    )
    def test_every_analysis_belongs_to_an_adr_that_exists(self, path):
        # One-directional on purpose. An ADR with no companion is legal; an
        # analysis with no ADR is reasoning for a decision nobody can find.
        assert (ADR_DIR / path.name).is_file(), (
            "adr/analysis/%s is an orphan: there is no adr/%s" % (path.name, path.name)
        )


# ── Pillar 0: the manifest, and whether it still describes this plugin ───────


@pytest.fixture(scope="module")
def manifest():
    yaml = pytest.importorskip(
        "yaml",
        reason="PyYAML is a test-only dependency; hook code stays stdlib-only",
    )
    assert MANIFEST.is_file(), "spec.manifest.yaml belongs at the repo root"
    with MANIFEST.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def hook_timeouts():
    with (REPO_ROOT / "hooks" / "hooks.json").open(encoding="utf-8") as fh:
        wiring = json.load(fh)["hooks"]
    return {
        event: groups[0]["hooks"][0]["timeout"] for event, groups in wiring.items()
    }


def _interfaces(manifest):
    for direction in ("inbound", "outbound"):
        for iface in manifest["interfaces"].get(direction) or []:
            yield direction, iface


class TestTheManifestIsAnEaGraphNode:
    @pytest.mark.parametrize(
        "key",
        (
            "name",
            "description",
            "type",
            "ea_layer",
            "owner",
            "status",
            "business_capability",
            "artefacts",
            "interfaces",
            "dependencies",
        ),
    )
    def test_the_node_field_is_declared_and_non_empty(self, manifest, key):
        assert manifest.get(key), "spec.manifest.yaml has no %r" % key

    def test_the_name_matches_the_plugin(self, manifest):
        with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
            assert manifest["name"] == json.load(fh)["name"]

    def test_every_artefact_path_is_a_directory(self, manifest):
        # British `artefacts` is deliberate; this test binds to the key by name, so
        # the two spellings cannot drift apart silently.
        for pillar, rel in manifest["artefacts"].items():
            assert (REPO_ROOT / rel).is_dir(), "artefacts.%s points at %s" % (
                pillar,
                rel,
            )

    def test_the_empty_pillars_are_exactly_the_ones_this_version_declares_empty(
        self, manifest
    ):
        # Two-sided, because both directions are real mistakes. Emptying `contracts/`
        # must fail; and populating `features/` must also fail, so that the
        # allowance is deleted rather than quietly outliving the gap it excused.
        #
        # `features/`, `contexts/` and `adr/analysis/` are empty at 0.1.0: the
        # feature space and the canvas arrive with episodes in 0.2.0, and the
        # analysis companions with the first decision weighed rather than recorded.
        # Dotfiles do not count as artefacts. Each empty pillar holds a tracked
        # `.gitkeep`, because git does not track empty directories and the test
        # above requires the directory to exist — so without the placeholder the
        # pillar would be present here and absent from a fresh clone, which is the
        # one environment where nobody is watching.
        empty = {
            pillar
            for pillar, rel in manifest["artefacts"].items()
            if not any(
                child
                for child in (REPO_ROOT / rel).iterdir()
                if not child.name.startswith(".")
            )
        }
        assert empty == {"features", "contexts", "adr_analyses"}, (
            "the set of empty pillars moved: %s" % sorted(empty)
        )

    def test_every_dependency_cites_a_contract_that_exists(self, manifest):
        for dep in manifest["dependencies"]:
            assert dep.get("name"), "a dependency entry has no name"
            target = REPO_ROOT / dep["contract"]
            assert target.is_file(), "dependency %r cites %s, which does not exist" % (
                dep["name"],
                dep["contract"],
            )


class TestTheManifestDoesNotDriftFromTheCode:
    def test_every_script_an_interface_names_exists(self, manifest):
        # A manifest naming a renamed script is worse than one naming none: it
        # reads as verified.
        named = 0
        for direction, iface in _interfaces(manifest):
            for script in re.findall(r"hooks/scripts/(\w+\.py)", iface["schema"]):
                assert (
                    REPO_ROOT / "hooks" / "scripts" / script
                ).is_file(), "%s (%s) names %s, which does not exist" % (
                    iface["name"],
                    direction,
                    script,
                )
                named += 1
        assert named, "no interface names a hook script"

    def test_every_contract_an_interface_cites_exists(self, manifest):
        cited = set()
        for _direction, iface in _interfaces(manifest):
            cited.update(re.findall(r"contracts/\w+/[\w.-]+\.md", iface["schema"]))

        assert cited, "no interface cites a contract file"
        for rel in sorted(cited):
            assert (REPO_ROOT / rel).is_file(), "manifest cites %s" % rel

    def test_every_wired_hook_event_has_exactly_one_inbound_interface(
        self, manifest, hook_timeouts
    ):
        # Bound to `hooks.json` rather than to a list retyped here, so wiring an
        # event without declaring it fails instead of passing unnoticed.
        declared = [i["event"] for i in manifest["interfaces"]["inbound"]]

        assert sorted(declared) == sorted(hook_timeouts), (
            "hooks.json wires %s; the manifest declares %s"
            % (sorted(hook_timeouts), sorted(declared))
        )

    def test_every_inbound_sla_mirrors_its_hook_timeout(self, manifest, hook_timeouts):
        # A timeout is a promise about worst-case latency and the SLA must repeat
        # it. Nothing but this keeps the two numbers together.
        for iface in manifest["interfaces"]["inbound"]:
            expected = hook_timeouts[iface["event"]] * 1000
            assert iface["sla"]["latency_p99_ms"] == expected, (
                "%s declares latency_p99_ms=%s; hooks.json budgets %s at %ss"
                % (
                    iface["name"],
                    iface["sla"]["latency_p99_ms"],
                    iface["event"],
                    hook_timeouts[iface["event"]],
                )
            )

    def test_no_interface_claims_an_llm_call(self, manifest):
        # ADR-011 in the one place a future maintainer would add one: an outbound
        # interface. The suite asserts there is no HTTP client in the code; this
        # asserts nobody declared the intention either.
        for direction, iface in _interfaces(manifest):
            assert iface["protocol"] in ("event", "cli", "subprocess", "file"), (
                "%s (%s) declares protocol %r — an outbound network protocol here "
                "would contradict ADR-011" % (iface["name"], direction, iface["protocol"])
            )


# ── The feature pillar: empty at 0.1.0, bound the moment it is not ───────────


def _feature_files():
    return sorted((REPO_ROOT / "features").glob("*.feature"))


class TestTheFeatureSpace:
    @pytest.mark.parametrize("path", _feature_files(), ids=lambda p: p.name)
    def test_the_feature_parses_and_declares_scenarios(self, path):
        # The real parser, not a regex: a file this cannot read is a file the suite
        # cannot execute, so a parse failure here is the collection failure found
        # early. Parametrised over the directory, so the first feature file written
        # inherits this without anyone remembering to come back.
        parser = pytest.importorskip("pytest_bdd.parser")
        feature = parser.FeatureParser("features", path.name).parse()

        assert feature.scenarios, "%s parses but declares no scenarios" % path.name


# ── Release coherence ───────────────────────────────────────────────────────


class TestTheVersionIsOneNumber:
    def test_the_plugin_and_marketplace_manifests_agree(self):
        with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
            plugin = json.load(fh)
        with (REPO_ROOT / ".claude-plugin" / "marketplace.json").open(
            encoding="utf-8"
        ) as fh:
            market = json.load(fh)

        entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
        assert entry["version"] == plugin["version"], (
            "plugin.json says %s, marketplace.json advertises %s"
            % (plugin["version"], entry["version"])
        )

    def test_the_changelog_documents_the_current_version(self):
        with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
            version = json.load(fh)["version"]

        assert "[%s]" % version in (REPO_ROOT / "CHANGELOG.md").read_text(
            encoding="utf-8"
        ), "CHANGELOG.md has no entry for %s" % version

    def test_the_readme_documents_the_install_a_stranger_will_type(self):
        # Derived from the manifests rather than retyped, so a rename cannot orphan
        # the two commands a new user runs first — both of which would fail with
        # nothing in the suite to notice.
        with (REPO_ROOT / ".claude-plugin" / "marketplace.json").open(
            encoding="utf-8"
        ) as fh:
            market = json.load(fh)
        with (REPO_ROOT / ".claude-plugin" / "plugin.json").open(encoding="utf-8") as fh:
            plugin = json.load(fh)

        shorthand = plugin["repository"].replace("https://github.com/", "")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for line in (
            "/plugin marketplace add %s" % shorthand,
            "/plugin install %s@%s" % (plugin["name"], market["name"]),
        ):
            assert line in readme, "README.md does not document `%s`" % line


class TestLayoutHygiene:
    def test_only_the_permitted_loose_markdown_exists_at_the_root(self):
        # Unchecked, top-level markdown is where undated, unowned design notes
        # accumulate — the thing the four pillars exist to prevent.
        permitted = {"README.md", "CLAUDE.md", "CHANGELOG.md", "CONTRIBUTING.md"}
        loose = {p.name for p in REPO_ROOT.glob("*.md")}

        assert loose <= permitted, "put %s in adr/ or contracts/" % sorted(
            loose - permitted
        )

    def test_no_directory_is_named_mcp(self):
        assert not (REPO_ROOT / "mcp").exists(), "mcp/ shadows the installed SDK package"

    def test_the_ci_workflow_installs_every_test_dependency(self):
        # This module reads the manifest with PyYAML. If CI does not install it,
        # the manifest fixture skips and every assertion above it vanishes from
        # the only run that gates a stranger's pull request — silently, and
        # reported as a pass.
        workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        for dependency in ("pytest", "pytest-bdd", "coverage", "pyyaml"):
            assert dependency in workflow, "CI does not install %s" % dependency
