"""Evidence-based entity selection.  Catalogue presence alone is not hardware support."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .api import async_request, number, sample_timestamp

VERSION = 1
CORE = {
    "Inverter",
    "status",
    "running-state",
    "response-time",
    "energy-generated",
    "energy-generated-month",
    "energy-generated-cumulative",
}
RAW_ENTITIES = {
    **{
        f"pv{n}-{suffix}": f"pv{n}{key}"
        for n in range(1, 19)
        for suffix, key in (("current", "Current"), ("power", "Power"), ("volt", "Volt"))
    },
    **{
        f"{phase.lower()}-{suffix}": f"{phase}{key}"
        for phase in "RST"
        for suffix, key in (("current", "Current"), ("freq", "Freq"), ("power", "Power"), ("volt", "Volt"))
    },
    "pv-power": "pvPower",
    "meter2-power": "meterPower2",
    "reactive-power": "ReactivePower",
    "power-factor": "PowerFactor",
    "ambient-temperature": "ambientTemperation",
    "boost-temperature": "boostTemperation",
    "inv-temperature": "invTemperation",
    "-generation-power": "generationPower",
    "grid-consumption-power": "gridConsumptionPower",
    "feedIn-power": "feedinPower",
    "load-power": "loadsPower",
    "bat-temperature": "batTemperature",
    "bat-temperature2": "batTemperature_2",
    "bat-soc": "SoC",
    "bat-soc1": "SoC_1",
    "bat-soc2": "SoC_2",
    "bat-soh": "SOH",
    "inv-Bat-Power": "invBatPower",
    "inv-Bat-Power2": "invBatPower_2",
    "energy-throughput": "energyThroughput",
    "bat-discharge-power": "batDischargePower",
    "bat-charge-power": "batChargePower",
    "residual-energy": "ResidualEnergy",
    "max-bat-charge-charge": "maxChargeCurrent",
    "max-bat-discharge-charge": "maxDischargeCurrent",
}
COUNTERS = {
    "grid-consumption": ("gridConsumption", "gridConsumptionPower"),
    "feedIn": ("feedin", "feedinPower"),
    "load": ("loads", "loadsPower"),
    "PVEnergyTotal": ("PVEnergyTotal", "pvPower"),
    "bat-charge": ("chargeEnergyToTal", "batChargePower"),
    "bat-discharge": ("dischargeEnergyToTal", "batDischargePower"),
}
ESTIMATES = {
    "estimated-load": ("load-power", "load", "Estimated Load Energy"),
    "estimated-grid-import": ("grid-consumption-power", "grid-consumption", "Estimated Grid Import Energy"),
    "estimated-grid-export": ("feedIn-power", "feedIn", "Estimated Grid Export Energy"),
}
BATTERY = {
    key
    for key in RAW_ENTITIES
    if key.startswith(("bat-", "inv-Bat-", "max-bat-")) or key in ("energy-throughput", "residual-energy")
} | {"bat-minsoc", "bat-minSocOnGrid"}
KNOWN_ENTITIES = CORE | RAW_ENTITIES.keys() | COUNTERS.keys() | BATTERY | ESTIMATES.keys() | {"solar", "solar-power"}


@dataclass
class Series:
    unit: str | None
    values: dict[str, float | int] = field(default_factory=dict)


def battery_support(details: dict) -> bool | None:
    """Conflicting metadata must never authorise night offload."""
    supported = details.get("hasBattery")
    batteries = details.get("batteryList")
    if batteries and supported is False:
        return None
    return supported if isinstance(supported, bool) else None


def compatible_entities(details: dict, *, extended: bool = False) -> set[str]:
    """Preserve the old compatible selection when observations are inconclusive."""
    entities = (CORE | RAW_ENTITIES.keys() | COUNTERS.keys() | BATTERY | {"solar", "solar-power"}) - {
        f"pv{n}-{suffix}" for n in range(7, 19) for suffix in ("current", "power", "volt")
    }
    if extended:
        entities |= {f"pv{n}-{suffix}" for n in range(7, 19) for suffix in ("current", "power", "volt")}
    if battery_support(details) is False:
        entities -= BATTERY | {"bat-charge", "bat-discharge"}
    return entities


def valid_record(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and record.get("version") == VERSION
        and isinstance(record.get("entities"), list)
        and all(isinstance(key, str) and key in KNOWN_ENTITIES for key in record["entities"])
        and CORE <= set(record["entities"])
        and record.get("outcome") in ("complete", "inconclusive", "failed")
        and isinstance(record.get("reasons"), dict)
        and all(
            estimate not in record["entities"] or (source in record["entities"] and counter not in record["entities"])
            for estimate, (source, counter, _) in ESTIMATES.items()
        )
    )


def select_capabilities(
    details: dict, series: dict[str, Series], previous: set[str], report: dict[str, float | int]
) -> dict:
    """Select only from informative operating history, retaining unresolved groups."""
    selected = set(previous) | CORE
    reasons: dict[str, str] = {}

    def values(variable):
        return list(series[variable].values.values()) if variable in series else []

    def active(variable):
        return any(value != 0 for value in values(variable))

    def choose(keys, support, reason):
        if support is not None:
            selected.difference_update(keys)
            if support:
                selected.update(keys)
        for key in keys:
            reasons[key] = reason if support is not None else "inconclusive; previous selection retained"

    awake = any(active(key) for key in ("generationPower", "pvPower", "RVolt", "SVolt", "TVolt", "SoC"))
    if not awake:
        return make_record(selected, "inconclusive", {"history": "no usable operating samples"})

    solar_awake = any(active(key) for key in ("generationPower", "pvPower"))
    for n in range(1, 19):
        samples = values(f"pv{n}Volt")
        choose(
            {f"pv{n}-{suffix}" for suffix in ("current", "power", "volt")},
            max(samples) >= 80 if samples and (solar_awake or max(samples) >= 80) else None,
            "PV voltage threshold: 80 V",
        )
    for phase in "RST":
        choose(
            {f"{phase.lower()}-{suffix}" for suffix in ("current", "freq", "power", "volt")},
            active(f"{phase}Volt") if values(f"{phase}Volt") else None,
            "phase voltage",
        )
    for key, variable in (
        ("pv-power", "pvPower"),
        ("-generation-power", "generationPower"),
        ("load-power", "loadsPower"),
        ("meter2-power", "meterPower2"),
        ("ambient-temperature", "ambientTemperation"),
        ("boost-temperature", "boostTemperation"),
        ("inv-temperature", "invTemperation"),
    ):
        choose({key}, active(variable) if values(variable) else None, "informative observed series")
    grid = ("gridConsumptionPower", "feedinPower")
    choose(
        {"grid-consumption-power", "feedIn-power"},
        any(active(key) for key in grid) if all(values(key) for key in grid) else None,
        "observed grid exchange in either direction",
    )
    pf, reactive = values("PowerFactor"), values("ReactivePower")
    quality = None
    if pf and reactive:
        quality = any(value != 0 for value in reactive) or bool(set(pf) - {0, 1}) or set(pf) == {1}
    choose({"power-factor", "reactive-power"}, quality, "informative power-quality group")
    battery = battery_support(details)
    choose(BATTERY, battery, "static battery metadata")
    if battery is True:
        for suffix in ("bat-temperature", "bat-temperature2"):
            variable = RAW_ENTITIES[suffix]
            choose({suffix}, active(variable) if values(variable) else None, "informative battery temperature")
    for suffix, (counter, power) in COUNTERS.items():
        if suffix.startswith("bat-") and battery is False:
            choose({suffix}, False, "confirmed no battery")
            continue
        samples = values(counter)
        # A retained non-zero counter establishes support even in a quiet window.
        supported = True if any(value > 0 for value in samples) or report.get(counter, 0) > 0 else None
        if samples and active(power) and all(value == 0 for value in samples) and report.get(counter) == 0:
            supported = False
        choose({suffix}, supported, "counter activity checked against corresponding power")

    for redundant, preferred in (("r-power", "-generation-power"), ("boost-temperature", "inv-temperature")):
        if redundant == "r-power" and not (
            values("SVolt") and values("TVolt") and not active("SVolt") and not active("TVolt")
        ):
            continue
        left, right = series.get(RAW_ENTITIES[redundant]), series.get(RAW_ENTITIES[preferred])
        if (
            left
            and right
            and left.unit is not None
            and left.unit == right.unit
            and left.values == right.values
            and len(left.values) >= 2
            and len(set(left.values.values())) > 1
            and redundant in selected
            and preferred in selected
        ):
            choose({redundant}, False, f"aligned informative duplicate of {preferred}")
    # These legacy conservation formulae are not reported measurements.
    choose({"solar", "solar-power"}, False, "retired calculated conservation formula; not a reported measurement")
    for estimate, (source, counter, _) in ESTIMATES.items():
        choose({estimate}, source in selected and counter not in selected, "reported power without a working counter")
    return make_record(selected, "complete", reasons)


def make_record(entities: set[str], outcome: str, reasons: dict[str, str]) -> dict:
    return {
        "version": VERSION,
        "detected_at": dt_util.utcnow().isoformat(),
        "entities": sorted(entities),
        "outcome": outcome,
        "reasons": reasons,
    }


async def async_detect(hass, api_key, serial, details, previous, report) -> dict:
    """Fetch two adjacent, at-most-24-hour windows.  A failed window invalidates detection."""
    end = dt_util.utcnow()
    series: dict[str, Series] = {}
    for start, stop in ((end - timedelta(hours=48), end - timedelta(hours=24)), (end - timedelta(hours=24), end)):
        result, _ = await async_request(
            hass,
            "POST",
            "/op/v0/device/history/query",
            api_key,
            payload={"sn": serial, "begin": int(start.timestamp() * 1000), "end": int(stop.timestamp() * 1000)},
        )
        if (
            not isinstance(result, list)
            or len(result) != 1
            or not isinstance(result[0], dict)
            or result[0].get("deviceSN") != serial
            or not isinstance(result[0].get("datas"), list)
        ):
            raise UpdateFailed("FoxESS returned invalid capability history")
        for item in result[0]["datas"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("variable"), str)
                or not isinstance(item.get("data"), list)
            ):
                raise UpdateFailed("FoxESS returned invalid capability history series")
            variable = {"batTemperature_1": "batTemperature", "invBatPower_1": "invBatPower"}.get(
                item["variable"], item["variable"]
            )
            if variable not in RAW_ENTITIES.values() and variable not in {counter for counter, _ in COUNTERS.values()}:
                continue
            unit = item.get("unit")
            if unit is not None and not isinstance(unit, str):
                raise UpdateFailed("FoxESS returned an invalid history unit")
            target = series.setdefault(variable, Series(unit))
            if target.unit != unit:
                raise UpdateFailed("FoxESS changed measurement units during capability detection")
            for point in item["data"]:
                if not isinstance(point, dict):
                    raise UpdateFailed("FoxESS returned an invalid history point")
                value = number(point.get("value"))
                if value is not None:
                    timestamp = sample_timestamp(point.get("time"))
                    if start <= timestamp <= stop:
                        target.values[timestamp.isoformat()] = value
    return select_capabilities(details, series, previous, report)
