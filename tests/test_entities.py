"""Tests for the FoxESS status entity, the night zero-leak fix, and DST-safe elevation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from custom_components.foxess.sensor import (
    FoxESSEnergySolar,
    FoxESSSolarPower,
    FoxESSStatus,
    _solar_elevation,
)

SYD = ZoneInfo("Australia/Sydney")


def _entity(cls, data: dict):
    coordinator = MagicMock()
    coordinator.data = data
    return cls(coordinator, "FoxESS", "DEV1")


def test_status_native_value_reports_operational_state() -> None:
    assert _entity(FoxESSStatus, {"operational_state": "asleep"}).native_value == "asleep"
    assert _entity(FoxESSStatus, {"operational_state": "offline"}).native_value == "offline"


def test_status_native_value_none_before_first_update() -> None:
    assert _entity(FoxESSStatus, {}).native_value is None


def test_status_options_are_the_enum_set() -> None:
    entity = _entity(FoxESSStatus, {"operational_state": "online"})
    assert entity._attr_options == ["online", "asleep", "offline"]


def test_energy_solar_returns_none_when_offline() -> None:
    # The fix: never emit a synthetic 0 on a TOTAL_INCREASING sensor while offline.
    entity = _entity(FoxESSEnergySolar, {"online": False, "report": {}})
    assert entity.native_value is None


def test_energy_solar_computes_value_when_online() -> None:
    entity = _entity(
        FoxESSEnergySolar,
        {
            "online": True,
            "report": {
                "loads": 5,
                "chargeEnergyToTal": 0,
                "feedin": 2,
                "gridConsumption": 1,
                "dischargeEnergyToTal": 0,
            },
        },
    )
    # loads + charge + feedin - grid - discharge = 5 + 0 + 2 - 1 - 0 = 6
    assert entity.native_value == 6.0


def test_solar_power_returns_none_when_offline() -> None:
    entity = _entity(FoxESSSolarPower, {"online": False, "raw": {}})
    assert entity.native_value is None


async def test_solar_elevation_is_dst_safe(hass) -> None:
    hass.config.latitude = -33.87
    hass.config.longitude = 151.21
    hass.config.elevation = 20
    await hass.config.async_set_time_zone("Australia/Sydney")

    winter_noon = datetime(2026, 6, 21, 12, 0, tzinfo=SYD).astimezone(ZoneInfo("UTC"))
    summer_noon = datetime(2026, 12, 21, 12, 0, tzinfo=SYD).astimezone(ZoneInfo("UTC"))
    winter_midnight = datetime(2026, 6, 21, 0, 0, tzinfo=SYD).astimezone(ZoneInfo("UTC"))

    # Sun is well up at local noon in both seasons (AEST in June, AEDT in December),
    # and below the horizon at midnight - proving tz/DST-correct elevation.
    assert _solar_elevation(hass, winter_noon) > 20
    assert _solar_elevation(hass, summer_noon) > 60
    assert _solar_elevation(hass, winter_midnight) < 0
    # Summer noon is higher than winter noon.
    assert _solar_elevation(hass, summer_noon) > _solar_elevation(hass, winter_noon)
