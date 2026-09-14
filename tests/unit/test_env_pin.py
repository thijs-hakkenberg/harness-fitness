"""The environment pin — whether two measurements are comparable at all.

`composition_digest` says what is being compared; `env_hash` says whether the
comparison is legitimate (ADR-007). A model swap that halves tokens per outcome
must never read as a harness win, and this hash is the only thing standing
between that mistake and a report.

Two constraints come from outside and are asserted rather than chosen:

- **The field set is the rig's five.** Matching it is the point — it keeps the
  continuous detector and the controlled confirmer talking about the same
  environment, so a suspicious signal here can be handed to the rig there.
- **The hash is 64 hex, not 12.** `rig/schemas/result.schema.json` types
  `env_hash` as `^[0-9a-f]{64}$`, so a truncated hash would fail `--rig-handoff`
  validation. It is deliberately a different width from `composition_digest`,
  and the difference is load-bearing rather than an inconsistency to tidy up.

The third constraint was found by measurement, not read off a spec: **the model
is not declarable**. On the machine this was written on, `settings.model` said
`opus`, `env.ANTHROPIC_MODEL` said `claude-fable-5-innovation`, and 389 OTEL
`api_request` events reported `claude-opus-5`. No precedence rule over the two
declared fields produces the observed answer. So a disk-resolved model is marked
`declared` and never presented as the model that ran.
"""

import json

import env_pin

# Assembled from parts. A public repo whose own secret scanner matches a fixture
# reports a credential it does not have, on every clone, forever.
PLANTED_TOKEN = "dapi" + "fedcba9876543210" * 2

GATEWAY = "https://gateway.example.com/anthropic"


def user_settings(**over):
    """A settings block shaped like the live one, minus anything secret."""
    settings = {
        "model": "opus",
        "effortLevel": "high",
        "env": {
            "ANTHROPIC_BASE_URL": GATEWAY,
            "ENABLE_PROMPT_CACHING_1H": "1",
        },
    }
    settings.update(over)
    return settings


def record_for(tree, **kw):
    return env_pin.build(tree.home, tree.project, **kw)


def hash_for(settings_tree, **kw):
    """Build one tree and hash it, in that order, in a single expression.

    There is one isolated `HOME` per test, so `settings_tree` *overwrites* rather
    than accumulating: naming two trees and then reading both reads the second one
    twice. That makes a "moves" assertion fail for the wrong reason and — far
    worse — makes a "holds still" assertion pass without having compared anything.
    Composition's suite avoids it by never naming a tree it is about to hash, and
    this helper is what makes the ordering impossible to get wrong here.
    """
    return record_for(settings_tree(**kw))["env_hash"]


class TestTheFieldSet:
    def test_the_pinned_field_set_matches_the_rig(self):
        # Transcribed from `rig/rig/env_pin.py::_PINNED_FIELDS`. Asserted as an
        # exact tuple rather than a subset: an extra field here would make our
        # hash move where the rig's holds still, and the two instruments would
        # disagree about whether the same two runs were comparable.
        assert env_pin.PINNED_FIELDS == (
            "model",
            "effort",
            "anthropic_base_url",
            "cache_policy",
            "max_retries",
        )

    def test_the_hash_is_sixty_four_hex(self, settings_tree):
        tree = settings_tree(user=user_settings())
        got = record_for(tree)["env_hash"]

        assert env_pin.HASH_LEN == 64
        assert len(got) == 64
        assert all(c in "0123456789abcdef" for c in got)

    def test_the_env_block_carries_every_field_the_rig_schema_requires(
        self, settings_tree
    ):
        # `--rig-handoff` validates against the rig's result schema at step 8.
        # Discovering a missing key then would mean reopening this module after
        # every episode already recorded had been pinned without it.
        tree = settings_tree(user=user_settings())
        block = record_for(tree)["env"]

        for field in (
            "model",
            "effort",
            "anthropic_base_url",
            "cache_policy",
            "max_retries",
            "claude_code_version",
            "agent_sdk_version",
            "platform",
            "python_version",
        ):
            assert field in block, field

    def test_the_record_is_json_serialisable(self, settings_tree):
        tree = settings_tree(user=user_settings())
        json.dumps(record_for(tree))


class TestResolution:
    def test_the_base_url_comes_from_the_env_block(self, settings_tree):
        tree = settings_tree(user=user_settings())
        assert record_for(tree)["env"]["anthropic_base_url"] == GATEWAY

    def test_the_effort_comes_from_the_effort_level(self, settings_tree):
        tree = settings_tree(user=user_settings(effortLevel="low"))
        assert record_for(tree)["env"]["effort"] == "low"

    def test_prompt_caching_becomes_the_cache_policy(self, settings_tree):
        tree = settings_tree(user=user_settings())
        assert record_for(tree)["env"]["cache_policy"] == "1h"

    def test_absent_prompt_caching_is_the_default_policy_not_an_unknown(
        self, settings_tree
    ):
        # Absence here is a determinate state: no 1h flag means the default cache
        # policy is in force. Reporting `null` would claim we could not tell.
        tree = settings_tree(user=user_settings(env={"ANTHROPIC_BASE_URL": GATEWAY}))
        assert record_for(tree)["env"]["cache_policy"] == "default"

    def test_max_retries_is_an_integer_when_declared(self, settings_tree):
        tree = settings_tree(
            user=user_settings(
                env={"ANTHROPIC_BASE_URL": GATEWAY, "CLAUDE_CODE_MAX_RETRIES": "4"}
            )
        )
        assert record_for(tree)["env"]["max_retries"] == 4

    def test_an_undeclared_max_retries_is_null_and_not_the_rigs_default(
        self, settings_tree
    ):
        # The rig's `child_env` defaults this to 2 because it is *setting* the
        # value. We are *observing* one, and Claude Code's built-in default is not
        # knowable from disk. Substituting 2 would let a machine that really uses 2
        # and a machine whose default is something else hash identically — the one
        # thing this hash exists to prevent.
        tree = settings_tree(user=user_settings())
        assert record_for(tree)["env"]["max_retries"] is None

    def test_a_settings_layer_overrides_a_scalar(self, settings_tree):
        # Scope precedence is `settings_read`'s job; this asserts `env_pin`
        # actually goes through it rather than reading the user file directly.
        tree = settings_tree(
            user=user_settings(), project_local={"effortLevel": "medium"}
        )
        assert record_for(tree)["env"]["effort"] == "medium"


class TestTheModelIsNotDeclarable:
    def test_a_disk_resolved_model_is_marked_declared(self, settings_tree):
        tree = settings_tree(user=user_settings())
        assert record_for(tree)["model_basis"] == "declared"

    def test_an_observed_model_wins_over_every_declared_field(self, settings_tree):
        # Measured on the machine this was written on: `settings.model = "opus"`,
        # `env.ANTHROPIC_MODEL = "claude-fable-5-innovation"`, and every one of 389
        # `api_request` events reporting `claude-opus-5`. Observation is the only
        # source that was right.
        tree = settings_tree(
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": GATEWAY,
                    "ANTHROPIC_MODEL": "claude-fable-5-innovation",
                }
            )
        )
        record = record_for(tree, observed={"model": "claude-opus-5"})

        assert record["env"]["model"] == "claude-opus-5"
        assert record["model_basis"] == "observed"

    def test_a_model_that_cannot_be_resolved_at_all_is_null_and_unknown(
        self, settings_tree
    ):
        tree = settings_tree(user={"env": {"ANTHROPIC_BASE_URL": GATEWAY}})
        record = record_for(tree)

        assert record["env"]["model"] is None
        assert record["model_basis"] == "unknown"

    def test_the_basis_does_not_enter_the_hash(self, settings_tree):
        # Tempting, because then a declared pin could never be silently compared
        # against an observed one. Refused: every user who gains an OTEL log would
        # re-hash an unchanged environment, splitting one comparable population
        # into two that each fall below `min_episodes_per_digest`. The refusal
        # belongs in the comparison layer, which already refuses on a differing
        # `env_hash`; ADR-007 records the obligation.
        tree = settings_tree(user=user_settings(model="claude-opus-5"))

        declared = record_for(tree)
        observed = record_for(tree, observed={"model": "claude-opus-5"})

        assert declared["model_basis"] != observed["model_basis"]
        assert declared["env_hash"] == observed["env_hash"]


class TestFlags:
    def test_two_disagreeing_declarations_are_flagged(self, settings_tree):
        tree = settings_tree(
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": GATEWAY,
                    "ANTHROPIC_MODEL": "claude-fable-5-innovation",
                }
            )
        )
        kinds = [f["kind"] for f in record_for(tree)["flags"]]
        assert "model-declaration-ambiguous" in kinds

    def test_an_observation_contradicting_the_declaration_is_flagged(
        self, settings_tree
    ):
        # The live case. Unflagged, a reader would assume the declaration was
        # merely an alias for the observed id rather than a stale setting.
        tree = settings_tree(
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": GATEWAY,
                    "ANTHROPIC_MODEL": "claude-fable-5-innovation",
                }
            )
        )
        record = record_for(tree, observed={"model": "claude-opus-5"})
        kinds = [f["kind"] for f in record["flags"]]

        assert "model-declaration-stale" in kinds

    def test_an_agreeing_declaration_and_observation_is_not_flagged(
        self, settings_tree
    ):
        tree = settings_tree(user=user_settings(model="claude-opus-5"))
        record = record_for(tree, observed={"model": "claude-opus-5"})

        assert record["flags"] == []

    def test_a_single_clean_declaration_is_not_flagged(self, settings_tree):
        tree = settings_tree(user=user_settings())
        assert record_for(tree)["flags"] == []


class TestTheHash:
    def test_it_is_stable_for_the_same_environment(self, settings_tree):
        tree = settings_tree(user=user_settings())
        assert record_for(tree)["env_hash"] == record_for(tree)["env_hash"]

    def test_it_ignores_the_order_the_pinned_fields_were_built_in(self):
        a = {"model": "m", "effort": "high", "anthropic_base_url": GATEWAY}
        b = {"anthropic_base_url": GATEWAY, "effort": "high", "model": "m"}
        assert env_pin.env_hash(a) == env_pin.env_hash(b)

    def test_it_moves_when_the_model_moves(self, settings_tree):
        assert hash_for(settings_tree, user=user_settings()) != hash_for(
            settings_tree, user=user_settings(model="sonnet")
        )

    def test_it_moves_when_the_base_url_moves(self, settings_tree):
        assert hash_for(settings_tree, user=user_settings()) != hash_for(
            settings_tree,
            user=user_settings(env={"ANTHROPIC_BASE_URL": "https://other.example.com"}),
        )

    def test_it_moves_when_the_effort_moves(self, settings_tree):
        assert hash_for(settings_tree, user=user_settings()) != hash_for(
            settings_tree, user=user_settings(effortLevel="low")
        )

    def test_it_holds_still_for_an_unpinned_environment_variable(self, settings_tree):
        # The live settings file carries 29 env vars, of which three are pinned.
        # Hashing the block wholesale would move `env_hash` on an OTEL endpoint
        # tweak and refuse every comparison across it.
        assert hash_for(settings_tree, user=user_settings()) == hash_for(
            settings_tree,
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": GATEWAY,
                    "ENABLE_PROMPT_CACHING_1H": "1",
                    "AZURE_DEVOPS_DEFAULT_PROJECT": "somewhere-else",
                }
            ),
        )

    def test_it_holds_still_for_a_composition_change(self, settings_tree):
        # The two identities must be independent, or a plugin toggle would refuse
        # its own before/after comparison as environmentally incomparable —
        # exactly the comparison this plugin exists to make.
        assert hash_for(settings_tree, user=user_settings()) == hash_for(
            settings_tree,
            user=user_settings(enabledPlugins={"abacus@abacus": True}),
            plugins={"abacus@abacus": {"version": "1.0.0"}},
        )

    def test_an_observed_app_version_moves_it(self, settings_tree):
        # A Claude Code upgrade changes the harness's substrate under a fixed
        # config. The rig folds its probed versions in for the same reason.
        tree = settings_tree(user=user_settings())
        a = record_for(tree, observed={"app_version": "2.1.0"})
        b = record_for(tree, observed={"app_version": "2.2.0"})
        assert a["env_hash"] != b["env_hash"]

    def test_the_local_python_version_does_not_move_it(self, settings_tree):
        # Reported in the env block for handoff, deliberately absent from the
        # hash: the interpreter running a measurement hook is not part of the
        # environment being measured, and a brew upgrade must not split a
        # population.
        tree = settings_tree(user=user_settings())
        assert "python_version" not in env_pin.hash_payload(record_for(tree)["env"])


class TestNoLeaks:
    def test_the_auth_token_never_reaches_the_record(self, settings_tree):
        # `env_pin` is the only module that opens the `env` block at all —
        # `composition` deliberately never does. So this is the one place a live
        # credential can escape, and it takes exactly the five fields it needs.
        tree = settings_tree(
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": GATEWAY,
                    "ANTHROPIC_AUTH_TOKEN": PLANTED_TOKEN,
                }
            )
        )
        blob = json.dumps(record_for(tree))

        assert PLANTED_TOKEN not in blob
        assert "ANTHROPIC_AUTH_TOKEN" not in blob

    def test_credentials_embedded_in_the_base_url_are_stripped(self, settings_tree):
        # A base URL is stored verbatim by the rig's env block, and a URL can
        # carry userinfo. Storing it would put a password in a file a user might
        # attach to a bug report.
        tree = settings_tree(
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": "https://someone:%s@gateway.example.com/v1"
                    % PLANTED_TOKEN
                }
            )
        )
        record = record_for(tree)

        assert PLANTED_TOKEN not in json.dumps(record)
        assert record["env"]["anthropic_base_url"] == "https://gateway.example.com/v1"

    def test_a_query_string_credential_is_stripped(self, settings_tree):
        tree = settings_tree(
            user=user_settings(
                env={
                    "ANTHROPIC_BASE_URL": "https://gateway.example.com/v1?api_key=%s"
                    % PLANTED_TOKEN
                }
            )
        )
        record = record_for(tree)

        assert PLANTED_TOKEN not in json.dumps(record)
        assert record["env"]["anthropic_base_url"] == "https://gateway.example.com/v1"

    def test_a_rotated_credential_does_not_move_the_hash(self, settings_tree):
        # The hash covers the sanitised URL, so rotating a token embedded in it
        # does not refuse comparison against yesterday's episodes. The endpoint is
        # what decides comparability; the credential is not.
        assert hash_for(
            settings_tree,
            user=user_settings(
                env={"ANTHROPIC_BASE_URL": "https://u:one@gateway.example.com/v1"}
            ),
        ) == hash_for(
            settings_tree,
            user=user_settings(
                env={"ANTHROPIC_BASE_URL": "https://u:two@gateway.example.com/v1"}
            ),
        )
