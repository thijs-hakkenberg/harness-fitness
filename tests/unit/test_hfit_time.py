"""Time handling.

Every record this plugin writes is timestamped, and every measure is a window
over timestamps. Two things therefore have to be true: the clock is freezable
(or no test of a windowed measure is deterministic), and a malformed timestamp
from an external source never raises inside a hook.
"""

import datetime as dt

import pytest

import hfit_time


class TestNow:
    def test_now_is_timezone_aware_utc(self):
        got = hfit_time.now()
        assert got.tzinfo is not None
        assert got.utcoffset() == dt.timedelta(0)

    def test_hfit_now_freezes_the_clock(self, frozen_now):
        frozen_now("2026-09-14T12:00:00Z")
        assert hfit_time.now() == dt.datetime(
            2026, 9, 14, 12, 0, 0, tzinfo=dt.timezone.utc
        )

    def test_hfit_now_accepts_an_offset_form(self, monkeypatch):
        monkeypatch.setenv("HFIT_NOW", "2026-09-14T14:00:00+02:00")
        assert hfit_time.now() == dt.datetime(
            2026, 9, 14, 12, 0, 0, tzinfo=dt.timezone.utc
        )

    def test_garbage_hfit_now_falls_back_instead_of_raising(self, monkeypatch):
        # A bad env var must not be able to kill a hook.
        monkeypatch.setenv("HFIT_NOW", "not-a-time")
        got = hfit_time.now()
        assert got.tzinfo is not None
        assert got.year >= 2024


class TestIso:
    def test_iso_is_z_suffixed_and_second_precision(self, frozen_now):
        frozen_now("2026-09-14T12:00:00Z")
        assert hfit_time.iso(hfit_time.now()) == "2026-09-14T12:00:00Z"

    def test_iso_normalises_a_non_utc_input_to_utc(self):
        aware = dt.datetime(
            2026, 9, 14, 14, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))
        )
        assert hfit_time.iso(aware) == "2026-09-14T12:00:00Z"

    def test_iso_round_trips_through_parse(self):
        original = dt.datetime(2026, 9, 14, 12, 0, 0, tzinfo=dt.timezone.utc)
        assert hfit_time.parse_iso(hfit_time.iso(original)) == original

    def test_iso_treats_a_naive_datetime_as_utc(self):
        # beads writes naive strings, so naive datetimes reach this on the way
        # back out. Guessing local time would shift every episode boundary.
        naive = dt.datetime(2026, 9, 14, 12, 0, 0)
        assert hfit_time.iso(naive) == "2026-09-14T12:00:00Z"

    @pytest.mark.parametrize("bad", ["2026-09-14", 0, [], {}])
    def test_iso_of_a_non_datetime_is_none(self, bad):
        # `iso(None)` means "now", so the not-a-datetime case cannot also be
        # signalled by None input; it is signalled by None output.
        assert hfit_time.iso(bad) is None


class TestParseIso:
    @pytest.mark.parametrize(
        "raw",
        [
            "2026-09-14T12:00:00Z",
            "2026-09-14T12:00:00+00:00",
            "2026-09-14T12:00:00.000Z",
            "2026-09-14T12:00:00",  # naive: beads writes these
        ],
    )
    def test_accepted_forms_all_mean_the_same_instant(self, raw):
        got = hfit_time.parse_iso(raw)
        assert got == dt.datetime(2026, 9, 14, 12, 0, 0, tzinfo=dt.timezone.utc)

    @pytest.mark.parametrize("raw", ["", "   ", "nonsense", "2026-13-45", None, 17, {}])
    def test_unparseable_input_returns_none_not_an_exception(self, raw):
        assert hfit_time.parse_iso(raw) is None


class TestUnixNanoToDt:
    def test_accepts_the_string_nanoseconds_otlp_actually_emits(self):
        # timeUnixNano is a JSON *string* on the wire. Reading it as an int
        # without coercion is the trap this test exists to hold shut.
        assert hfit_time.unix_nano_to_dt("1789389456425000000") == dt.datetime(
            2026, 9, 14, 12, 37, 36, 425000, tzinfo=dt.timezone.utc
        )

    def test_accepts_an_int_too(self):
        assert hfit_time.unix_nano_to_dt(
            1789389456425000000
        ) == hfit_time.unix_nano_to_dt("1789389456425000000")

    @pytest.mark.parametrize("raw", [None, "", "abc", {}, []])
    def test_unusable_input_returns_none(self, raw):
        assert hfit_time.unix_nano_to_dt(raw) is None


class TestAgeHelpers:
    def test_days_between_is_signed_and_fractional(self):
        a = dt.datetime(2026, 9, 10, 0, 0, 0, tzinfo=dt.timezone.utc)
        b = dt.datetime(2026, 9, 14, 12, 0, 0, tzinfo=dt.timezone.utc)
        assert hfit_time.days_between(a, b) == pytest.approx(4.5)
        assert hfit_time.days_between(b, a) == pytest.approx(-4.5)

    def test_days_between_returns_none_when_either_side_is_unparseable(self):
        a = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)
        assert hfit_time.days_between(a, None) is None
        assert hfit_time.days_between(None, a) is None

    def test_days_between_treats_a_naive_side_as_utc(self):
        # Mixing an aware and a naive datetime raises in Python. A window
        # measure must not be the place that discovers that.
        naive = dt.datetime(2026, 9, 10, 0, 0, 0)
        aware = dt.datetime(2026, 9, 14, 12, 0, 0, tzinfo=dt.timezone.utc)
        assert hfit_time.days_between(naive, aware) == pytest.approx(4.5)
        assert hfit_time.days_between(aware, naive) == pytest.approx(-4.5)
