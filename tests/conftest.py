"""Shared pytest fixtures for the FoxESS integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.foxess import api, sensor


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture
def cloud_device():
    return {"status": 1, "hasBattery": False, "stationName": "Example"}


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
            return [
                {
                    "deviceSN": sn,
                    "time": dt_util.utcnow()
                    .astimezone(ZoneInfo("Australia/Sydney"))
                    .strftime("%Y-%m-%d %H:%M:%S %Z%z"),
                    "datas": [
                        {"variable": "pvPower", "value": 1.0, "unit": "kW"},
                        {"variable": "runningState", "value": "163"},
                        {"variable": "PowerFactor", "value": "0.95"},
                        {"variable": "currentFaultCount", "value": "0"},
                        {"variable": "currentFault", "value": "[]"},
                    ],
                }
            ]
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

    monkeypatch.setattr(sensor, "async_request", fetch)
    monkeypatch.setattr(api, "async_request", fetch)
    return request


@pytest.fixture
async def setup_inverter(hass, monkeypatch, cloud_request):
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: 15)
    coordinators = []

    async def setup(**options):
        config = {"name": "FoxESS", "deviceSN": "DEV1", "deviceID": "LEGACY", "apiKey": "example-key", **options}
        entities = []
        await sensor._async_setup_sensors(hass, config, entities.extend)
        coordinator = entities[0].coordinator
        coordinators.append(coordinator)
        return coordinator, entities

    yield setup
    for coordinator in coordinators:
        await coordinator.async_shutdown()
