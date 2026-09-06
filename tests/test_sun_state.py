"""Tests for the pure sun-aware state helpers (custom_components.foxess.sun_state)."""

from __future__ import annotations

import pytest

from custom_components.foxess.sun_state import (
    ASLEEP,
    OFFLINE,
    ONLINE,
    OPERATIONAL_STATES,
    expected_online,
    operational_state,
    should_offload,
    should_raise_issue,
)


@pytest.mark.parametrize(
    ("elev_now", "wake", "expected"),
    [
        (10.0, 5.0, True),
        (2.0, 5.0, False),
        (5.0, 5.0, True),
        (4.99, 5.0, False),
    ],
)
def test_expected_online(elev_now: float, wake: float, expected: bool) -> None:
    assert expected_online(elev_now, wake) is expected


@pytest.mark.parametrize(
    ("online", "pv_only", "is_expected", "result"),
    [
        (True, True, True, ONLINE),
        (True, True, False, ONLINE),
        (True, False, False, ONLINE),  # online always wins
        (False, True, True, OFFLINE),  # PV-only, sun up -> unexpected offline
        (False, True, False, ASLEEP),  # PV-only, sun down -> benign asleep
        (False, False, True, OFFLINE),  # battery, sun irrelevant -> offline
        (False, False, False, OFFLINE),  # battery never asleep
    ],
)
def test_operational_state(online: bool, pv_only: bool, is_expected: bool, result: str) -> None:
    assert operational_state(online, pv_only, is_expected) == result


def test_battery_is_never_asleep() -> None:
    for is_expected in (True, False):
        assert operational_state(False, False, is_expected) != ASLEEP


def test_operational_states_constant_is_stable() -> None:
    # The ENUM sensor's option set must stay frozen; automations depend on it.
    assert OPERATIONAL_STATES == [ONLINE, ASLEEP, OFFLINE]


@pytest.mark.parametrize(
    ("detail_seen", "online", "pv_only", "elev_now", "elev_grace_ago", "wake", "expected"),
    [
        (True, False, True, 2.0, 2.0, 5.0, True),  # PV-only, offline, night -> offload
        (False, False, True, 2.0, 2.0, 5.0, False),  # no detail yet -> never offload (first poll must run)
        (True, True, True, 2.0, 2.0, 5.0, False),  # online -> never offload
        (True, False, False, 2.0, 2.0, 5.0, False),  # battery -> never offload
        (True, False, True, 10.0, 10.0, 5.0, False),  # daytime -> keep polling
        (True, False, True, 2.0, 10.0, 5.0, False),  # dusk (now below, grace_ago above) -> keep polling
        (True, False, True, 10.0, 2.0, 5.0, False),  # dawn (now above, grace_ago below) -> keep polling
    ],
)
def test_should_offload(
    detail_seen: bool,
    online: bool,
    pv_only: bool,
    elev_now: float,
    elev_grace_ago: float,
    wake: float,
    expected: bool,
) -> None:
    assert should_offload(detail_seen, online, pv_only, elev_now, elev_grace_ago, wake) is expected


@pytest.mark.parametrize(
    ("state", "offline_minutes", "grace", "issue_raised", "expected"),
    [
        (OFFLINE, 60.0, 60.0, False, True),  # offline for >= grace -> raise
        (OFFLINE, 59.0, 60.0, False, False),  # mid-day blip / within grace -> no raise (debounce)
        (OFFLINE, 120.0, 60.0, True, False),  # already raised -> never double-raise
        (ASLEEP, 600.0, 60.0, False, False),  # asleep is benign -> never raise
        (ONLINE, 600.0, 60.0, False, False),  # online -> never raise
    ],
)
def test_should_raise_issue(
    state: str, offline_minutes: float, grace: float, issue_raised: bool, expected: bool
) -> None:
    assert should_raise_issue(state, offline_minutes, grace, issue_raised) is expected
