# Contract: consent notice and status — output

## Binding

Stdout of `hooks/scripts/acknowledge.py`, the only surface in this plugin that can
open or close the write gate. Three actions, in a required mutually exclusive group,
plus a `--json` modifier:

```
acknowledge.py --show   [--json]    # print the notice and the current status
acknowledge.py --accept [--json]    # consent to recording, and start it
acknowledge.py --revoke [--json]    # withdraw consent; stops all recording
```

There is **no default action**. A bare invocation prints usage on stderr and exits
non-zero. This is a contract term, not an implementation detail: a script whose
default is `--accept` has collected a keystroke rather than a decision, and the whole
gate reduces to whether the user happened to type a word. For the same reason there
is no `-y` short form.

Unlike a hook, this surface is noisy. `--acccept` exits non-zero and complains on
stderr rather than being ignored, because silently proceeding after a mistyped flag
would leave the user believing they had consented while the gate stayed shut.

**`--show` writes nothing, anywhere.** Not a file, not a directory. It is the command
a cautious user runs first, to find out what this thing would do, which makes it the
worst place to break the promise that nothing is recorded before consent — and the
easiest, because resolving a path is one `makedirs` away from creating it. Asserted on
the *existence* of the state root rather than on a file listing, because an empty
directory is a trace too, and a file-only listing cannot see one.

## The notice

Printed by `--show` before the status line, and never by `--json`. Its exact wording
is free to improve; four claims are load-bearing and a test binds to them.

| claim | why it is in the contract |
|---|---|
| The ledger root, as an absolute path | Consent to "measurement" in the abstract is not consent. The user has to be told where the files are so they can read, diff and delete them. |
| The plugin's name | The notice may be read out of context — piped, pasted into an issue, or seen after `/plugin install`. A notice that does not name what is asking is an anonymous request for permission. |
| What is never written | Hook and MCP command strings (reduced to a basename and a one-way digest, ADR-010), credentials (the `env` block of a settings file is never opened at all), prompt text (a length and a hash prefix only), absolute paths from the machine. |
| That nothing leaves the machine | No upload, no endpoint, no telemetry of its own (ADR-011). A measurement tool that does not say this precisely is indistinguishable from one that does not mean it. |

The notice also states what the acknowledgement is *scoped to*: the fingerprint covers
the governing configuration keys only —

```
beads.write_metadata   events_retention_days   inference.enabled
ledger.in_repo         otel.enabled
```

— so changing one of those stops recording until the user accepts again, while tuning
a threshold or a display option does not re-ask. Without that sentence, a user who is
re-prompted after editing a config file has no way to tell a deliberate re-ask from a
bug, and the obvious remedy is to stop using the plugin.

## The status object

`--json` suppresses the notice entirely, so stdout is one JSON object and nothing
else. A skill parses this; there is no second stream for a banner to be lost in.

```json
{
  "accepted": false,
  "accepted_at": null,
  "fingerprint": "9f2c1ab40d3e"
}
```

| key | meaning |
|---|---|
| `accepted` | Whether recording is active **right now** — that is, whether an acknowledgement exists *and* its fingerprint still matches the governing config. A stale acknowledgement reads `false`. |
| `accepted_at` | When consent was given, RFC-3339 UTC, or `null` if it never was. **Preserved across a repeated `--accept`**: the record has to keep saying when the user actually agreed, and a user re-running the command to check it worked must not rewrite history into a consent given just now. |
| `fingerprint` | The **current** governing fingerprint, 12 hex — not the one stored. So a caller comparing it against `acknowledged.json` can see a drift, and `--show` can report one. |

The three keys are the same on every action, so a caller can drive `--accept` and read
the resulting state from one stream without a second invocation.

`accepted: false` with a non-`null` `accepted_at` is the stale case, and `--show`
distinguishes it in prose from never-having-been-asked. "You agreed to something else"
and "you have never been asked" have different remedies, and a user told the second
when the first is true goes looking for a bug.

## Exit codes

| code | when |
|---|---|
| 0 | `--show` always; `--accept` when consent was recorded; `--revoke` always |
| 1 | `--accept` when the acknowledgement could not be written — loud on stderr, because a consent script that prints a confirmation it did not earn leaves every later "nothing recorded" reading as a different bug |
| 2 | argparse: no action, an unknown flag, or two actions at once |

**`--revoke` exits 0 even when there was nothing to remove.** A non-zero exit there
reads as "the revoke did not work", which is the one message in this script that must
never be wrong. Revoking also stops at the acknowledgement: it does not delete the
config, the ledger, or the history the user already agreed to record — withdrawing
consent for future writes is not a request to destroy the past.

## Versioning

This contract is at version 1, matching `consent.SCHEMA`. The three status keys are
additive-only; a new key may appear, and none of the three may change meaning or type
without a schema bump. `acknowledged.json` on disk is a private format described by
`hooks/lib/consent.py` — a caller reads it through this surface, never directly.
