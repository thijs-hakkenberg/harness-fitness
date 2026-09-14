"""Every harness component is a guide or a sensor, computational or inferential.

The table is transcribed from `Workshop.HarnessEngineering/
build-methods-plugins-agent-sdk.md` §03. It is static and deterministic: no LLM
is involved in classification, ever. A measure whose denominator depended on a
model's opinion of what a hook is would not be reproducible.

Four behaviours here are load-bearing:

- **`PreToolUse` is feedforward, against the source table** ([ADR-005](../../adr/)).
  The workshop files it under FB·COMP because it validates and blocks. But a deny
  happens *before* the action lands, so it prevents rather than detects. Filing it
  as feedback would make measure M4 report abacus's edit gate as a feedback
  control — the exact opposite of what it is. The event list lives in config, so
  the judgement is inspectable and reversible rather than buried here.
- **A sub-agent's direction is not determinable from the table.** The workshop
  says "FF + FB · INF — direction depends on the role": generators steer,
  evaluators sense. Nothing computational distinguishes them, so a sub-agent is
  counted as `both_inf` rather than guessed into one bucket. Defaulting it to
  `ff_inf` would quietly file every review agent as a guide.
- **An unrecognised component or hook event is `unknown`, never a default.** A
  future Claude Code release will add a hook event. Filing it as feedback by
  default would inflate the sensor count of every harness that adopts it — and a
  sensor count that grows on its own is worse than one that admits a gap.
- **A profile always has the same keys.** `profile_delta` subtracts two of them,
  and a report prints them as a table; a key that appears only when non-zero
  would turn both into special cases.
"""

import pytest

import taxonomy


class TestAxes:
    def test_the_bucket_set_is_fixed_and_enumerable(self):
        # A report prints these and `profile_delta` subtracts across them, so the
        # set is part of the contract rather than whatever the input produced.
        assert taxonomy.BUCKETS == (
            "ff_comp",
            "ff_inf",
            "fb_comp",
            "fb_inf",
            "both_inf",
            "substrate",
            "packaging",
            "unknown",
        )

    def test_every_bucket_has_a_human_legible_label(self):
        # The report has to write "B added two FB·COMP sensors". A bucket without
        # a label would be printed as a raw dict key.
        for bucket in taxonomy.BUCKETS:
            assert taxonomy.label(bucket)

    def test_an_unknown_bucket_label_says_so_rather_than_raising(self):
        assert taxonomy.label("not_a_bucket") == "unknown"


class TestClassifyStaticKinds:
    @pytest.mark.parametrize(
        "kind,bucket",
        [
            # §03: skills load on a trigger and steer the approach before code is
            # written; slash commands drive the first attempt down a known path.
            ("skill", "ff_inf"),
            ("command", "ff_inf"),
            ("mcp_server", "substrate"),
            ("manifest", "packaging"),
            ("marketplace", "packaging"),
        ],
    )
    def test_classifies_each_component_kind_from_the_table(self, kind, bucket):
        assert taxonomy.classify(kind)["bucket"] == bucket

    def test_a_subagent_is_both_directions_because_the_table_says_so(self):
        # "FF + FB · INF — direction depends on the role." Generators steer,
        # evaluators sense, and no static rule tells them apart.
        got = taxonomy.classify("agent")

        assert got["bucket"] == "both_inf"
        assert got["direction"] == "both"
        assert got["mechanism"] == "inf"

    def test_a_subagents_ambiguity_is_stated_not_hidden(self):
        # A consumer that wants to split guides from sensors must be able to see
        # that this one component cannot be split, rather than discovering it by
        # finding a review agent counted among the guides.
        assert taxonomy.classify("agent")["basis"] == "table-ambiguous"

    def test_an_unrecognised_kind_is_unknown_rather_than_dropped(self):
        got = taxonomy.classify("quantum_widget")

        assert got["bucket"] == "unknown"
        assert got["direction"] == "unknown"
        assert got["basis"] == "unrecognised-kind"

    def test_the_basis_names_the_table_for_a_known_kind(self):
        assert taxonomy.classify("skill")["basis"] == "table"


class TestClassifyHooks:
    @pytest.mark.parametrize(
        "event",
        ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest"],
    )
    def test_a_feedforward_event_is_ff_comp(self, event):
        got = taxonomy.classify("hook", event=event)

        assert got["bucket"] == "ff_comp"
        assert got["mechanism"] == "comp"

    @pytest.mark.parametrize(
        "event",
        [
            "PostToolUse",
            "Stop",
            "SubagentStop",
            "PreCompact",
            "SessionEnd",
            "Notification",
        ],
    )
    def test_a_feedback_event_is_fb_comp(self, event):
        assert taxonomy.classify("hook", event=event)["bucket"] == "fb_comp"

    def test_pretooluse_diverges_from_the_source_table_deliberately(self):
        # ADR-005, asserted rather than only documented. The workshop files
        # PreToolUse under FB·COMP; a deny prevents the action instead of
        # detecting it, so M4 must count it as a feedforward catch. If this ever
        # flips, abacus's edit gate starts reading as a feedback control.
        got = taxonomy.classify("hook", event="PreToolUse")

        assert got["direction"] == "ff"
        assert got["basis"] == "adr-005"

    def test_the_feedforward_event_list_is_overridable(self):
        # The judgement lives in config precisely so it can be reversed without
        # editing this module. A hard-coded list would make ADR-005 unfalsifiable.
        got = taxonomy.classify("hook", event="PostToolUse", ff_events=("PostToolUse",))

        assert got["bucket"] == "ff_comp"

    def test_an_unknown_event_is_unknown_not_feedback(self):
        # The direction that matters: a new hook event filed as feedback by
        # default would inflate the sensor count of every harness using it.
        got = taxonomy.classify("hook", event="SomeFutureEvent")

        assert got["bucket"] == "unknown"
        assert got["basis"] == "unrecognised-event"

    def test_a_hook_with_no_event_is_unknown(self):
        assert taxonomy.classify("hook")["bucket"] == "unknown"

    def test_the_default_feedforward_list_comes_from_config(self):
        import hfit_config

        # One source of truth. Two lists would drift, and the drift would show up
        # as M4 and the composition profile disagreeing about the same hook.
        for event in hfit_config.DEFAULTS["ff_hook_events"]:
            assert taxonomy.classify("hook", event=event)["direction"] == "ff"


class TestProfile:
    def test_counts_components_into_buckets(self):
        got = taxonomy.profile(
            [
                {"kind": "skill"},
                {"kind": "skill"},
                {"kind": "hook", "event": "PostToolUse"},
                {"kind": "mcp_server"},
            ]
        )

        assert got["ff_inf"] == 2
        assert got["fb_comp"] == 1
        assert got["substrate"] == 1

    def test_every_bucket_is_present_even_at_zero(self):
        # `profile_delta` subtracts two profiles and a report tabulates them.
        # A key that appears only when non-zero makes both a special case.
        got = taxonomy.profile([])

        assert set(got) == set(taxonomy.BUCKETS)
        assert set(got.values()) == {0}

    def test_the_count_does_not_depend_on_input_order(self):
        a = taxonomy.profile([{"kind": "skill"}, {"kind": "agent"}])
        b = taxonomy.profile([{"kind": "agent"}, {"kind": "skill"}])

        assert a == b

    def test_an_unrecognised_component_is_counted_as_unknown(self):
        # Counted, not skipped. A component the taxonomy cannot place is still
        # part of the harness, and dropping it under-reports composition.
        got = taxonomy.profile([{"kind": "mystery"}])

        assert got["unknown"] == 1
        assert sum(got.values()) == 1

    def test_a_malformed_component_does_not_raise(self):
        got = taxonomy.profile([{"kind": "skill"}, "not-a-dict", None, {}])

        assert got["ff_inf"] == 1
        assert got["unknown"] == 3

    def test_the_ff_event_list_reaches_the_profile(self):
        got = taxonomy.profile(
            [{"kind": "hook", "event": "PostToolUse"}], ff_events=("PostToolUse",)
        )

        assert got["ff_comp"] == 1
        assert got["fb_comp"] == 0


class TestProfileDelta:
    def test_two_identical_profiles_have_no_delta(self):
        a = taxonomy.profile([{"kind": "skill"}])

        assert taxonomy.profile_delta(a, dict(a)) == {}

    def test_an_added_sensor_shows_as_a_positive_delta(self):
        before = taxonomy.profile([{"kind": "skill"}])
        after = taxonomy.profile(
            [
                {"kind": "skill"},
                {"kind": "hook", "event": "PostToolUse"},
                {"kind": "hook", "event": "Stop"},
            ]
        )

        # This is the sentence the report has to be able to write: "B added two
        # FB·COMP sensors."
        assert taxonomy.profile_delta(before, after) == {"fb_comp": 2}

    def test_a_removed_guide_shows_as_a_negative_delta(self):
        before = taxonomy.profile([{"kind": "skill"}, {"kind": "command"}])
        after = taxonomy.profile([{"kind": "skill"}])

        assert taxonomy.profile_delta(before, after) == {"ff_inf": -1}

    def test_only_the_buckets_that_moved_appear(self):
        before = taxonomy.profile([{"kind": "skill"}, {"kind": "mcp_server"}])
        after = taxonomy.profile([{"kind": "skill"}, {"kind": "agent"}])

        assert taxonomy.profile_delta(before, after) == {
            "substrate": -1,
            "both_inf": 1,
        }

    def test_a_bucket_this_version_does_not_know_still_shows_a_delta(self):
        # A ledger written by a newer version can carry a bucket added after this
        # one shipped. Dropping it would hide a real composition change behind a
        # version skew — the reader would see "nothing moved" for a harness that
        # gained a component.
        got = taxonomy.profile_delta({"ff_inf": 1}, {"ff_inf": 1, "ff_new_axis": 2})

        assert got == {"ff_new_axis": 2}

    def test_a_non_numeric_count_is_read_as_zero(self):
        # A hand-edited or truncated ledger line must not raise inside the report
        # that was trying to explain the change.
        assert taxonomy.profile_delta({"ff_inf": None}, {"ff_inf": 1}) == {"ff_inf": 1}

    def test_a_missing_bucket_is_read_as_zero_rather_than_raising(self):
        # A profile written by an older version will not have a bucket added
        # later. The delta must degrade to a number, not take down the report
        # that was trying to explain a composition change.
        assert taxonomy.profile_delta({}, {"ff_inf": 1}) == {"ff_inf": 1}
