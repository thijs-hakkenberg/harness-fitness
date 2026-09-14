"""Read the layered settings files that decide what the harness is made of.

Four files, in ascending precedence: the user's `settings.json`, the user's
`settings.local.json`, the project's `settings.json`, the project's
`settings.local.json`. Claude Code resolves them with two *different* merge
rules, and this module reproduces both rather than picking one:

- **Mappings override key-wise.** `enabledPlugins` and `env` merge, so a project
  that sets one variable does not blank the user's others. A wholesale replace
  would drop `ANTHROPIC_MODEL` and make every `env_hash` wrong.
- **Hooks accumulate.** Claude Code runs the user's hook *and* the project's.
  Treating them as an override would report a smaller harness than the one that
  ran — an under-count, which is the error direction that makes a measure
  quietly wrong rather than obviously so.
- **Lists replace.** `enabledMcpjsonServers` has no key to merge on, and
  concatenating would leave a project unable to narrow the user's selection.

Nothing here raises on bad input. A typo in a settings file must degrade one
measure, not end the session, so an unparseable layer is *reported* alongside
the layers that did read.

Command strings are reduced to a basename and a digest before they leave this
module (ADR-010). Doing it here rather than in `composition` means no downstream
module can leak one even by accident: on a real machine a hook command carries
absolute home paths and sometimes an inline credential.
"""

import json
import os

import _security

# Ascending precedence. The order is the contract — every merge below walks this
# list forwards and lets the later layer win.
SCOPES = ("user", "user_local", "project", "project_local")

# `enabledMcpjsonServers: "*"` means every server in `.mcp.json`. Expanding it
# here would need a second source and could silently disagree with it, so it is
# passed through as a sentinel for the caller that holds both.
ALL_MCPJSON = ("*",)


def layer_paths(home, project_root):
    """The four settings paths, ascending precedence, project scopes last.

    Returns pairs so the scope label travels with the path; a hook record has to
    say which layer it came from.
    """
    home = _as_path(home)
    paths = [
        ("user", os.path.join(home, ".claude", "settings.json")),
        ("user_local", os.path.join(home, ".claude", "settings.local.json")),
    ]
    if project_root:
        proj = _as_path(project_root)
        paths.append(("project", os.path.join(proj, ".claude", "settings.json")))
        paths.append(
            ("project_local", os.path.join(proj, ".claude", "settings.local.json"))
        )
    return paths


def read_layers(home, project_root):
    """`(layers, unreadable)` — every layer that parsed, and why the rest did not.

    `layers` is `[(scope, dict), ...]` in ascending precedence; `unreadable` is
    `[(scope, reason), ...]`. A missing file is neither: not configuring a layer
    is the normal case, not a fault.
    """
    layers = []
    unreadable = []

    for scope, path in layer_paths(home, project_root):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            unreadable.append((scope, "unparseable"))
            continue
        if not isinstance(data, dict):
            # Valid JSON, wrong shape. Caught here so it does not surface as a
            # TypeError somewhere far from the file that caused it.
            unreadable.append((scope, "not_an_object"))
            continue
        layers.append((scope, data))

    return layers, unreadable


def merged_scalar(layers, key, default=None):
    """The value from the highest-precedence layer that declares `key`."""
    value = default
    for _scope, data in layers:
        if key in data:
            value = data[key]
    return value


def merged_mapping(layers, key):
    """Key-wise merge of a mapping across layers, higher precedence winning.

    A layer whose value is not a mapping contributes nothing — it is a
    configuration error, and dropping the well-formed layers with it would be a
    worse answer than ignoring the broken one.
    """
    merged = {}
    for _scope, data in layers:
        value = data.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def enabled_plugin_keys(layers):
    """The plugin keys that are actually on, sorted.

    This is the on/off truth. A plugin present on disk is not part of the
    harness; a plugin enabled is. Sorted because the composition digest must not
    depend on the order someone's settings file happens to list them in.
    """
    merged = merged_mapping(layers, "enabledPlugins")
    return tuple(sorted(key for key, on in merged.items() if on))


def settings_hooks(layers):
    """Hook entries declared in settings files, in declaration order.

    These belong to no plugin — eleven of them on the machine this was measured
    against — and a plugin-only inventory cannot see them. They are real
    composition, so they are read from the same place Claude Code reads them.

    Order is left as declared rather than sorted: it is stable for a given set
    of files, and `composition` canonicalises anyway. Sorting here would discard
    execution order for no gain.
    """
    records = []
    for scope, data in layers:
        records.extend(hook_entries(data.get("hooks"), scope))
    return tuple(records)


def hook_entries(hooks_block, scope, owner=None):
    """Parse one `hooks` block into records, skipping anything malformed.

    Shared with `plugin_scan`, because a plugin's `hooks/hooks.json` uses this
    exact shape. One implementation means the command reduction of ADR-010
    happens in one place and cannot be forgotten at a second call site.

    `scope` says which layer or install the block came from; `owner` names the
    plugin that declared it, and is `None` for a settings-level hook that belongs
    to no plugin.
    """
    if not isinstance(hooks_block, dict):
        return ()

    records = []
    for event, groups in hooks_block.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            matcher = group.get("matcher")
            for entry in entries:
                record = _hook_record(scope, owner, event, matcher, entry)
                if record is not None:
                    records.append(record)
    return tuple(records)


def _hook_record(scope, owner, event, matcher, entry):
    if not isinstance(entry, dict):
        return None
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    record = {
        "scope": scope,
        "owner": owner,
        "event": event,
        # None and "" are different reaches: no matcher fires on every tool, an
        # empty matcher fires on none. Collapsing them would misreport a sensor.
        "matcher": matcher if isinstance(matcher, str) else None,
        "type": entry.get("type") or "command",
        "timeout": entry.get("timeout") if isinstance(entry.get("timeout"), int) else None,
    }
    # The raw command stops here, permanently.
    record.update(_security.hash_command(command))
    return record


def mcpjson_enabled(layers):
    """Server names enabled from `.mcp.json`, or `ALL_MCPJSON` for a wildcard."""
    value = merged_scalar(layers, "enabledMcpjsonServers")
    if value == "*":
        return ALL_MCPJSON
    if isinstance(value, list):
        return tuple(sorted(str(name) for name in value))
    return ()


def read(cwd):
    """Everything the composition inventory needs from settings, for one project.

    `cwd` comes from a hook payload. `CLAUDE_PROJECT_DIR` wins when set, because
    a session's project root is not always its working directory. Neither is
    required to exist — a cwd that has been deleted still has user-level layers
    worth reading.
    """
    home = os.path.expanduser("~")
    project_root = os.environ.get("CLAUDE_PROJECT_DIR") or cwd

    layers, unreadable = read_layers(home, project_root)

    return {
        "project_root": _as_path(project_root) if project_root else None,
        "scopes_present": tuple(scope for scope, _data in layers),
        "enabled_plugins": enabled_plugin_keys(layers),
        "settings_hooks": settings_hooks(layers),
        "mcpjson_enabled": mcpjson_enabled(layers),
        "env": merged_mapping(layers, "env"),
        "effort_level": merged_scalar(layers, "effortLevel"),
        "permissions": merged_mapping(layers, "permissions"),
        "unreadable": unreadable,
    }


def _as_path(value):
    return value if isinstance(value, str) else str(value)
