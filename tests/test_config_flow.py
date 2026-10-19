"""Configuration ownership, recovery and lifecycle regressions."""

from __future__ import annotations

from calendar import monthrange
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foxess import config_flow, sensor
from custom_components.foxess import coordinator as polling
from custom_components.foxess.capabilities import CORE, VERSION, make_record


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
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 10)
    cloud_request.side_effect = UpdateFailed("Temporary outage")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_reload_and_unload_stop_old_coordinator(hass, entry, cloud_request, monkeypatch):
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 10)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first = entry.runtime_data[0].coordinator
    registry = er.async_get(hass)
    before = {
        entity.entity_id: entity.unique_id for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"redetect": False, "Evo": False, "wake_elevation": 5, "wake_grace_minutes": 0}
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


async def test_yaml_import_adopts_renamed_disabled_entity_without_changing_identity(hass, cloud_request, monkeypatch):
    registry = er.async_get(hass)
    retained = registry.async_get_or_create("sensor", "foxess", "LEGACYstatus")
    retained = registry.async_update_entity(
        retained.entity_id, new_entity_id="sensor.roof_status", disabled_by=er.RegistryEntryDisabler.USER
    )
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    await sensor.async_setup_platform(
        hass, {"apiKey": "example-key", "deviceSN": "DEV1", "deviceID": "LEGACY", "name": "Roof"}, lambda _: None
    )
    await hass.async_block_till_done()
    imported = hass.config_entries.async_entries("foxess")[0]
    assert imported.data["deviceID"] == "LEGACY"
    assert imported.unique_id == "DEV1"
    assert registry.async_get("sensor.roof_status").config_entry_id == imported.entry_id
    assert registry.async_get("sensor.roof_status").disabled_by is er.RegistryEntryDisabler.USER
    await hass.config_entries.async_unload(imported.entry_id)


async def test_yaml_sensor_setup_imports_without_a_second_operational_platform(hass, cloud_request, monkeypatch):
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    registry = er.async_get(hass)
    retained = registry.async_get_or_create("sensor", "foxess", "LEGACYstatus")
    registry.async_update_entity(retained.entity_id, new_entity_id="sensor.roof_status")
    assert await async_setup_component(
        hass,
        "sensor",
        {
            "sensor": [
                {
                    "platform": "foxess",
                    "apiKey": "example-key",
                    "deviceSN": "DEV1",
                    "deviceID": "LEGACY",
                    "name": "Roof",
                }
            ],
        },
    )
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries("foxess")
    assert len(entries) == 1
    try:
        assert entries[0].source == "import"
        assert entries[0].state is ConfigEntryState.LOADED
        assert registry.async_get("sensor.roof_status").config_entry_id == entries[0].entry_id
        assert not [
            item for item in registry.entities.values() if item.platform == "foxess" and item.config_entry_id is None
        ]
    finally:
        await hass.config_entries.async_unload(entries[0].entry_id)


async def test_completed_detection_prunes_only_known_owned_superseded_entities(hass, entry, cloud_request, monkeypatch):
    registry = er.async_get(hass)
    obsolete = registry.async_get_or_create("sensor", "foxess", "LEGACYpv3-power", config_entry=entry)
    retained = registry.async_get_or_create("sensor", "foxess", "LEGACYstatus", config_entry=entry)
    retained = registry.async_update_entity(retained.entity_id, new_entity_id="sensor.roof_status", name="My inverter")
    foreign = registry.async_get_or_create("sensor", "foxess", "OTHERpv3-power", config_entry=entry)
    helper = registry.async_get_or_create("sensor", "integration", "LEGACYsolar")
    record = make_record(CORE, "complete", {})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "capabilities": record})
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(obsolete.entity_id) is None
    assert registry.async_get("sensor.roof_status").name == "My inverter"
    assert registry.async_get(foreign.entity_id) is foreign
    assert registry.async_get(helper.entity_id) is helper
    await hass.config_entries.async_unload(entry.entry_id)


async def test_existing_ui_entry_adopts_only_its_legacy_orphans(hass, entry, cloud_request, monkeypatch):
    registry = er.async_get(hass)
    orphan = registry.async_get_or_create("sensor", "foxess", "LEGACYbat-soc")
    registry.async_update_entity(orphan.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    foreign = registry.async_get_or_create("sensor", "foxess", "OTHERbat-soc")
    helper = registry.async_get_or_create("sensor", "integration", "LEGACYbat-soc")
    other_entry = MockConfigEntry(
        domain="foxess",
        unique_id="OTHER",
        data={"deviceID": "OTHER"},
        disabled_by=ConfigEntryDisabler.USER,
    )
    other_entry.add_to_hass(hass)
    owned_elsewhere = registry.async_get_or_create("sensor", "foxess", "LEGACYbat-soc1", config_entry=other_entry)
    retained = registry.async_get_or_create("sensor", "foxess", "LEGACYstatus")
    registry.async_update_entity(
        retained.entity_id,
        new_entity_id="sensor.my_inverter_status",
        name="My inverter",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    record = make_record(CORE, "complete", {})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "capabilities": record})
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await hass.async_block_till_done()
        assert registry.async_get(orphan.entity_id) is None
        adopted = registry.async_get("sensor.my_inverter_status")
        assert adopted.config_entry_id == entry.entry_id
        assert adopted.disabled_by is er.RegistryEntryDisabler.USER
        assert adopted.name == "My inverter"
        assert registry.async_get(foreign.entity_id) is foreign
        assert registry.async_get(helper.entity_id) is helper
        assert registry.async_get(owned_elsewhere.entity_id).config_entry_id == other_entry.entry_id
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_redetection_does_not_change_capabilities_or_prune(hass, entry, cloud_request):
    record = make_record(CORE | {"pv3-power"}, "complete", {})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "capabilities": record})
    entity = er.async_get(hass).async_get_or_create("sensor", "foxess", "LEGACYpv3-power", config_entry=entry)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"redetect": True, "Evo": False})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "detection_inconclusive"
    assert entry.data["capabilities"] == record
    assert er.async_get(hass).async_get(entity.entity_id) is entity


async def test_policy_upgrade_preserves_previous_selection_when_history_fails(hass, entry, cloud_request, monkeypatch):
    record = make_record(CORE | {"load-power", "estimated-load"}, "complete", {})
    record["version"] = VERSION - 1
    hass.config_entries.async_update_entry(entry, data={**entry.data, "capabilities": record})
    detect = AsyncMock(side_effect=UpdateFailed("History unavailable"))
    monkeypatch.setattr(sensor, "async_detect", detect)
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await hass.async_block_till_done()
        detect.assert_awaited_once()
        assert entry.data["capabilities"]["version"] == VERSION
        assert entry.data["capabilities"]["outcome"] == "failed"
        assert entry.data["capabilities"]["entities"] == record["entities"]
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_successful_redetection_persists_then_reloads_and_preserves_retained_ids(
    hass, entry, cloud_request, monkeypatch
):
    record = make_record(CORE | {"pv3-power"}, "complete", {})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "capabilities": record})
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: 15)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    previous = entry.runtime_data[0].coordinator
    registry = er.async_get(hass)
    retained = registry.async_get_entity_id("sensor", "foxess", "LEGACYstatus")
    obsolete = registry.async_get_entity_id("sensor", "foxess", "LEGACYpv3-power")
    selection = make_record(CORE, "complete", {})
    monkeypatch.setattr(config_flow, "async_detect", AsyncMock(return_value=selection))
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"redetect": True, "Evo": False})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["capabilities"] == selection
    assert previous._shutdown_requested
    assert registry.async_get_entity_id("sensor", "foxess", "LEGACYstatus") == retained
    assert registry.async_get(obsolete) is None
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("when", "previous_total"),
    [
        ("2026-10-19T13:30:00Z", 9.5),
        ("2026-10-19T13:30:00Z", 0),
        ("2026-10-19T13:30:00Z", None),
        ("2026-10-31T13:30:00Z", 9.5),
        ("2026-12-31T13:30:00Z", 9.5),
        ("2026-10-03T16:30:00Z", 9.5),
    ],
)
async def test_redetection_matches_counter_reports_to_history_days(
    hass, entry, cloud_request, monkeypatch, freezer, when, previous_total
):
    await hass.config.async_set_time_zone("Australia/Sydney")
    freezer.move_to(when)
    previous_day = dt_util.now().date() - timedelta(days=1)
    active_at = dt_util.start_of_local_day(previous_day) + timedelta(hours=12)
    original = cloud_request.side_effect

    async def respond(*args, **kwargs):
        if args[2].endswith("/history/query"):
            return [
                {
                    "deviceSN": "DEV1",
                    "datas": [
                        {
                            "variable": variable,
                            "data": [{"time": active_at.strftime("%Y-%m-%d %H:%M:%S %z"), "value": value}],
                        }
                        for variable, value in (("RVolt", 230), ("loadsPower", 1), ("loads", 0))
                    ],
                }
            ]
        if args[2].endswith("/report/query"):
            year, month = kwargs["payload"]["year"], kwargs["payload"]["month"]
            values = [0] * monthrange(year, month)[1]
            if (year, month) == (previous_day.year, previous_day.month):
                values[previous_day.day - 1] = previous_total
            return [{"variable": "loads", "values": values}]
        return await original(*args, **kwargs)

    cloud_request.side_effect = respond
    monkeypatch.setattr(polling, "_solar_elevation", lambda hass, when: -10)
    record = make_record(CORE | {"load-power", "load"}, "complete", {})
    hass.config_entries.async_update_entry(entry, data={**entry.data, "capabilities": record})
    registry = er.async_get(hass)
    counter = registry.async_get_or_create("sensor", "foxess", "LEGACYload", config_entry=entry)
    registry.async_update_entity(counter.entity_id, name="House energy")
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await hass.async_block_till_done()
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"redetect": True, "Evo": False})
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY
        selected = entry.data["capabilities"]["entities"]
        if previous_total == 0:
            assert "load" not in selected
            assert "estimated-load" in selected
            assert registry.async_get(counter.entity_id) is None
        else:
            assert "load" in selected
            assert "estimated-load" not in selected
            assert registry.async_get(counter.entity_id).name == "House energy"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
