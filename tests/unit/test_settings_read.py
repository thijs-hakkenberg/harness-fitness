"""Layered settings are the on/off truth for what the harness is composed of.

A plugin present on disk is not part of the harness; a plugin *enabled* is. That
distinction lives in `enabledPlugins` across four settings files, and getting the
precedence wrong would silently inventory components that never load.

Two behaviours here are easy to get backwards and both are asserted explicitly:

- `enabledPlugins` **overrides** across layers — a `false` at higher precedence
  disables a plugin enabled at lower precedence.
- `hooks` **accumulate** across layers — Claude Code runs the user's hook *and*
  the project's. Treating them as an override would under-report composition,
  which is the one direction of error a measurement tool must not make.

Command strings never cross this module's boundary in raw form (ADR-010). The
reduction happens here rather than in `composition`, so that no downstream module
can leak one even by accident.
"""

import json

import pytest

import settings_read


# A credential-shaped value planted in a hook command, assembled from parts so
# the file itself carries no scannable literal (see tests/unit/test_security.py).
PLANTED = "dapi" + "0123456789abcdef" * 2


class TestLayerPaths:
    def test_returns_four_scopes_in_ascending_precedence(self, isolated_home, tmp_path):
        proj = tmp_path / "proj"
        got = settings_read.layer_paths(isolated_home, proj)

        assert [scope for scope, _path in got] == [
            "user",
            "user_local",
            "project",
            "project_local",
        ]

    def test_paths_point_where_claude_code_actually_looks(
        self, isolated_home, tmp_path
    ):
        proj = tmp_path / "proj"
        got = dict(settings_read.layer_paths(isolated_home, proj))

        # Paths are strings, as everywhere else in `hooks/lib`, so a hook can
        # hand one straight to `open` without a Path round-trip.
        assert got["user"] == str(isolated_home / ".claude" / "settings.json")
        assert got["user_local"] == str(
            isolated_home / ".claude" / "settings.local.json"
        )
        assert got["project"] == str(proj / ".claude" / "settings.json")
        assert got["project_local"] == str(proj / ".claude" / "settings.local.json")

    def test_omits_project_scopes_when_there_is_no_project(self, isolated_home):
        got = settings_read.layer_paths(isolated_home, None)

        assert [scope for scope, _path in got] == ["user", "user_local"]


class TestReadLayers:
    def test_reads_only_the_files_that_exist(self, settings_tree):
        tree = settings_tree(user={"a": 1}, project={"b": 2})

        layers, unreadable = settings_read.read_layers(tree.home, tree.project)

        assert [scope for scope, _data in layers] == ["user", "project"]
        assert unreadable == []

    def test_preserves_ascending_precedence_order(self, settings_tree):
        tree = settings_tree(
            user={"a": 1}, user_local={"a": 2}, project={"a": 3}, project_local={"a": 4}
        )

        layers, _unreadable = settings_read.read_layers(tree.home, tree.project)

        assert [data["a"] for _scope, data in layers] == [1, 2, 3, 4]

    def test_a_corrupt_layer_is_reported_not_raised(self, settings_tree):
        # A typo in settings.json must not take the hook down with it. The whole
        # point of fail-open is that a broken input degrades one measure rather
        # than every session.
        tree = settings_tree(user={"a": 1})
        (tree.project / ".claude").mkdir(parents=True, exist_ok=True)
        (tree.project / ".claude" / "settings.json").write_text("{not json,")

        layers, unreadable = settings_read.read_layers(tree.home, tree.project)

        assert [scope for scope, _data in layers] == ["user"]
        assert [scope for scope, _reason in unreadable] == ["project"]

    def test_a_corrupt_layer_does_not_stop_the_layers_after_it(self, settings_tree):
        tree = settings_tree(project_local={"a": 4})
        (tree.home / ".claude" / "settings.json").write_text("]")

        layers, unreadable = settings_read.read_layers(tree.home, tree.project)

        assert [scope for scope, _data in layers] == ["project_local"]
        assert [scope for scope, _reason in unreadable] == ["user"]

    def test_a_non_object_top_level_counts_as_unreadable(self, settings_tree):
        # Valid JSON, wrong shape. `[]` would otherwise be indexed as a dict
        # later and raise somewhere far from the cause.
        tree = settings_tree()
        (tree.home / ".claude" / "settings.json").write_text("[1, 2, 3]")

        layers, unreadable = settings_read.read_layers(tree.home, tree.project)

        assert layers == []
        assert unreadable == [("user", "not_an_object")]

    def test_no_settings_at_all_is_not_an_error(self, settings_tree):
        tree = settings_tree()

        layers, unreadable = settings_read.read_layers(tree.home, tree.project)

        assert layers == []
        assert unreadable == []


class TestMerged:
    def test_higher_precedence_wins_for_a_scalar(self, settings_tree):
        tree = settings_tree(user={"effortLevel": "low"}, project={"effortLevel": "high"})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.merged_scalar(layers, "effortLevel") == "high"

    def test_absent_key_returns_the_default(self, settings_tree):
        tree = settings_tree(user={})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.merged_scalar(layers, "nope", "fallback") == "fallback"

    def test_a_mapping_merges_key_wise_rather_than_replacing(self, settings_tree):
        # `env` in a project settings file adds to the user's env, it does not
        # blank it. Replacing wholesale would drop ANTHROPIC_MODEL and make
        # env_hash wrong for every project that sets one variable.
        tree = settings_tree(
            user={"env": {"ANTHROPIC_MODEL": "opus", "MAX_RETRIES": "3"}},
            project={"env": {"MAX_RETRIES": "5"}},
        )
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.merged_mapping(layers, "env") == {
            "ANTHROPIC_MODEL": "opus",
            "MAX_RETRIES": "5",
        }

    def test_a_mapping_is_empty_when_no_layer_declares_it(self, settings_tree):
        tree = settings_tree(user={})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.merged_mapping(layers, "env") == {}

    def test_a_non_mapping_value_is_ignored_rather_than_merged(self, settings_tree):
        tree = settings_tree(user={"env": "not-a-dict"}, project={"env": {"A": "1"}})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.merged_mapping(layers, "env") == {"A": "1"}


class TestEnabledPlugins:
    def test_returns_only_the_enabled_keys(self, settings_tree):
        tree = settings_tree(user={"enabledPlugins": {"a@m": True, "b@m": False}})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.enabled_plugin_keys(layers) == ("a@m",)

    def test_a_higher_layer_can_disable_what_a_lower_layer_enabled(self, settings_tree):
        # The on/off truth. If this merged the wrong way the inventory would
        # include a plugin that never loads, and every measure grouped by that
        # composition would be attributed to a harness that did not exist.
        tree = settings_tree(
            user={"enabledPlugins": {"a@m": True, "b@m": True}},
            project_local={"enabledPlugins": {"b@m": False}},
        )
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.enabled_plugin_keys(layers) == ("a@m",)

    def test_a_higher_layer_can_enable_what_a_lower_layer_disabled(self, settings_tree):
        tree = settings_tree(
            user={"enabledPlugins": {"a@m": False}},
            project={"enabledPlugins": {"a@m": True}},
        )
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.enabled_plugin_keys(layers) == ("a@m",)

    def test_keys_are_sorted_so_the_digest_is_order_independent(self, settings_tree):
        tree = settings_tree(
            user={"enabledPlugins": {"z@m": True, "a@m": True, "m@m": True}}
        )
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.enabled_plugin_keys(layers) == ("a@m", "m@m", "z@m")

    def test_a_malformed_enabled_plugins_block_yields_nothing(self, settings_tree):
        tree = settings_tree(user={"enabledPlugins": ["a@m"]})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.enabled_plugin_keys(layers) == ()


class TestSettingsHooks:
    def _layers(self, settings_tree, **kw):
        tree = settings_tree(**kw)
        layers, _ = settings_read.read_layers(tree.home, tree.project)
        return layers

    def test_reads_a_user_level_hook_that_belongs_to_no_plugin(self, settings_tree):
        # Source 7 in the plan: eleven such entries on the live machine. A
        # plugin-only inventory cannot see them, and they are real composition.
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {"type": "command", "command": "/x/auto-test.sh"}
                            ],
                        }
                    ]
                }
            },
        )

        got = settings_read.settings_hooks(layers)

        assert len(got) == 1
        assert got[0]["event"] == "PostToolUse"
        assert got[0]["matcher"] == "Edit|Write"
        assert got[0]["scope"] == "user"
        assert got[0]["cmd_basename"] == "auto-test.sh"

    def test_hooks_accumulate_across_layers_rather_than_override(self, settings_tree):
        # Claude Code runs both. Overriding would report a smaller harness than
        # the one that actually ran — an under-count, which is the error
        # direction that makes a measure quietly wrong instead of obviously so.
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/x/u.sh"}]}]
                }
            },
            project={
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/x/p.sh"}]}]
                }
            },
        )

        got = settings_read.settings_hooks(layers)

        assert sorted(h["cmd_basename"] for h in got) == ["p.sh", "u.sh"]
        assert sorted(h["scope"] for h in got) == ["project", "user"]

    def test_an_absent_matcher_is_none_not_an_empty_string(self, settings_tree):
        # A hook with no matcher fires on every tool; one matching "" fires on
        # none. Collapsing them would misreport the sensor's reach.
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "/x/s.sh"}]}
                    ]
                }
            },
        )

        assert settings_read.settings_hooks(layers)[0]["matcher"] is None

    def test_multiple_hooks_in_one_matcher_group_are_all_recorded(self, settings_tree):
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "/x/one.sh"},
                                {"type": "command", "command": "/x/two.sh"},
                            ],
                        }
                    ]
                }
            },
        )

        got = settings_read.settings_hooks(layers)

        assert sorted(h["cmd_basename"] for h in got) == ["one.sh", "two.sh"]

    def test_the_timeout_is_carried_when_declared(self, settings_tree):
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/x/s.sh",
                                    "timeout": 45,
                                }
                            ]
                        }
                    ]
                }
            },
        )

        assert settings_read.settings_hooks(layers)[0]["timeout"] == 45

    def test_no_raw_command_ever_crosses_the_boundary(self, settings_tree):
        # ADR-010, enforced at the earliest possible point. A hook command on a
        # real machine can carry an absolute home path or an inline credential.
        raw = "ANTHROPIC_AUTH_TOKEN=%s /Users/someone/.claude/x/gate.py" % PLANTED
        layers = self._layers(
            settings_tree,
            user={"hooks": {"Stop": [{"hooks": [{"type": "command", "command": raw}]}]}},
        )

        got = settings_read.settings_hooks(layers)

        blob = json.dumps(got)
        assert PLANTED not in blob
        assert "/Users/someone" not in blob
        assert "command" not in got[0]
        assert len(got[0]["cmd_digest"]) == 12

    def test_a_malformed_hook_block_is_skipped_not_raised(self, settings_tree):
        # Every shape a hand-edited settings file can go wrong in. A hook that
        # raises on any of them is a hook that dies the first time someone
        # typos, taking the session's measurement with it.
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "Stop": "not-a-list",
                    "PreToolUse": [{"hooks": "not-a-list"}],
                    "PostToolUse": [{"hooks": [{"type": "command"}]}],
                    "PreCompact": ["a-string-where-a-group-belongs"],
                    "SubagentStop": [{"hooks": ["a-string-where-an-entry-belongs"]}],
                    "Notification": [{"hooks": [{"type": "command", "command": "  "}]}],
                    "SessionEnd": [
                        {"hooks": [{"type": "command", "command": "/x/ok.sh"}]}
                    ],
                }
            },
        )

        got = settings_read.settings_hooks(layers)

        assert [h["cmd_basename"] for h in got] == ["ok.sh"]

    def test_a_non_string_matcher_is_normalised_to_none(self, settings_tree):
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "Stop": [
                        {
                            "matcher": ["Edit", "Write"],
                            "hooks": [{"type": "command", "command": "/x/s.sh"}],
                        }
                    ]
                }
            },
        )

        assert settings_read.settings_hooks(layers)[0]["matcher"] is None

    def test_a_non_integer_timeout_is_dropped_rather_than_carried(self, settings_tree):
        # A string timeout would reach the composition record and then a report
        # that formats it as a number.
        layers = self._layers(
            settings_tree,
            user={
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/x/s.sh",
                                    "timeout": "45",
                                }
                            ]
                        }
                    ]
                }
            },
        )

        assert settings_read.settings_hooks(layers)[0]["timeout"] is None

    def test_no_hooks_block_yields_an_empty_tuple(self, settings_tree):
        layers = self._layers(settings_tree, user={})

        assert settings_read.settings_hooks(layers) == ()


class TestMcpjsonServers:
    def test_returns_the_declared_server_names(self, settings_tree):
        tree = settings_tree(user={"enabledMcpjsonServers": ["b", "a"]})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.mcpjson_enabled(layers) == ("a", "b")

    def test_a_wildcard_is_reported_as_a_wildcard_not_expanded(self, settings_tree):
        # `"*"` means "every server in .mcp.json". Expanding it here would need
        # a second source and would silently disagree with it; the caller that
        # has both can resolve it.
        tree = settings_tree(user={"enabledMcpjsonServers": "*"})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.mcpjson_enabled(layers) == settings_read.ALL_MCPJSON

    def test_absent_means_none_enabled(self, settings_tree):
        tree = settings_tree(user={})
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.mcpjson_enabled(layers) == ()

    def test_a_higher_layer_replaces_the_list(self, settings_tree):
        # A list, unlike a mapping, has no key to merge on. Concatenating would
        # make a project unable to narrow the user's selection.
        tree = settings_tree(
            user={"enabledMcpjsonServers": ["a", "b"]},
            project={"enabledMcpjsonServers": ["c"]},
        )
        layers, _ = settings_read.read_layers(tree.home, tree.project)

        assert settings_read.mcpjson_enabled(layers) == ("c",)


class TestReadForCwd:
    def test_resolves_the_project_root_from_a_cwd(self, settings_tree, monkeypatch):
        tree = settings_tree(
            user={"enabledPlugins": {"a@m": True}},
            project={"enabledPlugins": {"a@m": False}},
        )
        monkeypatch.setenv("HOME", str(tree.home))

        got = settings_read.read(str(tree.project))

        assert got["enabled_plugins"] == ()
        assert got["unreadable"] == []

    def test_reports_effort_and_env_for_the_env_hash(self, settings_tree):
        tree = settings_tree(
            user={"env": {"ANTHROPIC_MODEL": "opus"}, "effortLevel": "high"}
        )

        got = settings_read.read(str(tree.project))

        assert got["env"]["ANTHROPIC_MODEL"] == "opus"
        assert got["effort_level"] == "high"

    def test_an_unreadable_layer_surfaces_in_the_result(self, settings_tree):
        tree = settings_tree()
        (tree.home / ".claude" / "settings.json").write_text("{oops")

        got = settings_read.read(str(tree.project))

        assert got["unreadable"] == [("user", "unparseable")]

    def test_a_cwd_that_does_not_exist_still_reads_the_user_layers(self, settings_tree):
        tree = settings_tree(user={"enabledPlugins": {"a@m": True}})

        got = settings_read.read("/nonexistent/path/nowhere")

        assert got["enabled_plugins"] == ("a@m",)
