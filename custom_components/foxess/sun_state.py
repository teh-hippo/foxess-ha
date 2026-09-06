"""Pure helpers for FoxESS sun-aware operational state.

Deliberately free of Home Assistant imports so the logic can be unit-tested
without a running hass instance.
"""

from __future__ import annotations

ONLINE = "online"
ASLEEP = "asleep"
OFFLINE = "offline"
OPERATIONAL_STATES = [ONLINE, ASLEEP, OFFLINE]


def expected_online(elevation_now: float, wake_elevation: float) -> bool:
    """Return whether daylight outage time may accrue, before applying the single grace."""
    return elevation_now >= wake_elevation


def operational_state(online: bool, pv_only: bool, is_expected_online: bool) -> str:
    """Map raw online state + PV/battery + sun expectation to a categorised state."""
    if online:
        return ONLINE
    if not pv_only:
        # Battery inverters should stay online 24/7; offline is never benign.
        return OFFLINE
    return OFFLINE if is_expected_online else ASLEEP


def should_offload(
    detail_seen: bool,
    online: bool,
    pv_only: bool,
    elevation_now: float,
    elevation_grace_ago: float,
    wake_elevation: float,
) -> bool:
    """Return True when night cloud polling can be suspended.

    Only once the device detail has been fetched at least once (so the addressbook is
    populated), the inverter is PV-only and offline, and the sun has been below the wake
    elevation for the whole grace window.
    """
    if not (detail_seen and pv_only and not online):
        return False
    return elevation_now < wake_elevation and elevation_grace_ago < wake_elevation


def should_raise_issue(state: str, offline_minutes: float, grace_minutes: float, issue_raised: bool) -> bool:
    """Return True when a Repairs issue should be raised this cycle.

    Debounced by offline duration so a brief daytime cloud blip does not alarm, and
    guarded so the issue is raised at most once.
    """
    return state == OFFLINE and offline_minutes >= grace_minutes and not issue_raised
