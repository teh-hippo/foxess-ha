"""Capability selection guards against phantom and zero-padded measurements."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.foxess import capabilities
from custom_components.foxess.capabilities import (
    BATTERY,
    COUNTERS,
    RAW_ENTITIES,
    Series,
    battery_support,
    compatible_entities,
    select_capabilities,
)


@pytest.fixture
def observed_series():
    series = {
        variable: Series("kW", {"first": 0, "second": 0})
        for variable in set(RAW_ENTITIES.values()) | {counter for counter, _ in COUNTERS.values()}
    }
    for variable, values in {
        "pv1Volt": (400, 420),
        "pv2Volt": (350, 360),
        "pv3Volt": (40, 47.5),
        "pvPower": (2, 3),
        "generationPower": (2, 3),
        "RPower": (2, 3),
        "RVolt": (230, 231),
        "loadsPower": (1, 2),
        "feedinPower": (1, 0),
        "gridConsumptionPower": (0, 1),
        "ambientTemperation": (20, 21),
        "invTemperation": (30, 31),
        "boostTemperation": (30, 31),
        "PowerFactor": (0, 1),
    }.items():
        series[variable].values = dict(zip(("first", "second"), values, strict=True))
    return series


def test_solar_profile_removes_phantoms_and_supplies_estimates(observed_series):
    details = {"hasBattery": False, "batteryList": []}
    record = select_capabilities(
        details, observed_series, compatible_entities(details), {counter: 0 for counter, _ in COUNTERS.values()}
    )
    selected = set(record["entities"])
    assert {"pv1-volt", "pv1-current", "pv1-power", "pv2-volt", "r-current", "feedIn-power"} <= selected
    assert not selected & {"pv3-volt", "pv3-current", "pv3-power", "r-power", "boost-temperature", "power-factor"}
    assert not selected & BATTERY
    assert not selected & COUNTERS.keys()
    assert capabilities.ESTIMATES.keys() <= selected
    assert len(selected) == 26
    assert record["outcome"] == "complete"


def test_quiet_or_missing_evidence_preserves_support_and_never_deduplicates_zeroes(observed_series):
    previous = compatible_entities({"hasBattery": True})
    empty = select_capabilities({"hasBattery": True}, {}, previous, {})
    assert set(empty["entities"]) == previous
    assert empty["outcome"] == "inconclusive"
    for variable in ("boostTemperation", "invTemperation"):
        observed_series[variable].values = {"first": 30, "second": 30}
    observed_series["gridConsumptionPower"].values = {"first": 0, "second": 0}
    record = select_capabilities({"hasBattery": True}, observed_series, previous, {})
    assert {"boost-temperature", "inv-temperature", "grid-consumption", "grid-consumption-power"} <= set(
        record["entities"]
    )


@pytest.mark.parametrize("pf", [(1, 1), (0.95, 0.96)])
def test_battery_three_phase_and_valid_power_factor_survive(observed_series, pf):
    observed_series["SVolt"].values = {"first": 230, "second": 231}
    observed_series["TVolt"].values = {"first": 230, "second": 231}
    observed_series["PowerFactor"].values = dict(zip(("first", "second"), pf, strict=True))
    observed_series["loads"].values = {"first": 10, "second": 11}
    record = select_capabilities({"hasBattery": True}, observed_series, compatible_entities({"hasBattery": True}), {})
    assert {"s-current", "t-current", "power-factor", "reactive-power", "r-power", "load", "bat-soc"} <= set(
        record["entities"]
    )
    assert "estimated-load" not in record["entities"]
    assert battery_support({"hasBattery": False, "batteryList": [{"batterySN": "EXAMPLE"}]}) is None


def test_battery_output_is_not_evidence_of_solar_activity(observed_series):
    details = {"hasBattery": True, "hasPV": True}
    for variable in observed_series:
        if variable.startswith("pv"):
            observed_series[variable].values = {"first": 0, "second": 0}
    observed_series["SoC"].values = {"first": 50, "second": 49}
    record = select_capabilities(details, observed_series, compatible_entities(details), {})
    assert {"pv-power", "pv1-current", "pv1-volt", "pv1-power"} <= set(record["entities"])


def test_binary_percentage_power_factor_is_not_an_informative_profile(observed_series):
    observed_series["PowerFactor"] = Series("%", {"first": 0, "second": 100})
    details = {"hasBattery": True}
    record = select_capabilities(details, observed_series, compatible_entities(details), {})
    assert not {"power-factor", "reactive-power"} & set(record["entities"])


async def test_history_windows_and_failure_are_not_successful_empty_detection(hass, cloud_request):
    calls = []

    async def respond(hass, method, path, key, *, payload):
        if path.endswith("/report/query"):
            return []
        calls.append(payload)
        assert payload["end"] - payload["begin"] == timedelta(hours=24).total_seconds() * 1000
        timestamp = dt_util.utc_from_timestamp(payload["begin"] / 1000 + 1).strftime("%Y-%m-%d %H:%M:%S %z")
        return [
            {
                "deviceSN": "DEV1",
                "datas": [
                    {"variable": "PowerFactor", "data": [{"time": timestamp, "value": 0.95}]},
                    {"variable": "currentFault", "data": [{"time": timestamp, "value": "[]"}]},
                ],
            }
        ]

    cloud_request.side_effect = respond
    previous = compatible_entities({"hasBattery": False})
    record = await capabilities.async_detect(hass, "example-key", "DEV1", {"hasBattery": False}, previous)
    assert calls[0]["end"] == calls[1]["begin"]
    assert set(record["entities"]) == previous
    cloud_request.side_effect = [[], UpdateFailed("History unavailable")]
    with pytest.raises(UpdateFailed):
        await capabilities.async_detect(hass, "example-key", "DEV1", {"hasBattery": False}, previous)
