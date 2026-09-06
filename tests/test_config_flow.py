"""Configuration ownership, recovery and lifecycle regressions."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foxess import sensor


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="foxess",
        unique_id="DEV1",
        data={
            "apiKey": "example-key",
            "deviceSN": "DEV1",
            "deviceID": "LEGACY",
            "name": "FoxESS",
            "hasBattery": False,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("evo", [False, True])
async def test_distinct_ui_device_and_duplicate_detection(hass, entry, cloud_request, evo):
    er.async_get(hass).async_get_or_create("sensor", "foxess", "LEGACYstatus", config_entry=entry)
    hass.states.async_set("sensor.foxess_status", "online")
    result = await hass.config_entries.flow.async_init("foxess", context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    duplicate = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"deviceSN": "DEV1", "apiKey": "example-key"}
    )
    assert duplicate["reason"] == "already_configured"
    cloud_request.assert_not_awaited()
    result = await hass.config_entries.flow.async_init("foxess", context={"source": "user"})
    created = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"deviceSN": "DEV2", "apiKey": "example-key", "Evo": evo}
    )
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["data"]["deviceSN"] == "DEV2"
    assert created["options"]["Evo"] is evo
    expected_path = "/op/v0/device/list" if evo else "/op/v1/device/detail"
    assert cloud_request.await_args_list[0].args[2] == expected_path
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(created["result"].entry_id)


async def test_renamed_yaml_entities_are_detected(hass, cloud_request):
    entity = er.async_get(hass).async_get_or_create("sensor", "foxess", "LEGACYstatus")
    er.async_get(hass).async_update_entity(entity.entity_id, new_entity_id="sensor.roof_inverter")
    result = await hass.config_entries.flow.async_init("foxess", context={"source": "user"})
    assert result["reason"] == "yaml_in_use"
    cloud_request.assert_not_awaited()


async def test_malformed_capabilities_are_not_saved_as_solar_only(hass, cloud_request):
    cloud_request.side_effect = None
    cloud_request.return_value = {"deviceSN": "DEV2", "status": 1}
    result = await hass.config_entries.flow.async_init("foxess", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"deviceSN": "DEV2", "apiKey": "example-key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("evo", [False, True])
async def test_reauth_preserves_config_entry_and_legacy_ids(hass, entry, cloud_request, monkeypatch, evo):
    hass.config_entries.async_update_entry(entry, options={"Evo": evo})
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    result = await hass.config_entries.flow.async_init(
        "foxess", context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
    )
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"apiKey": "replacement-key"})
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["apiKey"] == "replacement-key"
    assert entry.data["deviceID"] == "LEGACY"
    assert entry.unique_id == "DEV1"
    assert len(hass.config_entries.async_entries("foxess")) == 1
    reload_entry.assert_awaited_once_with(entry.entry_id)
    assert cloud_request.await_args.args[2] == ("/op/v0/device/list" if evo else "/op/v1/device/detail")


async def test_initial_entry_failure_is_retryable(hass, entry, cloud_request, monkeypatch):
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: 10)
    cloud_request.side_effect = UpdateFailed("Temporary outage")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_reload_and_unload_stop_old_coordinator(hass, entry, cloud_request, monkeypatch):
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: 10)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first = entry.runtime_data[0].coordinator
    registry = er.async_get(hass)
    before = {
        entity.entity_id: entity.unique_id for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"extendPV": False, "Evo": False, "wake_elevation": 5, "wake_grace_minutes": 0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert first._shutdown_requested
    assert entry.runtime_data[0].coordinator is not first
    assert {
        entity.entity_id: entity.unique_id for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    } == before
    current = entry.runtime_data[0].coordinator
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert current._shutdown_requested


@pytest.mark.parametrize(
    ("key", "value"), [("wake_grace_minutes", -1), ("wake_grace_minutes", 181), ("wake_elevation", 16)]
)
async def test_wake_bounds_match_yaml_and_options(hass, entry, key, value):
    with pytest.raises(vol.Invalid):
        sensor.PLATFORM_SCHEMA({"platform": "foxess", **entry.data, key: value})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        result["data_schema"]({key: value})
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {key: value})
