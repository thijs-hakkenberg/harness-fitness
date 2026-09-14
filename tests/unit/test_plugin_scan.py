"""What each enabled plugin actually contributes to the harness, read from disk.

`installed_plugins.json` says what is installed; the plugin's own directory says
what it contains. Both are needed, and the directory is the authority: a manifest
can claim a skill that is not there, and a live-edited plugin's manifest does not
change when its contents do.

Four behaviours here are load-bearing and each has a test that fails loudly:

- **`gitCommitSha` is identity; `installedAt` and `lastUpdated` are not.** A
  timestamp in the digest makes every reinstall look like a new harness, which
  would split one composition's episodes into two populations too small to
  compare.
- **A plugin with no sha falls back to a content digest**, so live-editing a
  local plugin *does* move the composition. Without this the whole measurement
  is blind to exactly the plugin its author is iterating on — including this one.
- **The content digest hashes content, not mtimes** ([ADR-001](../../adr/)). A
  fresh clone or a `touch` changes an mtime with no change to the harness; a
  digest that moved on either would manufacture composition changes.
- **No absolute path reaches a record.** Install paths are under the user's home
  and hook commands can carry an inline credential, so the reduction happens
  before anything is returned.

Enabled-ness is not decided here. `plugin_scan` is told which keys are on and
reports on those; a plugin sitting on disk but switched off is not part of the
harness and must not appear.
"""

import json
import os

import pytest

import plugin_scan


# A credential-shaped value planted in a plugin's hook command, assembled from
# parts so this file carries no scannable literal (see tests/unit/test_security.py).
PLANTED = "dapi" + "0123456789abcdef" * 2

GITHUB_MARKET = {
    "abacus": {
        "source": {"source": "github", "repo": "thijs-hakkenberg/abacus"},
        "installLocation": "/Users/someone/.claude/plugins/marketplaces/abacus",
        "lastUpdated": "2026-05-29T19:18:59.682Z",
    }
}


def _hooks_block(command="${CLAUDE_PLUGIN_ROOT}/hooks/scripts/gate.py", **kw):
    entry = {"type": "command", "command": command}
    entry.update(kw)
    return {"PreToolUse": [{"matcher": "Edit|Write", "hooks": [entry]}]}


class TestReadInstalled:
    def test_reads_the_four_fields_that_identify_an_install(self, settings_tree):
        tree = settings_tree(
            plugins={
                "abacus@abacus": {
                    "version": "0.3.1",
                    "gitCommitSha": "abc123def4567890",
                    "scope": "user",
                }
            }
        )

        installed, unreadable = plugin_scan.read_installed(tree.home)

        entry = installed["abacus@abacus"]
        assert entry["version"] == "0.3.1"
        assert entry["gitCommitSha"] == "abc123def4567890"
        assert entry["scope"] == "user"
        assert entry["installPath"] == str(tree.plugin_dirs["abacus@abacus"])
        assert unreadable is None

    def test_an_absent_file_is_empty_and_not_an_error(self, settings_tree):
        # No plugins installed is a real state, not a fault.
        tree = settings_tree()

        installed, unreadable = plugin_scan.read_installed(tree.home)

        assert installed == {}
        assert unreadable is None

    def test_a_corrupt_file_is_reported_not_raised(self, settings_tree):
        tree = settings_tree(plugins={"a@m": {}})
        (tree.home / ".claude" / "plugins" / "installed_plugins.json").write_text("{oops")

        installed, unreadable = plugin_scan.read_installed(tree.home)

        assert installed == {}
        assert unreadable == "unparseable"

    def test_a_wrong_shaped_file_is_reported_not_raised(self, settings_tree):
        tree = settings_tree(plugins={"a@m": {}})
        (tree.home / ".claude" / "plugins" / "installed_plugins.json").write_text("[]")

        installed, unreadable = plugin_scan.read_installed(tree.home)

        assert installed == {}
        assert unreadable == "not_an_object"

    def test_a_non_object_entry_is_skipped_not_raised(self, settings_tree):
        tree = settings_tree(plugins={"a@m": {}})
        (tree.home / ".claude" / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"plugins": {"a@m": "nonsense", "b@m": {"version": "1"}}})
        )

        installed, _unreadable = plugin_scan.read_installed(tree.home)

        assert list(installed) == ["b@m"]


class TestReadMarketplaces:
    def test_reads_the_source_kind_and_repo(self, settings_tree):
        tree = settings_tree(marketplaces=GITHUB_MARKET)

        got = plugin_scan.read_marketplaces(tree.home)

        assert got["abacus"]["source_type"] == "github"
        assert got["abacus"]["repo"] == "thijs-hakkenberg/abacus"

    def test_a_directory_marketplace_is_flagged_as_live_edited(self, settings_tree):
        # `"source": "directory"` means the plugin is a working copy, so its
        # contents can change without any version or sha moving. Everything
        # downstream needs to know that before it trusts a version number.
        tree = settings_tree(
            marketplaces={
                "local": {"source": {"source": "directory", "path": "/Users/x/dev/p"}}
            }
        )

        got = plugin_scan.read_marketplaces(tree.home)

        assert got["local"]["source_type"] == "directory"
        assert got["local"]["live_edited"] is True

    def test_timestamps_and_install_locations_are_dropped(self, settings_tree):
        # `lastUpdated` would make the digest move on every marketplace refresh;
        # `installLocation` is an absolute path under the user's home.
        tree = settings_tree(marketplaces=GITHUB_MARKET)

        got = plugin_scan.read_marketplaces(tree.home)

        blob = json.dumps(got)
        assert "lastUpdated" not in blob
        assert "2026-05-29" not in blob
        assert "/Users/someone" not in blob

    def test_settings_declared_extras_are_merged_in(self, settings_tree):
        # Source 5: `extraKnownMarketplaces` in settings is as real as the file.
        tree = settings_tree(marketplaces=GITHUB_MARKET)

        got = plugin_scan.read_marketplaces(
            tree.home,
            extra={"extra-mkt": {"source": {"source": "git", "url": "https://e.invalid/r"}}},
        )

        assert got["abacus"]["source_type"] == "github"
        assert got["extra-mkt"]["source_type"] == "git"

    def test_an_absent_or_corrupt_file_yields_an_empty_map(self, settings_tree):
        tree = settings_tree()
        assert plugin_scan.read_marketplaces(tree.home) == {}

        (tree.home / ".claude" / "plugins").mkdir(parents=True, exist_ok=True)
        (tree.home / ".claude" / "plugins" / "known_marketplaces.json").write_text("{")
        assert plugin_scan.read_marketplaces(tree.home) == {}

    def test_an_unrecognisable_source_is_unknown_not_guessed(self, settings_tree):
        tree = settings_tree(marketplaces={"weird": {"nope": 1}})

        got = plugin_scan.read_marketplaces(tree.home)

        assert got["weird"]["source_type"] == "unknown"
        assert got["weird"]["live_edited"] is False


class TestContentDigest:
    def _tree(self, settings_tree, **spec):
        base = {"version": "1.0.0", "skills": ["audit"], "agents": ["judge"]}
        base.update(spec)
        tree = settings_tree(plugins={"local@dev": base})
        return tree, tree.plugin_dirs["local@dev"]

    def test_is_deterministic_for_an_unchanged_directory(self, settings_tree):
        _tree, pdir = self._tree(settings_tree, hooks=_hooks_block())

        assert plugin_scan.content_digest(pdir) == plugin_scan.content_digest(pdir)

    def test_is_twelve_hex_characters(self, settings_tree):
        _tree, pdir = self._tree(settings_tree)

        got = plugin_scan.content_digest(pdir)

        assert len(got) == 12
        assert all(c in "0123456789abcdef" for c in got)

    def test_moves_when_a_skill_body_changes(self, settings_tree):
        # The reason this function exists: a live-edited plugin whose version
        # never moves is invisible to a version-only digest.
        _tree, pdir = self._tree(settings_tree)
        before = plugin_scan.content_digest(pdir)

        (pdir / "skills" / "audit" / "SKILL.md").write_text("---\nname: audit\n---\nNEW\n")

        assert plugin_scan.content_digest(pdir) != before

    def test_moves_when_a_hook_is_added(self, settings_tree):
        _tree, pdir = self._tree(settings_tree)
        before = plugin_scan.content_digest(pdir)

        (pdir / "hooks").mkdir(exist_ok=True)
        (pdir / "hooks" / "hooks.json").write_text(json.dumps({"hooks": _hooks_block()}))

        assert plugin_scan.content_digest(pdir) != before

    def test_moves_when_an_agent_is_added(self, settings_tree):
        _tree, pdir = self._tree(settings_tree)
        before = plugin_scan.content_digest(pdir)

        (pdir / "agents" / "second.md").write_text("---\nname: second\n---\nbody\n")

        assert plugin_scan.content_digest(pdir) != before

    def test_moves_when_a_referenced_hook_script_changes(self, settings_tree):
        # A directory plugin under development is edited in its *scripts*, not
        # its declarations. A digest covering only declarations would sit still
        # across every behavioural change to the plugin being iterated on.
        _tree, pdir = self._tree(settings_tree, hooks=_hooks_block())
        script = pdir / "hooks" / "scripts" / "gate.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("print('v1')\n")
        before = plugin_scan.content_digest(pdir)

        script.write_text("print('v2')\n")

        assert plugin_scan.content_digest(pdir) != before

    def test_does_not_move_when_only_an_mtime_changes(self, settings_tree):
        # ADR-001. A fresh clone rewrites every mtime and changes no behaviour.
        # An mtime-based digest would report a composition change on checkout,
        # splitting one composition's episodes across two digests.
        _tree, pdir = self._tree(settings_tree, hooks=_hooks_block())
        before = plugin_scan.content_digest(pdir)

        target = pdir / "skills" / "audit" / "SKILL.md"
        os.utime(str(target), (1000000000, 1000000000))

        assert plugin_scan.content_digest(pdir) == before

    def test_a_command_pointing_outside_the_plugin_is_not_read(self, settings_tree):
        # Only files under the plugin dir are hashed. A command naming
        # `/etc/passwd` or a 2 GB binary must not be opened.
        _tree, pdir = self._tree(settings_tree, hooks=_hooks_block(command="/etc/passwd"))

        assert len(plugin_scan.content_digest(pdir)) == 12

    def test_a_directory_that_does_not_exist_yields_none(self, tmp_path):
        assert plugin_scan.content_digest(tmp_path / "gone") is None

    def test_an_unreadable_file_does_not_raise(self, settings_tree):
        _tree, pdir = self._tree(settings_tree)
        target = pdir / "skills" / "audit" / "SKILL.md"
        os.chmod(str(target), 0o000)
        try:
            assert len(plugin_scan.content_digest(pdir)) == 12
        finally:
            os.chmod(str(target), 0o600)


class TestScanPlugin:
    def test_reports_the_components_found_on_disk(self, settings_tree):
        tree = settings_tree(
            plugins={
                "abacus@abacus": {
                    "version": "0.3.1",
                    "gitCommitSha": "abc123",
                    "hooks": _hooks_block(),
                    "skills": ["audit", "claim"],
                    "agents": ["auditor"],
                    "mcp": {"mcpServers": {"beads": {"command": "bd"}}},
                }
            }
        )

        got = plugin_scan.scan(tree.home, ("abacus@abacus",))
        rec = got["plugins"][0]

        assert rec["key"] == "abacus@abacus"
        assert rec["name"] == "abacus"
        assert rec["marketplace"] == "abacus"
        assert rec["version"] == "0.3.1"
        assert rec["skills"] == ("audit", "claim")
        assert rec["agents"] == ("auditor",)
        assert rec["mcp_servers"] == ("beads",)
        assert len(rec["hooks"]) == 1
        assert rec["hooks"][0]["event"] == "PreToolUse"
        assert rec["hooks"][0]["matcher"] == "Edit|Write"
        assert rec["hooks"][0]["owner"] == "abacus@abacus"

    def test_components_are_sorted_so_the_digest_is_order_independent(
        self, settings_tree
    ):
        # Directory iteration order is not guaranteed across filesystems, and an
        # unsorted list would give the same harness two digests on two machines.
        tree = settings_tree(
            plugins={"p@m": {"skills": ["z", "a", "m"], "agents": ["z", "a"]}}
        )

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["skills"] == ("a", "m", "z")
        assert rec["agents"] == ("a", "z")

    def test_a_git_sha_is_the_identity_basis(self, settings_tree):
        tree = settings_tree(
            plugins={"p@m": {"version": "1.0.0", "gitCommitSha": "deadbeefcafe1234"}}
        )

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["sha_basis"] == "git"
        assert rec["sha"] == "deadbeefcafe1234"

    def test_without_a_sha_the_basis_is_the_directory_contents(self, settings_tree):
        tree = settings_tree(plugins={"p@m": {"version": "1.0.0", "skills": ["s"]}})

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["sha_basis"] == "content"
        assert rec["sha"] == plugin_scan.content_digest(tree.plugin_dirs["p@m"])

    def test_editing_a_shaless_plugin_moves_its_sha(self, settings_tree):
        # The end-to-end version of the content-digest promise, at the level the
        # composition record actually consumes.
        tree = settings_tree(plugins={"p@m": {"version": "1.0.0", "skills": ["s"]}})
        before = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]["sha"]

        (tree.plugin_dirs["p@m"] / "skills" / "s" / "SKILL.md").write_text("changed\n")

        after = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]["sha"]
        assert after != before

    def test_an_install_whose_directory_is_gone_falls_back_to_the_version(
        self, settings_tree
    ):
        # Enabled and registered but not on disk. It is still declared harness,
        # so dropping it would under-report; but nothing about its contents is
        # knowable.
        tree = settings_tree(plugins={"p@m": {"version": "2.0.0"}})
        import shutil

        shutil.rmtree(str(tree.plugin_dirs["p@m"]))

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["on_disk"] is False
        assert rec["sha_basis"] == "version-only"
        assert rec["sha"] is None
        assert rec["version"] == "2.0.0"

    def test_installed_at_and_last_updated_never_reach_the_record(self, settings_tree):
        # The rule that keeps a reinstall from reading as a new harness.
        tree = settings_tree(
            plugins={
                "p@m": {
                    "version": "1.0.0",
                    "gitCommitSha": "abc",
                    "installedAt": "2026-01-01T00:00:00Z",
                    "lastUpdated": "2026-09-14T00:00:00Z",
                }
            }
        )

        blob = json.dumps(plugin_scan.scan(tree.home, ("p@m",)), default=str)

        assert "installedAt" not in blob
        assert "lastUpdated" not in blob
        assert "2026-01-01" not in blob
        assert "2026-09-14" not in blob

    def test_marketplace_provenance_is_attached(self, settings_tree):
        tree = settings_tree(
            plugins={"abacus@abacus": {"version": "1.0.0", "gitCommitSha": "a"}},
            marketplaces=GITHUB_MARKET,
        )

        rec = plugin_scan.scan(tree.home, ("abacus@abacus",))["plugins"][0]

        assert rec["marketplace_source"] == "github"
        assert rec["live_edited"] is False

    def test_a_directory_marketplace_marks_the_plugin_live_edited(self, settings_tree):
        tree = settings_tree(
            plugins={"p@dev": {"version": "1.0.0", "gitCommitSha": "a"}},
            marketplaces={"dev": {"source": {"source": "directory", "path": "/x"}}},
        )

        rec = plugin_scan.scan(tree.home, ("p@dev",))["plugins"][0]

        assert rec["live_edited"] is True

    def test_an_unknown_marketplace_is_unknown_not_absent(self, settings_tree):
        tree = settings_tree(plugins={"p@nowhere": {"version": "1.0.0"}})

        rec = plugin_scan.scan(tree.home, ("p@nowhere",))["plugins"][0]

        assert rec["marketplace"] == "nowhere"
        assert rec["marketplace_source"] == "unknown"

    def test_a_malformed_plugin_manifest_does_not_lose_the_plugin(self, settings_tree):
        tree = settings_tree(plugins={"p@m": {"version": "1.0.0"}})
        (tree.plugin_dirs["p@m"] / ".claude-plugin" / "plugin.json").write_text("{bad")

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["key"] == "p@m"
        assert rec["on_disk"] is True

    def test_a_malformed_hooks_json_does_not_lose_the_plugin(self, settings_tree):
        tree = settings_tree(plugins={"p@m": {"hooks": _hooks_block()}})
        (tree.plugin_dirs["p@m"] / "hooks" / "hooks.json").write_text("]")

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["hooks"] == ()
        assert rec["key"] == "p@m"

    def test_a_malformed_mcp_json_yields_no_servers(self, settings_tree):
        tree = settings_tree(plugins={"p@m": {"mcp": {"mcpServers": {"a": {}}}}})
        (tree.plugin_dirs["p@m"] / ".mcp.json").write_text("nope")

        rec = plugin_scan.scan(tree.home, ("p@m",))["plugins"][0]

        assert rec["mcp_servers"] == ()

    def test_a_key_with_no_marketplace_suffix_is_handled(self, settings_tree):
        tree = settings_tree(plugins={"bare": {"version": "1.0.0"}})

        rec = plugin_scan.scan(tree.home, ("bare",))["plugins"][0]

        assert rec["name"] == "bare"
        assert rec["marketplace"] is None


class TestScanSelection:
    def test_a_plugin_on_disk_but_not_enabled_is_excluded(self, settings_tree):
        # `enabledPlugins` is the on/off truth. Inventorying an installed-but-off
        # plugin would attribute every measure to a harness that never loaded.
        tree = settings_tree(
            plugins={"on@m": {"version": "1"}, "off@m": {"version": "1"}}
        )

        got = plugin_scan.scan(tree.home, ("on@m",))

        assert [p["key"] for p in got["plugins"]] == ["on@m"]

    def test_an_enabled_key_with_no_install_record_is_unresolved(self, settings_tree):
        # Nothing is knowable about it beyond its key, and inventing a version
        # would be worse than saying so.
        tree = settings_tree(plugins={"real@m": {"version": "1"}})

        got = plugin_scan.scan(tree.home, ("real@m", "ghost@m"))

        assert [p["key"] for p in got["plugins"]] == ["real@m"]
        assert got["unresolved"] == ("ghost@m",)

    def test_plugins_are_returned_in_key_order(self, settings_tree):
        tree = settings_tree(
            plugins={"z@m": {"version": "1"}, "a@m": {"version": "1"}, "m@m": {"version": "1"}}
        )

        got = plugin_scan.scan(tree.home, ("z@m", "a@m", "m@m"))

        assert [p["key"] for p in got["plugins"]] == ["a@m", "m@m", "z@m"]

    def test_an_unreadable_install_file_makes_every_key_unresolved(self, settings_tree):
        # Fail-open, and honest about it: the keys are still reported as enabled
        # composition we could not resolve, plus the reason.
        tree = settings_tree(plugins={"p@m": {"version": "1"}})
        (tree.home / ".claude" / "plugins" / "installed_plugins.json").write_text("{x")

        got = plugin_scan.scan(tree.home, ("p@m",))

        assert got["plugins"] == ()
        assert got["unresolved"] == ("p@m",)
        assert got["installed_unreadable"] == "unparseable"

    def test_no_enabled_plugins_is_an_empty_scan_not_an_error(self, settings_tree):
        tree = settings_tree()

        got = plugin_scan.scan(tree.home, ())

        assert got["plugins"] == ()
        assert got["unresolved"] == ()


class TestUserComponents:
    def test_reads_user_level_skills_and_agents(self, settings_tree):
        # Source 8. These belong to no plugin and no plugin-only inventory sees
        # them, but Claude Code loads them all the same.
        tree = settings_tree()
        claude = tree.home / ".claude"
        (claude / "skills" / "my-skill").mkdir(parents=True)
        (claude / "skills" / "my-skill" / "SKILL.md").write_text("---\nname: x\n---\n")
        (claude / "agents").mkdir(parents=True)
        (claude / "agents" / "my-agent.md").write_text("---\nname: y\n---\n")

        got = plugin_scan.user_components(tree.home)

        assert got["skills"] == ("my-skill",)
        assert got["agents"] == ("my-agent",)

    def test_absent_directories_yield_empty_tuples(self, settings_tree):
        tree = settings_tree()

        got = plugin_scan.user_components(tree.home)

        assert got == {"skills": (), "agents": ()}

    def test_a_skill_directory_without_a_manifest_is_not_a_skill(self, settings_tree):
        # A stray directory under `skills/` is not a loaded component; counting
        # it would inflate the profile.
        tree = settings_tree()
        (tree.home / ".claude" / "skills" / "notaskill").mkdir(parents=True)

        assert plugin_scan.user_components(tree.home)["skills"] == ()

    def test_results_are_sorted(self, settings_tree):
        tree = settings_tree()
        skills = tree.home / ".claude" / "skills"
        for name in ("z", "a", "m"):
            (skills / name).mkdir(parents=True)
            (skills / name / "SKILL.md").write_text("---\nname: %s\n---\n" % name)

        assert plugin_scan.user_components(tree.home)["skills"] == ("a", "m", "z")


class TestNoLeaks:
    def test_no_absolute_path_reaches_a_scan_record(self, settings_tree):
        # Install paths live under the user's home. A composition record is a
        # file the user may share when reporting a delta, so the home path is
        # reduced away rather than trusted to be uninteresting.
        tree = settings_tree(
            plugins={"p@m": {"version": "1.0.0", "hooks": _hooks_block()}},
            marketplaces=GITHUB_MARKET,
        )

        blob = json.dumps(plugin_scan.scan(tree.home, ("p@m",)), default=str)

        assert str(tree.home) not in blob
        assert "installPath" not in blob

    def test_a_planted_credential_in_a_plugin_hook_command_never_appears(
        self, settings_tree
    ):
        # The same guarantee `settings_read` gives for settings-level hooks. A
        # plugin's hooks.json is read from disk and is no more trustworthy.
        tree = settings_tree(
            plugins={
                "p@m": {
                    "version": "1.0.0",
                    "hooks": _hooks_block(
                        command="TOKEN=%s /Users/someone/.claude/x/gate.py" % PLANTED
                    ),
                }
            }
        )

        got = plugin_scan.scan(tree.home, ("p@m",))
        blob = json.dumps(got, default=str)

        assert PLANTED not in blob
        assert "/Users/someone" not in blob
        assert "command" not in got["plugins"][0]["hooks"][0]
        assert len(got["plugins"][0]["hooks"][0]["cmd_digest"]) == 12
