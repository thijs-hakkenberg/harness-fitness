"""The composition digest — one identity for "what the harness was made of".

This is the primitive the whole plugin rests on. Episodes are grouped by
`(composition_digest, env_hash)`, so the digest has to satisfy a two-sided
property: it moves when the harness moves, and it does *not* move when anything
else does.

Both failure directions cost something, and they cost different things:

- **A digest that misses a change** merges two different harnesses into one
  population. The measure is then computed across a mixture and reported as if it
  described one thing. That is a wrong number presented as a right one.
- **A digest that moves spuriously** splits one population of ten episodes into
  two of five, each below `min_episodes_per_digest`, and the measure comes back
  `insufficient_n`. That is not a wrong number, but it is an unavailable one for a
  reason no report reader could trace back to a `touch` or a permissions grant.

So the sensitivity and insensitivity tests below are equally load-bearing, and
each one names the change it is pinning.

`env_hash` is deliberately *not* part of the digest (ADR-007). Model, effort and
base URL decide whether two runs are comparable at all; harness composition
decides what is being compared. Folding them together would make a model swap
read as a harness change.
"""

import json
import shutil

import pytest

import composition
import taxonomy

# A credential-shaped literal, assembled from parts. The shape is what a secret
# scanner matches, not the provenance, and a public repo that trips its own
# scanner on every clone would permanently report a secret it does not have.
PLANTED_TOKEN = "dapi" + "0123456789abcdef" * 2
PLANTED_BEARER = "Bearer " + "sk-ant-" + "x" * 24


def digest_for(tree, **kw):
    return composition.build(tree.home, tree.project, **kw)["digest"]


ONE_PLUGIN = {
    "abacus@abacus": {
        "version": "0.3.1",
        "gitCommitSha": "aaaa111",
        "marketplace": "abacus",
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": "/x/audit.py"}],
                }
            ]
        },
        "skills": ["audit"],
        "agents": ["abacus-auditor"],
    }
}

ENABLED_ONE = {"enabledPlugins": {"abacus@abacus": True}}


class TestDigestShape:
    def test_the_digest_is_twelve_hex_characters(self, settings_tree):
        # Twelve, matching the rig's env_hash, so the two identities are the same
        # width in a report and neither reads as the more precise one. It is also
        # the filename of `compositions/<digest12>.json`, so the width is a
        # storage contract and not a display choice.
        got = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        assert composition.DIGEST_LEN == 12
        assert len(got) == 12
        assert all(c in "0123456789abcdef" for c in got)

    def test_a_digest_is_reproducible_for_the_same_tree(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        assert digest_for(tree) == digest_for(tree)

    def test_the_canonical_form_ignores_key_insertion_order(self):
        # Asserted on the canonicaliser directly, because a dict built in a
        # different order is exactly what two settings files with the same content
        # produce, and `json.dumps` preserves insertion order by default.
        a = {"plugins": [{"key": "x", "version": "1"}], "settings_hooks": []}
        b = {"settings_hooks": [], "plugins": [{"version": "1", "key": "x"}]}

        assert composition.digest(a) == composition.digest(b)

    def test_the_enabled_plugin_order_in_settings_does_not_matter(
        self, settings_tree
    ):
        plugins = dict(ONE_PLUGIN)
        plugins["other@abacus"] = {"version": "1.0.0", "gitCommitSha": "bbbb222"}

        first = digest_for(
            settings_tree(
                user={"enabledPlugins": {"abacus@abacus": True, "other@abacus": True}},
                plugins=plugins,
            )
        )
        second = digest_for(
            settings_tree(
                user={"enabledPlugins": {"other@abacus": True, "abacus@abacus": True}},
                plugins=plugins,
            )
        )

        assert first == second


class TestDigestMovesWhenTheHarnessDoes:
    def _variant(self, settings_tree, mutate, enabled=None):
        plugins = json.loads(json.dumps(ONE_PLUGIN))
        mutate(plugins)
        return digest_for(
            settings_tree(user=enabled or ENABLED_ONE, plugins=plugins)
        )

    def test_a_version_bump_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        def bump(p):
            p["abacus@abacus"]["version"] = "0.4.0"

        assert self._variant(settings_tree, bump) != base

    def test_a_commit_sha_change_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        def resha(p):
            p["abacus@abacus"]["gitCommitSha"] = "cccc333"

        assert self._variant(settings_tree, resha) != base

    def test_disabling_a_plugin_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        off = digest_for(
            settings_tree(
                user={"enabledPlugins": {"abacus@abacus": False}}, plugins=ONE_PLUGIN
            )
        )

        # The on/off truth is the whole point: a plugin on disk is not part of the
        # harness, and a plugin switched off must not carry its measures forward.
        assert off != base

    def test_a_new_hook_matcher_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        def widen(p):
            p["abacus@abacus"]["hooks"]["PostToolUse"].append(
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "/x/audit.py"}],
                }
            )

        # Same script, new reach. A sensor that starts watching Bash is a
        # different sensor.
        assert self._variant(settings_tree, widen) != base

    def test_a_changed_hook_command_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        def swap(p):
            p["abacus@abacus"]["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = (
                "/x/other.py"
            )

        # Only the digest of the command is stored (ADR-010), and it still has to
        # be enough to notice the swap.
        assert self._variant(settings_tree, swap) != base

    def test_an_added_skill_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        def add(p):
            p["abacus@abacus"]["skills"] = ["audit", "reconcile"]

        assert self._variant(settings_tree, add) != base

    def test_a_settings_level_hook_moves_it(self, settings_tree):
        # Source 7: hooks belonging to no plugin. Eleven of them on the machine
        # this was measured against, invisible to any plugin-only inventory.
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        with_hook = digest_for(
            settings_tree(
                user={
                    "enabledPlugins": {"abacus@abacus": True},
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Edit",
                                "hooks": [
                                    {"type": "command", "command": "~/bin/auto-test.sh"}
                                ],
                            }
                        ]
                    },
                },
                plugins=ONE_PLUGIN,
            )
        )

        assert with_hook != base

    def test_an_enabled_but_uninstalled_plugin_moves_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        ghost = digest_for(
            settings_tree(
                user={
                    "enabledPlugins": {"abacus@abacus": True, "ghost@nowhere": True}
                },
                plugins=ONE_PLUGIN,
            )
        )

        # Nothing is knowable about it beyond the key, but enabling it is still a
        # change to the harness, and it must not silently vanish from the identity.
        assert ghost != base

    def test_an_enabled_project_mcp_server_moves_it(self, settings_tree):
        # Source 6. A project's `.mcp.json` is substrate that belongs to no
        # plugin, so a plugin-only inventory would miss the whole file.
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        served = digest_for(
            settings_tree(
                user={
                    "enabledPlugins": {"abacus@abacus": True},
                    "enabledMcpjsonServers": ["ado"],
                },
                plugins=ONE_PLUGIN,
                mcp={"mcpServers": {"ado": {"command": "npx"}}},
            )
        )

        assert served != base


class TestDigestHoldsStillOtherwise:
    def test_an_install_timestamp_does_not_move_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        stamped = json.loads(json.dumps(ONE_PLUGIN))
        stamped["abacus@abacus"]["installedAt"] = "2026-01-01T00:00:00Z"
        stamped["abacus@abacus"]["lastUpdated"] = "2026-09-14T00:00:00Z"

        # A reinstall is not a new harness. If it were, every `/plugin update`
        # would split the population it is meant to be measured against.
        assert digest_for(settings_tree(user=ENABLED_ONE, plugins=stamped)) == base

    def test_an_installed_but_disabled_plugin_does_not_move_it(self, settings_tree):
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))

        extra = json.loads(json.dumps(ONE_PLUGIN))
        extra["dormant@abacus"] = {"version": "9.9.9", "gitCommitSha": "dddd444"}

        assert digest_for(settings_tree(user=ENABLED_ONE, plugins=extra)) == base

    def test_an_env_change_does_not_move_it(self, settings_tree):
        # ADR-007: model and effort belong to `env_hash`. A model swap that halves
        # tokens per outcome must not read as a harness win, and it cannot be told
        # apart from one if both live in the same digest.
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        swapped = digest_for(
            settings_tree(
                user={
                    "enabledPlugins": {"abacus@abacus": True},
                    "env": {"ANTHROPIC_MODEL": "something-else"},
                },
                plugins=ONE_PLUGIN,
            )
        )

        assert swapped == base

    def test_a_permission_grant_does_not_move_it(self, settings_tree):
        # `settings.local.json` accumulates per-machine grants constantly. None of
        # them changes what the harness is composed of, and a digest that moved on
        # each one would never accumulate a measurable population.
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        granted = digest_for(
            settings_tree(
                user=ENABLED_ONE,
                user_local={"permissions": {"allow": ["Bash(git status:*)"]}},
                plugins=ONE_PLUGIN,
            )
        )

        assert granted == base

    def test_a_declared_but_unenabled_project_mcp_server_does_not_move_it(
        self, settings_tree
    ):
        # `.mcp.json` is a file in the repository, and a server nobody enabled
        # never connects. Counting it would attribute measures to substrate that
        # was not part of the harness, and would move the digest for every
        # teammate who committed a server this user has not approved.
        base = digest_for(settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN))
        declared = digest_for(
            settings_tree(
                user=ENABLED_ONE,
                plugins=ONE_PLUGIN,
                mcp={"mcpServers": {"ado": {"command": "npx"}}},
            )
        )

        assert declared == base

    def test_the_env_hash_argument_does_not_move_it(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        assert digest_for(tree, env_hash="aaaaaaaaaaaa") == digest_for(
            tree, env_hash="bbbbbbbbbbbb"
        )

    def test_the_capture_time_does_not_move_it(self, settings_tree, frozen_now):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        frozen_now("2026-09-14T09:00:00Z")
        first = digest_for(tree)
        frozen_now("2026-09-15T18:30:00Z")

        assert digest_for(tree) == first


class TestRecord:
    def test_the_record_carries_a_schema_version(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        assert composition.build(tree.home, tree.project)["schema"] == composition.SCHEMA

    def test_the_record_is_json_serialisable(self, settings_tree):
        # It is written to `compositions/<digest12>.json` verbatim. A tuple is
        # fine; a set or a Path is not, and that would surface at write time in a
        # hook rather than here.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        json.dumps(composition.build(tree.home, tree.project))

    def test_the_env_hash_is_carried_beside_the_digest(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        record = composition.build(tree.home, tree.project, env_hash="abc123abc123")

        assert record["env_hash"] == "abc123abc123"

    def test_an_absent_env_hash_is_null_rather_than_a_placeholder(
        self, settings_tree
    ):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        # Not "unknown", not "": a consumer comparing two compositions must be
        # able to refuse on a missing env_hash, and only null is unambiguous.
        assert composition.build(tree.home, tree.project)["env_hash"] is None

    def test_the_profile_counts_every_component(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        record = composition.build(tree.home, tree.project)
        profile = record["profile"]

        assert profile["ff_inf"] == 1  # the skill
        assert profile["both_inf"] == 1  # the agent, direction undeterminable
        assert profile["fb_comp"] == 1  # the PostToolUse hook
        assert profile["packaging"] == 2  # the plugin manifest and its marketplace

    def test_the_profile_carries_every_bucket(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)

        assert set(composition.build(tree.home, tree.project)["profile"]) == set(
            taxonomy.BUCKETS
        )

    def test_a_pretooluse_hook_lands_in_the_feedforward_bucket(self, settings_tree):
        # ADR-005 reaching the profile, not just the classifier.
        gate = json.loads(json.dumps(ONE_PLUGIN))
        gate["abacus@abacus"]["hooks"] = {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": "/x/gate.py"}],
                }
            ]
        }
        tree = settings_tree(user=ENABLED_ONE, plugins=gate)

        profile = composition.build(tree.home, tree.project)["profile"]

        assert profile["ff_comp"] == 1
        assert profile["fb_comp"] == 0

    def test_mcp_servers_are_counted_as_substrate(self, settings_tree):
        served = json.loads(json.dumps(ONE_PLUGIN))
        served["abacus@abacus"]["mcp"] = {
            "mcpServers": {"ado": {"command": "npx"}, "snow": {"command": "npx"}}
        }
        tree = settings_tree(user=ENABLED_ONE, plugins=served)

        assert composition.build(tree.home, tree.project)["profile"]["substrate"] == 2

    def test_an_enabled_project_mcp_server_counts_as_substrate(self, settings_tree):
        tree = settings_tree(
            user={
                "enabledPlugins": {"abacus@abacus": True},
                "enabledMcpjsonServers": ["ado"],
            },
            plugins=ONE_PLUGIN,
            mcp={"mcpServers": {"ado": {"command": "npx"}, "snow": {"command": "npx"}}},
        )

        record = composition.build(tree.home, tree.project)

        # One of the two, because only one is switched on.
        assert record["profile"]["substrate"] == 1
        assert list(record["project_mcp_servers"]) == ["ado"]

    def test_the_mcpjson_wildcard_enables_every_declared_server(self, settings_tree):
        # `"*"` is a sentinel, not a server name. Resolving it against the file is
        # the only way to count it, and passing it through as a name would report
        # a server called `*`.
        tree = settings_tree(
            user={
                "enabledPlugins": {"abacus@abacus": True},
                "enabledMcpjsonServers": "*",
            },
            plugins=ONE_PLUGIN,
            mcp={"mcpServers": {"ado": {"command": "npx"}, "snow": {"command": "npx"}}},
        )

        record = composition.build(tree.home, tree.project)

        assert record["profile"]["substrate"] == 2
        assert list(record["project_mcp_servers"]) == ["ado", "snow"]

    def test_user_level_skills_and_agents_are_included(self, settings_tree):
        # Source 8. They belong to no plugin, and Claude Code loads them anyway.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        skill = tree.home / ".claude" / "skills" / "my-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nbody\n")

        record = composition.build(tree.home, tree.project)

        assert "my-skill" in record["user"]["skills"]
        assert record["profile"]["ff_inf"] == 2

    def test_an_unresolved_plugin_is_named_not_dropped(self, settings_tree):
        tree = settings_tree(
            user={"enabledPlugins": {"abacus@abacus": True, "ghost@nowhere": True}},
            plugins=ONE_PLUGIN,
        )

        record = composition.build(tree.home, tree.project)

        assert list(record["unresolved"]) == ["ghost@nowhere"]

    def test_an_unreadable_settings_layer_is_flagged(self, settings_tree):
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        (tree.home / ".claude" / "settings.local.json").write_text("{not json")

        record = composition.build(tree.home, tree.project)

        # The composition is still computed from the layers that did read — one
        # typo must degrade one measure, not end the session — but the report has
        # to be able to say the inventory was partial.
        assert any(
            flag["kind"] == "settings-unreadable" for flag in record["flags"]
        )

    def test_an_unreadable_install_record_is_flagged(self, settings_tree):
        # Every enabled plugin becomes unresolved when this file will not parse, so
        # the profile collapses to almost nothing. Without the flag, that reads as
        # a harness with no plugins rather than an inventory that failed.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        (tree.home / ".claude" / "plugins" / "installed_plugins.json").write_text(
            "{not json"
        )

        record = composition.build(tree.home, tree.project)

        assert any(flag["kind"] == "installed-unreadable" for flag in record["flags"])

    def test_a_live_edited_plugin_is_flagged(self, settings_tree):
        # A directory marketplace is a working copy: its contents move with no
        # version and no sha moving. Anything downstream needs that before it
        # trusts a version number.
        tree = settings_tree(
            user=ENABLED_ONE,
            plugins={"abacus@abacus": {"version": "0.3.1", "marketplace": "abacus"}},
            marketplaces={
                "abacus": {"source": {"source": "directory", "path": "/tmp/x"}}
            },
        )

        record = composition.build(tree.home, tree.project)

        assert any(flag["kind"] == "live-edited" for flag in record["flags"])

    def test_a_plugin_with_no_identity_basis_is_flagged(self, settings_tree):
        # `version-only`: no sha, and the directory could not be read either. The
        # digest cannot notice that plugin changing, so the report has to say the
        # identity is weak rather than let a stable digest imply a stable harness.
        tree = settings_tree(user=ENABLED_ONE, plugins={"abacus@abacus": {}})
        shutil.rmtree(tree.plugin_dirs["abacus@abacus"])

        record = composition.build(tree.home, tree.project)

        assert any(flag["kind"] == "weak-identity" for flag in record["flags"])

    def test_an_unresolved_plugin_is_flagged_as_well_as_listed(self, settings_tree):
        tree = settings_tree(
            user={"enabledPlugins": {"abacus@abacus": True, "ghost@nowhere": True}},
            plugins=ONE_PLUGIN,
        )

        record = composition.build(tree.home, tree.project)

        # `unresolved` is the data; the flag is what a report surfaces. Both,
        # because a reader of the report should not have to know to look.
        assert any(flag["kind"] == "plugin-unresolved" for flag in record["flags"])

    def test_a_clean_tree_has_no_flags(self, settings_tree):
        tree = settings_tree(
            user=ENABLED_ONE,
            plugins=ONE_PLUGIN,
            marketplaces={
                "abacus": {"source": {"source": "github", "repo": "owner/abacus"}}
            },
        )

        assert composition.build(tree.home, tree.project)["flags"] == []


class TestNoLeaks:
    @pytest.fixture
    def loaded(self, settings_tree):
        planted = json.loads(json.dumps(ONE_PLUGIN))
        planted["abacus@abacus"]["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = (
            "/Users/someone/.claude/plugins/abacus/gate.py --auth %s" % PLANTED_BEARER
        )
        planted["abacus@abacus"]["mcp"] = {
            "mcpServers": {
                "ado": {"command": "npx", "args": ["-y", "srv", "--token", PLANTED_TOKEN]}
            }
        }
        tree = settings_tree(
            user={
                "enabledPlugins": {"abacus@abacus": True},
                "env": {"ANTHROPIC_AUTH_TOKEN": PLANTED_TOKEN},
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/Users/someone/bin/x.sh --key %s"
                                    % PLANTED_TOKEN,
                                }
                            ]
                        }
                    ]
                },
            },
            plugins=planted,
        )
        return json.dumps(composition.build(tree.home, tree.project))

    def test_no_planted_credential_reaches_the_record(self, loaded):
        assert PLANTED_TOKEN not in loaded
        assert PLANTED_BEARER not in loaded
        assert "sk-ant-" not in loaded

    def test_no_absolute_home_path_reaches_the_record(self, loaded):
        # A composition record is a file a user may attach to a bug report.
        assert "/Users/someone" not in loaded

    def test_the_env_block_is_not_carried_at_all(self, loaded):
        # Not filtered key by key — absent. `env` is where the live Databricks PAT
        # on this machine lives, and a filter is a list someone has to keep
        # correct forever.
        assert "ANTHROPIC_AUTH_TOKEN" not in loaded

    def test_a_command_is_still_identified_well_enough_to_diff(self, settings_tree):
        # The reduction has to survive being useful: two different commands must
        # still produce two different records, or the digest cannot notice a swap.
        tree = settings_tree(user=ENABLED_ONE, plugins=ONE_PLUGIN)
        record = composition.build(tree.home, tree.project)
        hook = record["plugins"][0]["hooks"][0]

        assert hook["cmd_basename"] == "audit.py"
        assert hook["cmd_digest"]
        assert "command" not in hook
