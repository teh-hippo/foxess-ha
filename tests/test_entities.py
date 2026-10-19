"""Tests for the FoxESS status entity, the night zero-leak fix, and DST-safe elevation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.foxess.coordinator import _solar_elevation
from custom_components.foxess.sensor import (
    FoxESSEnergyGenerated,
    FoxESSEnergySolar,
    FoxESSInverter,
    FoxESSSolarPower,
    FoxESSStatus,
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


@pytest.mark.parametrize("entity_type", [FoxESSEnergySolar, FoxESSSolarPower])
def test_retired_formulas_do_not_claim_reported_generation(entity_type) -> None:
    entity = _entity(
        entity_type,
        {
            "online": True,
            "report": {
                "loads": 5,
                "chargeEnergyToTal": 0,
                "feedin": 2,
                "gridConsumption": 1,
                "dischargeEnergyToTal": 0,
            },
            "raw": {
                "loadsPower": 5,
                "batChargePower": 0,
                "feedinPower": 2,
                "gridConsumptionPower": 1,
                "batDischargePower": 0,
            },
        },
    )
    assert entity.native_value is None


def test_inverter_attributes_before_detail_and_during_sleep() -> None:
    entity = _entity(FoxESSInverter, {"online": False, "addressbook": {}})
    assert entity.native_value is None
    assert entity.extra_state_attributes == {
        "lastCloudSync": None,
        "lastCloudSuccess": None,
        "capabilityDetection": None,
    }
    timestamp = datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("UTC"))
    entity.coordinator.data.update(addressbook={"status": 3}, last_cloud_sync=timestamp)
    assert entity.native_value == "off-line"
    assert entity.extra_state_attributes["lastCloudSync"] is timestamp


def test_missing_generation_is_unknown() -> None:
    coordinator = MagicMock()
    coordinator.data = {"reportDailyGeneration": {}}
    entity = FoxESSEnergyGenerated(coordinator, "FoxESS", "LEGACY", "Cumulative", "cumulative", "cumulative")
    assert entity.native_value is None


async def test_solar_elevation_is_dst_safe(hass, caplog) -> None:
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
    for local_time in (
        datetime(2026, 10, 4, 1, 59, tzinfo=SYD),
        datetime(2026, 10, 4, 3, 1, tzinfo=SYD),
        datetime(2026, 4, 5, 2, 30, tzinfo=SYD, fold=0),
        datetime(2026, 4, 5, 2, 30, tzinfo=SYD, fold=1),
    ):
        assert _solar_elevation(hass, local_time) == pytest.approx(
            _solar_elevation(hass, local_time.astimezone(ZoneInfo("UTC")))
        )
    assert not any(
        record.name == "homeassistant.helpers.sun" and "deprecated" in record.getMessage() for record in caplog.records
    )


def _energy_ids(hass):
    registry = er.async_get(hass)
    return (
        registry.async_get_entity_id("sensor", "foxess", "LEGACYload-power"),
        registry.async_get_entity_id("sensor", "foxess", "LEGACYestimated-load"),
    )


async def test_native_energy_skips_gaps_and_negative_readings(hass, energy_entry, freezer):
    source, estimate = _energy_ids(hass)
    attributes = {"device_class": "power", "unit_of_measurement": "kW"}

    async def report(value, minutes=3):
        freezer.tick(timedelta(minutes=minutes))
        hass.states.async_set(source, value, attributes)
        await hass.async_block_till_done()

    await report("1")
    assert float(hass.states.get(estimate).state) == pytest.approx(0.05)
    await report("unknown")
    await report("2", minutes=720)
    assert float(hass.states.get(estimate).state) == pytest.approx(0.05)
    await report("2")
    assert float(hass.states.get(estimate).state) == pytest.approx(0.15)
    await report("-1")
    assert hass.states.get(source).state == "-1"
    assert hass.states.get(estimate).attributes["invalid_source_reading"]
    await report("2")
    assert float(hass.states.get(estimate).state) == pytest.approx(0.15)
    await report("2")
    assert float(hass.states.get(estimate).state) == pytest.approx(0.25)


@pytest.mark.parametrize("power", [0, 1])
async def test_estimate_restores_positive_and_real_zero_without_restart_energy(hass, energy_entry, freezer, power):
    source, estimate = _energy_ids(hass)
    attributes = {"device_class": "power", "unit_of_measurement": "kW"}
    hass.states.async_set(source, str(power), attributes)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(source, str(power), attributes)
    await hass.async_block_till_done()
    total = hass.states.get(estimate).state
    assert Decimal(total) == Decimal(str(power)) / 20
    await hass.config_entries.async_unload(energy_entry.entry_id)
    freezer.tick(timedelta(hours=12))
    assert await hass.config_entries.async_setup(energy_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(estimate).state == total
    assert len(hass.config_entries.async_entries("integration")) == 0
    assert er.async_get(hass).async_get(source).device_id == er.async_get(hass).async_get(estimate).device_id


async def test_estimate_tracks_source_rename_disable_and_removal(hass, energy_entry, freezer):
    source, estimate = _energy_ids(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(source, new_entity_id="sensor.renamed_house_power")
    await hass.async_block_till_done()
    assert hass.states.get(estimate).attributes["source"] == "sensor.renamed_house_power"
    registry.async_update_entity("sensor.renamed_house_power", disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert hass.states.get(estimate).state == "unavailable"
    registry.async_remove("sensor.renamed_house_power")
    await hass.async_block_till_done()
    assert hass.states.get(estimate).state == "unavailable"


async def test_cached_ticks_are_not_integral_samples(hass, energy_entry, freezer):
    source, estimate = _energy_ids(hass)
    coordinator = energy_entry.runtime_data[0].coordinator
    reported = hass.states.get(source).last_reported
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(source).last_reported == reported
    assert hass.states.get(estimate).state == "unknown"


async def test_estimate_normalises_configured_power_units(hass, energy_entry, freezer):
    source, estimate = _energy_ids(hass)
    for _ in range(2):
        freezer.tick(timedelta(minutes=3))
        hass.states.async_set(source, "1000", {"device_class": "power", "unit_of_measurement": "W"})
        await hass.async_block_till_done()
    assert hass.states.get(estimate).attributes["unit_of_measurement"] == "kWh"
    assert float(hass.states.get(estimate).state) == pytest.approx(0.1)
