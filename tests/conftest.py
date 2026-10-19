"""Shared pytest fixtures for the FoxESS integration tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foxess import api, capabilities, sensor
from custom_components.foxess import coordinator as polling


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture
def cloud_device():
    return {"status": 1, "hasBattery": False, "stationName": "Example"}


@pytest.fixture
def inverter_entry(hass):
    entry = MockConfigEntry(
        domain="foxess",
        unique_id="DEV1",
        data={"apiKey": "example-key", "deviceSN": "DEV1", "deviceID": "LEGACY", "name": "FoxESS", "hasBattery": False},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def energy_record():
    return capabilities.make_record(
        capabilities.CORE | {"load-power", "grid-consumption-power", "feedIn-power"} | capabilities.ESTIMATES.keys(),
        "complete",
        {},
    )


@pytest.fixture
async def energy_entry(hass, inverter_entry, cloud_request, monkeypatch, energy_record):
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    record = energy_record
    hass.config_entries.async_update_entry(inverter_entry, data={**inverter_entry.data, "capabilities": record})
    assert await hass.config_entries.async_setup(inverter_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = inverter_entry.runtime_data[0].coordinator
    coordinator._async_unsub_refresh()
    if coordinator._expiry_cancel:
        coordinator._expiry_cancel()
        coordinator._expiry_cancel = None
    yield inverter_entry
    await hass.config_entries.async_unload(inverter_entry.entry_id)


@pytest.fixture
def cloud_request(monkeypatch, cloud_device):
    async def respond(hass, method, path, api_key, *, params=None, payload=None):
        if path.endswith("/device/list"):
            return {
                "currentPage": 1,
                "pageSize": 10,
                "total": 2,
                "data": [{"deviceSN": sn, **cloud_device} for sn in ("DEV1", "DEV2")],
            }
        sn = params["sn"] if params else payload.get("sn", payload.get("sns", ["DEV1"])[0])
        if path.endswith("/device/detail"):
            return {"deviceSN": sn, **cloud_device}
        if path.endswith("/real/query"):
            return (
                [
                    {
                        "deviceSN": sn,
                        "time": dt_util.utcnow()
                        .astimezone(ZoneInfo("Australia/Sydney"))
                        .strftime("%Y-%m-%d %H:%M:%S %Z%z"),
                        "datas": [
                            {"variable": "pvPower", "value": 1.0, "unit": "kW"},
                            {"variable": "generationPower", "value": 1.0, "unit": "kW"},
                            {"variable": "loadsPower", "value": 1.0, "unit": "kW"},
                            {"variable": "feedinPower", "value": 0.0, "unit": "kW"},
                            {"variable": "gridConsumptionPower", "value": 0.0, "unit": "kW"},
                            {"variable": "runningState", "value": "163"},
                            {"variable": "PowerFactor", "value": "0.95"},
                            {"variable": "currentFaultCount", "value": "0"},
                            {"variable": "currentFault", "value": "[]"},
                        ],
                    }
                ]
                if cloud_device["status"] != 3
                else [
                    {
                        "deviceSN": sn,
                        "time": (dt_util.utcnow() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S %z"),
                        "datas": [{"variable": "runningState", "value": "163"}],
                    }
                ]
            )
        if path.endswith("/history/query"):
            return [{"deviceSN": sn, "datas": []}]
        if path.endswith("/report/query"):
            return []
        if path.endswith("/generation"):
            return {"today": 1, "month": 10, "cumulative": 100}
        if path.endswith("/soc/get"):
            return {"minSoc": 10, "minSocOnGrid": 20}
        raise AssertionError(f"Unexpected endpoint: {path}")

    request = AsyncMock(side_effect=respond)

    async def fetch(*args, **kwargs):
        return await request(*args, **kwargs), 0

    monkeypatch.setattr(polling, "async_request", fetch)
    monkeypatch.setattr(capabilities, "async_request", fetch)
    monkeypatch.setattr(api, "async_request", fetch)
    return request


@pytest.fixture
async def setup_inverter(hass, monkeypatch, cloud_request):
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    coordinators = []

    async def setup(**options):
        config = {"name": "FoxESS", "deviceSN": "DEV1", "deviceID": "LEGACY", "apiKey": "example-key", **options}
        entities = []
        entry = MockConfigEntry(domain="foxess", unique_id=config["deviceSN"], data=config)
        entry.add_to_hass(hass)
        coordinator = polling.FoxESSCoordinator(hass, entry, config)
        coordinators.append(coordinator)
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            raise ConfigEntryNotReady from coordinator.last_exception
        entities = sensor._create_sensors(coordinator, config["name"], config["deviceID"])
        return coordinator, entities

    yield setup
    for coordinator in coordinators:
        await coordinator.async_shutdown()
