from datetime import UTC, datetime

import pytest
from conftest import activity, build_task, create, extend

from scopeshift.oracle import is_expired, parse_instant, run_oracle

S, E = "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"


def _status_at(ts: str):
    r = run_oracle(build_task([create("E1", S, S, E), activity("E2", ts)]))
    return r.exception_status.value, r.transition_event_id


def test_exact_expiry_boundary():
    assert _status_at("2026-10-30T04:00:00Z") == ("TERMINATED", "E2")


def test_one_second_before_expiry():
    assert _status_at("2026-10-30T03:59:59Z") == ("ACTIVE", None)


def test_one_second_after_expiry():
    assert _status_at("2026-10-30T04:00:01Z") == ("TERMINATED", "E2")


def test_is_expired_rule():
    end = datetime(2026, 10, 30, 4, tzinfo=UTC)
    assert not is_expired(datetime(2026, 10, 30, 3, 59, 59, 999999, tzinfo=UTC), end)
    assert is_expired(end, end)


def test_offset_timezones_compared_as_instants():
    # 05:00+01:00 == 04:00Z -> exactly at expiry
    assert _status_at("2026-10-30T05:00:00+01:00") == ("TERMINATED", "E2")
    assert _status_at("2026-10-30T04:59:59+01:00") == ("ACTIVE", None)


def test_extended_window_boundary():
    def run(ts):
        t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z"),
                        activity("E3", ts)])
        return run_oracle(t).exception_status.value
    assert run("2026-10-30T04:29:59Z") == "ACTIVE"
    assert run("2026-10-30T04:30:00Z") == "TERMINATED"


def test_extension_exactly_at_expiry_is_too_late():
    t = build_task([create("E1", S, S, E), extend("E2", E, "2026-10-30T04:30:00Z"), activity("E3", "2026-10-30T04:10:00Z")])
    r = run_oracle(t)
    assert r.transition_event_id == "E2" and r.exception_status.value == "TERMINATED"


def test_naive_details_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_instant("2026-10-30T04:00:00", "x")


def test_z_and_utc_offset_equal():
    assert parse_instant("2026-10-30T04:00:00Z", "x") == parse_instant("2026-10-30T04:00:00+00:00", "x")
