"""Coordinator, API and measurement regressions."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, PlatformNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.foxess import api, sensor


async def test_multiple_inverters_keep_options_and_counters(setup_inverter, cloud_request):
    first, entities = await setup_inverter(Use_V1_Api=False, Restrict=True)
    second, _ = await setup_inverter(deviceSN="DEV2", deviceID="SECOND", Evo=True)
    cloud_request.reset_mock()
    for _ in range(5):
        await first.async_refresh()
        await second.async_refresh()
    calls = [call for call in cloud_request.await_args_list if call.args[2].endswith("/real/query")]
    assert any(call.args[2] == "/op/v0/device/real/query" and "variables" in call.kwargs["payload"] for call in calls)
    assert any(
        call.args[2] == "/op/v1/device/real/query" and call.kwargs["payload"] == {"sns": ["DEV2"]} for call in calls
    )
    assert first.last_update_success and second.last_update_success
    assert all(entity.unique_id.startswith("LEGACY") for entity in entities)


async def test_initial_failure_retries_without_guessing_battery(setup_inverter, cloud_request, cloud_device):
    original = cloud_request.side_effect
    cloud_request.side_effect = UpdateFailed("Temporary outage")
    with pytest.raises(PlatformNotReady):
        await setup_inverter()
    cloud_request.side_effect = original
    cloud_device["hasBattery"] = True
    coordinator, entities = await setup_inverter()
    assert coordinator.last_update_success
    assert any(isinstance(entity, sensor.FoxESSBatSoC) for entity in entities)


async def test_auxiliary_failure_does_not_mark_inverter_offline(setup_inverter, cloud_request):
    original = cloud_request.side_effect

    async def respond(*args, **kwargs):
        if args[2].endswith("/report/query"):
            raise UpdateFailed("Report unavailable")
        return await original(*args, **kwargs)

    cloud_request.side_effect = respond
    coordinator, _ = await setup_inverter()
    assert coordinator.last_update_success
    assert coordinator.data["online"]
    assert coordinator.data["report"] == {}
    assert coordinator.data["reportDailyGeneration"]["cumulative"] == 100


async def test_daylight_grace_excludes_night_and_retains_raised_issue(
    hass,
    setup_inverter,
    cloud_device,
    cloud_request,
    monkeypatch,
    freezer,
):
    cloud_device["status"] = 3
    start = dt_util.utcnow()
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: -10)
    coordinator, _ = await setup_inverter()
    registry = ir.async_get(hass)
    issue_id = "data_unavailable_DEV1"
    assert coordinator.data["operational_state"] == "asleep"
    assert registry.async_get_issue("foxess", issue_id) is None
    cloud_request.reset_mock()
    freezer.move_to(start + timedelta(hours=12))
    await coordinator.async_refresh()
    cloud_request.assert_not_awaited()

    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: 10)
    await coordinator.async_refresh()
    freezer.move_to(start + timedelta(hours=12, minutes=59))
    await coordinator.async_refresh()
    assert registry.async_get_issue("foxess", issue_id) is None
    freezer.move_to(start + timedelta(hours=13))
    await coordinator.async_refresh()
    issue = registry.async_get_issue("foxess", issue_id)
    assert issue is not None

    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: -10)
    freezer.move_to(start + timedelta(hours=24))
    await coordinator.async_refresh()
    assert registry.async_get_issue("foxess", issue_id) is issue
    restarted, _ = await setup_inverter()
    assert registry.async_get_issue("foxess", issue_id) is issue
    cloud_device["status"] = 1
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: 10)
    await restarted.async_refresh()
    assert registry.async_get_issue("foxess", issue_id) is None


async def test_partial_daylight_timer_restarts_next_morning(
    hass,
    setup_inverter,
    cloud_device,
    monkeypatch,
    freezer,
):
    cloud_device["status"] = 3
    start = dt_util.utcnow()
    coordinator, _ = await setup_inverter()
    freezer.move_to(start + timedelta(minutes=50))
    await coordinator.async_refresh()
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: -10)
    await coordinator.async_refresh()
    freezer.move_to(start + timedelta(days=1))
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: 10)
    await coordinator.async_refresh()
    freezer.move_to(start + timedelta(days=1, minutes=10))
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue("foxess", "data_unavailable_DEV1") is None
    freezer.move_to(start + timedelta(days=1, minutes=60))
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue("foxess", "data_unavailable_DEV1") is not None


@pytest.mark.parametrize("persistent", [False, True])
async def test_repair_survives_storage_reload_and_migrates_legacy_issue(
    hass,
    setup_inverter,
    cloud_device,
    monkeypatch,
    persistent,
):
    registry = ir.async_get(hass)
    ir.async_create_issue(
        hass,
        "foxess",
        "data_unavailable_DEV1",
        is_fixable=False,
        is_persistent=persistent,
        severity=ir.IssueSeverity.WARNING,
        translation_key="data_unavailable",
    )
    restored = ir.IssueRegistry(hass)
    monkeypatch.setattr(restored._store, "async_load", AsyncMock(return_value=registry._data_to_save()))
    await restored.async_load()
    assert restored.async_get_issue("foxess", "data_unavailable_DEV1").active is persistent
    monkeypatch.setattr(ir, "async_get", lambda hass: restored)
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: -10)
    cloud_device["status"] = 3
    await setup_inverter()
    issue = restored.async_get_issue("foxess", "data_unavailable_DEV1")
    assert issue.active and issue.is_persistent
    next_restart = ir.IssueRegistry(hass)
    monkeypatch.setattr(next_restart._store, "async_load", AsyncMock(return_value=restored._data_to_save()))
    await next_restart.async_load()
    assert next_restart.async_get_issue("foxess", "data_unavailable_DEV1").active


@pytest.mark.parametrize(("battery", "grace"), [(True, 60), (False, 0)])
async def test_battery_night_outage_and_zero_grace(
    hass,
    setup_inverter,
    cloud_device,
    monkeypatch,
    freezer,
    battery,
    grace,
):
    cloud_device.update(status=3, hasBattery=battery)
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: -10 if battery else 10)
    coordinator, _ = await setup_inverter(wake_grace_minutes=grace)
    freezer.tick(timedelta(minutes=grace))
    await coordinator.async_refresh()
    issue = ir.async_get(hass).async_get_issue("foxess", "data_unavailable_DEV1")
    assert issue is not None
    assert issue.translation_key == ("data_unavailable_battery" if battery else "data_unavailable")


async def test_transport_failure_does_not_offload_or_advance_sync(
    setup_inverter,
    cloud_request,
    monkeypatch,
    freezer,
):
    coordinator, _ = await setup_inverter()
    sample_time = coordinator.data["last_cloud_sync"]
    cloud_request.side_effect = UpdateFailed("Connection failed")
    monkeypatch.setattr(sensor, "_solar_elevation", lambda hass, when: -10)
    for _ in range(10):
        freezer.tick(timedelta(minutes=1))
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data["last_cloud_sync"] == sample_time
    assert coordinator.data["raw"] == {}
    assert cloud_request.await_count > 4


async def test_failed_poll_retries_after_five_ticks(setup_inverter, cloud_request):
    coordinator, _ = await setup_inverter()
    cloud_request.side_effect = UpdateFailed("FoxESS request limit reached")
    for _ in range(5):
        await coordinator.async_refresh()
    cloud_request.reset_mock()
    for _ in range(4):
        await coordinator.async_refresh()
    cloud_request.assert_not_awaited()
    await coordinator.async_refresh()
    cloud_request.assert_awaited_once()


async def test_report_freshness_uses_its_own_cadence(setup_inverter, freezer):
    coordinator, _ = await setup_inverter()
    coordinator.data["report"] = {"feedin": 5}
    freezer.tick(timedelta(minutes=7))
    await coordinator.async_refresh()
    assert not coordinator.data["online"]
    assert coordinator.data["report"] == {"feedin": 5}
    assert coordinator.data["reportDailyGeneration"]["cumulative"] == 100
    freezer.tick(timedelta(minutes=10))
    await coordinator.async_refresh()
    assert coordinator.data["report"] == {}
    assert coordinator.data["reportDailyGeneration"]["cumulative"] == 100


async def test_accepted_sample_stays_available_between_scheduled_polls(
    setup_inverter,
    cloud_request,
    freezer,
):
    original = cloud_request.side_effect

    async def respond(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[2].endswith("/real/query"):
            result[0]["time"] = (dt_util.utcnow() - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S GMT%z")
        return result

    cloud_request.side_effect = respond
    coordinator, _ = await setup_inverter()
    first_sample = coordinator.data["last_cloud_sync"]
    for _ in range(4):
        freezer.tick(timedelta(minutes=1))
        await coordinator.async_refresh()
        assert coordinator.data["online"]
        assert coordinator.data["raw"]["pvPower"] == 1.0
        assert coordinator.data["last_cloud_sync"] == first_sample
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_refresh()
    assert coordinator.data["last_cloud_sync"] > first_sample


async def test_null_and_missing_reports_never_reset_energy(hass, cloud_request, freezer):
    freezer.move_to("2026-09-07T12:00:00+00:00")
    data = {"report": {"feedin": 4}, "reportDailyGeneration": {"cumulative": 100}}
    cloud_request.side_effect = [
        [{"variable": "feedin", "values": [1] * 6 + [None]}, {"variable": "loads", "values": [1] * 6 + [0]}],
        {"today": None, "month": 0},
    ]
    await sensor.getReport(hass, data, "example-key", "DEV1")
    await sensor.getReportDailyGeneration(hass, data, "example-key", "DEV1")
    assert data["report"] == {"loads": 0}
    assert data["reportDailyGeneration"] == {"month": 0}


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(0.2, "kWh", 0.2), (50, "kWh", 50), (5, "0.1kWh", 0.5), (0, "1.0kWh", 0), (2, "unsupported", None)],
)
async def test_raw_normalisation_and_missing_fields(hass, cloud_request, value, unit, expected):
    data = {"raw": {"obsolete": 3, "energyThroughput": 100}, "hasBattery": True}
    cloud_request.return_value = [
        {
            "deviceSN": "DEV1",
            "time": dt_util.utcnow().strftime("%Y-%m-%d %H:%M:%S GMT%z"),
            "datas": [
                {"variable": "pvPower", "value": 0},
                {"variable": "energyThroughput", "value": None},
                {"variable": "ResidualEnergy", "value": value, "unit": unit},
                {"variable": "PowerFactor", "value": "0.95"},
                {"variable": "currentFaultCount", "value": "0"},
                {"variable": "currentFault", "value": "[]"},
            ],
        }
    ]
    cloud_request.side_effect = None
    await sensor.getRaw(hass, data, "example-key", "DEV1")
    assert data["raw"].get("ResidualEnergy") == expected
    assert data["raw"]["pvPower"] == 0
    assert "energyThroughput" not in data["raw"]
    assert "obsolete" not in data["raw"]
    assert data["raw"]["PowerFactor"] == 0.95
    assert data["raw"]["currentFaultCount"] == 0
    assert "currentFault" not in data["raw"]


@pytest.mark.parametrize("timestamp", ["malformed", "2020-01-01 00:00:00 GMT+0000"])
async def test_bad_or_stale_timestamp_does_not_update_data(hass, cloud_request, timestamp):
    data = {"raw": {"pvPower": 1}, "hasBattery": False, "last_cloud_sync": None}
    cloud_request.side_effect = None
    cloud_request.return_value = [{"time": timestamp, "datas": [{"variable": "pvPower", "value": 2}]}]
    with pytest.raises(UpdateFailed):
        await sensor.getRaw(hass, data, "example-key", "DEV1")
    assert data["last_cloud_sync"] is None
    assert data["raw"] == {"pvPower": 1}


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-07 12:00:00 GMT+1000",
        "2026-09-07 12:00:00 AEST+1000",
        "2026-09-07 13:00:00 AEDT+1100",
        "2026-09-06 22:00:00 GMT-04:00",
        "2026-09-07 07:30:00 IST+0530",
    ],
)
async def test_cloud_timestamp_uses_explicit_offset(hass, cloud_request, freezer, timestamp):
    freezer.move_to("2026-09-07T02:00:00+00:00")
    cloud_request.side_effect = None
    cloud_request.return_value = [
        {
            "time": timestamp,
            "datas": [{"variable": "pvPower", "value": 1}],
        }
    ]
    data = {"hasBattery": False}
    await sensor.getRaw(hass, data, "example-key", "DEV1")
    assert data["last_cloud_sync"] == dt_util.utcnow()


async def test_evo_selects_requested_device_across_pages(hass, cloud_request, cloud_device):
    cloud_request.side_effect = [
        {"currentPage": 1, "pageSize": 10, "total": 11, "data": [{"deviceSN": "OTHER", **cloud_device}] * 10},
        {"currentPage": 2, "pageSize": 10, "total": 11, "data": [{"deviceSN": "TARGET", **cloud_device}]},
    ]
    details = await api.async_device_details(hass, "example-key", "TARGET", evo=True)
    assert details["deviceSN"] == "TARGET"
    assert cloud_request.await_args.kwargs["payload"]["currentPage"] == 2
    cloud_request.side_effect = None
    cloud_request.return_value = {"currentPage": 1, "pageSize": 10, "total": 0, "data": []}
    with pytest.raises(api.DeviceNotFound):
        await api.async_device_details(hass, "example-key", "TARGET", evo=True)


@pytest.fixture
def http_client(monkeypatch):
    response = AsyncMock()
    response.status = 200
    response.json.return_value = {"errno": 0, "result": {}}
    session = MagicMock()
    session.request.return_value.__aenter__.return_value = response
    monkeypatch.setattr(api, "async_get_clientsession", lambda hass: session)
    return session, response


@pytest.mark.parametrize(
    ("status", "body", "exception"),
    [
        (401, {}, ConfigEntryAuthFailed),
        (503, {}, UpdateFailed),
        (429, {}, UpdateFailed),
        (200, {"errno": 41809}, ConfigEntryAuthFailed),
        (200, {"errno": 40256}, UpdateFailed),
        (200, {"errno": 40257}, UpdateFailed),
        (200, {"errno": 40400}, UpdateFailed),
        (200, {"errno": 40261}, api.DeviceNotFound),
        (200, [], UpdateFailed),
        (200, {"errno": 0, "result": None}, UpdateFailed),
    ],
)
async def test_api_classifies_failures(hass, http_client, status, body, exception):
    _, response = http_client
    response.status = status
    response.json.return_value = body
    with pytest.raises(exception) as caught:
        await api.async_request(hass, "GET", "/example", "example-key")
    if status == 429 or (isinstance(body, dict) and body.get("errno") == 40400):
        assert caught.value.retry_after is None


async def test_api_rejects_invalid_json_and_transport_failure(hass, http_client):
    session, response = http_client
    response.json.side_effect = ValueError("Invalid JSON")
    with pytest.raises(UpdateFailed, match="invalid JSON"):
        await api.async_request(hass, "GET", "/example", "example-key")
    session.request.side_effect = TimeoutError
    with pytest.raises(UpdateFailed, match="Unable to connect"):
        await api.async_request(hass, "GET", "/example", "example-key")


async def test_shared_request_gate_and_verified_https(hass, http_client):
    session, _ = http_client
    context = session.request.return_value
    starts = []

    def request(*args, **kwargs):
        starts.append(asyncio.get_running_loop().time())
        assert kwargs.get("ssl") is not False
        assert kwargs["timeout"].total == 75
        return context

    session.request.side_effect = request
    await api.async_request(hass, "GET", "/example", "example-key")
    await asyncio.sleep(0.1)
    responses = await asyncio.gather(
        api.async_request(hass, "GET", "/example", "example-key"),
        api.async_request(hass, "GET", "/example", "another-example-key"),
    )
    assert all(right - left >= 0.998 for left, right in zip(starts, starts[1:], strict=False))
    assert all(elapsed_ms < 500 for _, elapsed_ms in responses)
