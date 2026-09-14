# Contract: composition record and change log — output

## Binding

Files on disk, written only by `hooks/scripts/snapshot_composition.py` through
`hooks/lib/ledger.py`. No MCP server, no network, no stdout.

```
<ledger root>/
  compositions/<digest12>.json   # one per composition ever seen; immutable
  changes.jsonl                  # one line per moment the digest moved
```

The ledger root is `~/.claude/harness-fitness/projects/<project_slug>/` by default,
or `$CLAUDE_PROJECT_DIR/.harness-fitness/` when `ledger.in_repo` is true.
`$HFIT_STATE_DIR` overrides the user-level root. Both files are UTF-8, mode 0600
inside a 0700 directory, and written atomically (`mkstemp` + `os.replace`) so a
crashed write leaves the previous state rather than a truncated file.

**Nothing here is written until `acknowledged.json` carries a fingerprint matching
the governing config (ADR-014).** The check runs before any path is resolved as a
directory, so an unacknowledged install leaves no trace — asserted by looking at the
filesystem, not at a return value, and asserted on *existence* rather than on a file
listing, because an empty directory is a trace too.

## The composition file

`compositions/<digest12>.json`. The filename is the first 12 hex of the digest, and
that is a contract: `ledger.composition_path` composes it, `changes.jsonl` refers to
compositions by it, and every episode from 0.2.0 onward will reference one by name.

```json
{
  "schema": 1,
  "digest": "b79079db273c",
  "env_hash": "085874cc33509de46dc870b7a5b4ba238cbad96a6e8c192e21631ce168357ba6",
  "model_basis": "unknown",
  "captured_at": "2026-09-14T17:09:34Z",
  "profile": {
    "ff_inf": 1, "ff_comp": 0, "fb_comp": 1, "fb_inf": 0,
    "both_inf": 0, "substrate": 0, "packaging": 3, "unknown": 0
  },
  "plugins": [
    {
      "key": "abacus@abacus", "name": "abacus", "marketplace": "abacus",
      "scope": "user", "version": "0.3.1",
      "sha": "aaaa111", "sha_basis": "git",
      "marketplace_source": "unknown", "live_edited": false, "on_disk": true,
      "hooks": [
        {
          "event": "PostToolUse", "matcher": "Edit|Write", "type": "command",
          "cmd_basename": "audit.py", "cmd_digest": "aa7701aa94ed",
          "timeout": null, "owner": "abacus@abacus", "scope": "user"
        }
      ],
      "skills": ["audit"], "agents": [], "mcp_servers": []
    }
  ],
  "settings_hooks": [],
  "project_mcp_servers": [],
  "mcpjson_enabled": [],
  "user": { "skills": [], "agents": [] },
  "marketplaces": {},
  "unresolved": [],
  "flags": []
}
```

| field | meaning |
|---|---|
| `schema` | record schema, currently `1`. Bumped when a consumer would misread an old record. |
| `digest` | 12 hex. sha256 over the canonical form of the *enabled* inventory. The primitive every measure groups by. |
| `env_hash` | 64 hex. The five pinned environment fields, **first-sighting only** — see below. `null` when no pin was supplied. |
| `model_basis` | `observed` \| `declared` \| `unknown` \| `null`. How the model in that pin was resolved. |
| `captured_at` | ISO-8601 Z, the first sighting of this digest. |
| `profile` | FF/FB × COMP/INF roll-up. Eight buckets, always all eight present. |
| `plugins[]` | one per *enabled* plugin, read from its directory rather than trusted from its manifest. |
| `settings_hooks[]` | user- and project-level hooks belonging to no plugin. Real composition, invisible to any plugin-only inventory. |
| `project_mcp_servers` / `mcpjson_enabled` | the resolved server list, and the raw `enabledMcpjsonServers` setting beside it. |
| `user` | `~/.claude/skills` and `~/.claude/agents`. |
| `marketplaces` | provenance per marketplace; `"source": "directory"` means live-edited. |
| `unresolved` | components named by settings but absent from disk. Named, never silently dropped. |
| `flags` | conditions a reader must know about, e.g. `model-unknown`. |

### Written once, never rewritten

A composition file is content-addressed: the same harness produces the same digest,
so a re-sighting resolves to a file that already exists and nothing is written.
Rewriting one would retroactively change what every episode referencing that digest
says it ran under.

That has a consequence which is easy to get wrong in the other direction, so it is
stated here and in `contracts/input/session-start.md` both:

> **`env_hash` and `model_basis` in a composition file are first-sighting provenance
> only.** A later session under a different model produces the same digest, appends
> nothing, and leaves the original pin in place. An episode must resolve its own
> `env_hash` at close time and must **never** read one off a composition record
> (ADR-007).

### Why both halves of the pin are stored

`model_basis` travels beside `env_hash`, never inside it and never omitted. ADR-007
obliges the comparison layer to refuse on a differing basis exactly as it refuses on
a differing hash, and that refusal has nothing to read if the basis was dropped at
write time. A record carrying the hash alone is worse than one carrying neither: it
presents the hash as sufficient for a comparability decision it cannot support, since
two pins that hash alike are comparable only if they were resolved the same way.

The `null` / `"unknown"` distinction is load-bearing. `"unknown"` means the resolver
looked and could not determine a model; `null` means no pin was supplied at all. Only
the first is comparable against another `"unknown"`.

### What never appears here

- **No credential.** The `env` block of any settings file is never opened — not
  filtered, not redacted, never read. `~/.claude/settings.json` can hold a live token
  in `env.ANTHROPIC_AUTH_TOKEN`; no code path on this side can reach it.
- **No command string.** Hook and MCP commands are reduced one-way to
  `cmd_basename` + `cmd_digest` (ADR-010), because they carry absolute paths and can
  carry tokens.
- **No absolute path.** Install paths and marketplace `installLocation` are dropped
  rather than trusted to be uninteresting.
- **No timestamp that would move the digest.** `installedAt` and `lastUpdated` are
  excluded, or every reinstall would read as a new composition (ADR-001).

## The change log

`changes.jsonl`, append-only, one JSON object per line, oldest first.

```json
{"ts":"2026-09-14T17:09:34Z","schema":1,"from":null,"to":"2d53cbf2de0b",
 "env_hash":"0858…7ba6","model_basis":"unknown","kind":"baseline",
 "diff_basis":"baseline","added":[],"removed":[],"changed":[],"profile_delta":{}}
{"ts":"2026-09-14T17:09:34Z","schema":1,"from":"2d53cbf2de0b","to":"b79079db273c",
 "env_hash":"0858…7ba6","model_basis":"unknown","kind":"transition",
 "diff_basis":"records","added":["plugin:other@abacus"],"removed":[],"changed":[],
 "profile_delta":{"packaging":1}}
```

`kind` is `baseline` when `from` is `null`, `transition` otherwise. Component ids are
`<type>:<key>` — `plugin:abacus@abacus`, `hook:…`, `mcp:…`. `profile_delta` carries
**only the buckets that moved**, so an unchanged bucket is absent rather than `0`.

`env_hash` and `model_basis` are carried on the entry itself so a reader of the log
alone can tell whether the two sides were comparable, without resolving both
composition files.

### A record appears only when the digest moves

This runs on every `SessionStart`. A record per invocation would make `changes.jsonl`
a session log, and its duplicates would be indistinguishable from real re-entries
into a previously-seen composition — which is a thing that genuinely happens when a
plugin is disabled and re-enabled.

### `diff_basis`, and why an unavailable diff is `null` and not `[]`

| `diff_basis` | when | diffs |
|---|---|---|
| `baseline` | first sighting | `[]`, `{}` — a first sighting is not a change. Listing forty components as `added` would place a fabricated steering event at the head of every project's history. |
| `records` | both sides resolvable | the real diff |
| `unavailable` | the previous composition file is gone | **`null` on every diff field** |

The third row is the one worth care. An empty list would assert that the digest moved
while nothing about the harness did — which cannot happen. Unknown is never zero.

## Consumers

- `hooks/scripts/fitness.py --json` → `contracts/output/fitness-report.md`
- `skills/harness-composition/SKILL.md`, via that CLI only — a skill never reads these
  files directly, so the file layout stays free to change behind the report.
- From 0.2.0, `episodes.jsonl` records reference a `composition_digest` by name.

## SemVer

- **Contract version:** 1.0.0
- **Dated against:** Claude Code as observed on this machine, 2026-09.
- **Deprecation policy:** adding a key is a **minor** bump. Removing or retyping one,
  or changing the `<digest12>` filename width, is a **major** bump and requires a
  `schema` increment — old records stay on disk and a consumer must be able to tell
  which shape it is holding. Changing what the digest covers is **major** regardless
  of whether the record's shape moves, because it silently repartitions every
  project's history.

## SLA + telemetry

- **Latency:** part of the `SessionStart` budget of 15s; the real write is two file
  operations. See `contracts/input/session-start.md`.
- **Durability:** atomic replace, so a reader never sees a partial record. An
  interrupted append to `changes.jsonl` can leave a truncated final line; every
  reader tolerates one and skips it rather than failing the whole log.
- **Availability:** a blocked write degrades to *nothing recorded*, not to a swallowed
  traceback. The next session reads the absent baseline and re-attempts.
- **Telemetry:** none. No upload, no endpoint, no network call of any kind.
