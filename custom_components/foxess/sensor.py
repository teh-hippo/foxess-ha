from __future__ import annotations

import logging
import math
from collections import namedtuple
from datetime import datetime, timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from astral.sun import elevation
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_DATE,
    ATTR_TIME,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, PlatformNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.sun import get_astral_observer
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import DeviceNotFound, async_device_details, async_request
from .const import (
    CONF_APIKEY,
    CONF_DEVICEID,
    CONF_DEVICESN,
    CONF_EVO,
    CONF_EXTPV,
    CONF_GET_VARIABLES,
    CONF_HAS_BATTERY,
    CONF_V1_API,
    CONF_WAKE_ELEVATION,
    CONF_WAKE_GRACE,
    CONF_XTZONE,
    DEFAULT_NAME,
    DEFAULT_WAKE_ELEVATION,
    DEFAULT_WAKE_GRACE_MINUTES,
    DOMAIN,
)
from .sun_state import (
    OPERATIONAL_STATES,
    expected_online,
    operational_state,
    should_offload,
    should_raise_issue,
)

_LOGGER = logging.getLogger(__name__)
_ENDPOINT_OA_BATTERY_SETTINGS = "/op/v0/device/battery/soc/get"
_ENDPOINT_OA_REPORT = "/op/v0/device/report/query"
_ENDPOINT_OA_DEVICE_VARIABLES = "/op/v0/device/real/query"
_ENDPOINT_OA_DEVICE_VARIABLES_V1 = "/op/v1/device/real/query"
_ENDPOINT_OA_DAILY_GENERATION = "/op/v0/device/generation"

METHOD_POST = "POST"
METHOD_GET = "GET"

ATTR_DEVICE_SN = "deviceSN"
ATTR_PLANTNAME = "plantName"
ATTR_MODULESN = "moduleSN"
ATTR_DEVICE_TYPE = "deviceType"
ATTR_MASTER = "masterVersion"
ATTR_MANAGER = "managerVersion"
ATTR_SLAVE = "slaveVersion"
ATTR_BATTERYLIST = "batteryList"
ATTR_LASTCLOUDSYNC = "lastCloudSync"

BATTERY_LEVELS = {"High": 80, "Medium": 50, "Low": 25, "Empty": 10}

CONF_SYSTEM_ID = "system_id"
RETRY_NEXT_SLOT = -1
RETRY_IN_5_MINS = 25
RAW_MAX_AGE = timedelta(seconds=361)

SCAN_MINUTES = 1  # number of minutes betwen API requests
SCAN_INTERVAL = timedelta(minutes=SCAN_MINUTES)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Required(CONF_APIKEY): cv.string,
        vol.Required(CONF_DEVICESN): cv.string,
        vol.Required(CONF_DEVICEID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_EXTPV): cv.boolean,
        vol.Optional(CONF_XTZONE): cv.boolean,
        vol.Optional(CONF_GET_VARIABLES): cv.boolean,
        vol.Optional(CONF_V1_API): cv.boolean,
        vol.Optional(CONF_EVO): cv.boolean,
        vol.Optional(CONF_HAS_BATTERY): cv.boolean,
        vol.Optional(CONF_WAKE_ELEVATION): vol.All(vol.Coerce(float), vol.Range(min=-6, max=15)),
        vol.Optional(CONF_WAKE_GRACE): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
    }
)


def _solar_elevation(hass, when):
    """Return the sun's elevation in degrees at an aware datetime (DST-safe)."""
    return elevation(get_astral_observer(hass), dateandtime=dt_util.as_utc(when))


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up a YAML platform, retaining its legacy entity identities."""
    hass.data.setdefault(DOMAIN, {})["yaml_configured"] = True
    await _async_setup_sensors(hass, config, async_add_entities)


async def _async_setup_sensors(hass, config, async_add_entities, entry=None):
    """Set up one inverter with independent polling state."""
    name = config.get(CONF_NAME)
    deviceID = config.get(CONF_DEVICEID)
    devicesn = config.get(CONF_DEVICESN)
    apiKey = config.get(CONF_APIKEY)
    ExtPV = config.get(CONF_EXTPV)
    RestrictGetVar = config.get(CONF_GET_VARIABLES)
    V1_Api = config.get(CONF_V1_API)
    Evo = config.get(CONF_EVO)
    hasBatteryOverride = config.get(CONF_HAS_BATTERY)
    wake_elevation = config.get(CONF_WAKE_ELEVATION)
    if wake_elevation is None:
        wake_elevation = DEFAULT_WAKE_ELEVATION
    wake_grace = config.get(CONF_WAKE_GRACE)
    if wake_grace is None:
        wake_grace = DEFAULT_WAKE_GRACE_MINUTES
    wake_grace = int(wake_grace)
    _LOGGER.debug("API Key: <redacted, length %s>", len(apiKey) if apiKey else 0)
    _LOGGER.debug("Device SN: %s", devicesn)
    _LOGGER.debug("Device ID: %s", deviceID)
    _LOGGER.debug("FoxESS Scan Interval: %s minutes", SCAN_MINUTES)
    _LOGGER.debug("Restrict Variables: %s", RestrictGetVar)
    _LOGGER.debug("Extended PV: %s", ExtPV)
    _LOGGER.debug("v1 Api Calls: %s", V1_Api)
    _LOGGER.debug("EVO: %s", Evo)
    if V1_Api is not False:
        V1_Api = True
        _LOGGER.debug("v1 Api Calls Enabled")
    else:
        _LOGGER.warning("v1 Api Calls Disabled, using v0")
    if ExtPV is not True:
        ExtPV = False
        _LOGGER.debug("Extended PV Disabled")
    else:
        _LOGGER.warning("Extended PV 1-18 strings enabled")
    if RestrictGetVar is not True:
        RestrictGetVar = False
        _LOGGER.debug("Get Variables is full variable mode")
    else:
        _LOGGER.warning("Get Variables is in restricted mode")
    timeslice = RETRY_NEXT_SLOT
    last_error = None
    confirmed_offline = False
    allData = {
        "report": {},
        "reportDailyGeneration": {},
        "raw": {},
        "battery": {},
        "addressbook": {},
        "online": False,
        "operational_state": None,
        "hasBattery": hasBatteryOverride,
        "last_cloud_sync": None,
        "updated_at": {},
    }

    staleness = {
        "offline_since_at": None,
        "detail_seen": False,
    }

    def _is_pv_only():
        return allData["hasBattery"] is False

    def _elevations(now):
        """Return (elevation_now, elevation_grace_ago) in degrees."""
        return (
            _solar_elevation(hass, now),
            _solar_elevation(hass, now - timedelta(minutes=wake_grace)),
        )

    def _update_status(now, is_expected_online):
        """Set the categorised operational state and raise/clear the Repairs issue.

        PV-only inverters sleep benignly when the sun is down; an issue is raised only if
        the inverter stays offline for the grace window once the sun is up. Battery
        inverters must stay online 24/7, so any sustained offline raises an issue.
        """
        state = operational_state(allData["online"], _is_pv_only(), is_expected_online)
        allData["operational_state"] = state
        issue_id = f"data_unavailable_{devicesn}"

        if state == "online":
            staleness["offline_since_at"] = None
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            return

        if state == "asleep":
            staleness["offline_since_at"] = None
            offline_minutes = 0
        else:
            if staleness["offline_since_at"] is None:
                staleness["offline_since_at"] = now
            offline_minutes = (now - staleness["offline_since_at"]).total_seconds() / 60
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        if should_raise_issue(state, offline_minutes, wake_grace, issue is not None) or (
            issue is not None and (not issue.active or not issue.is_persistent)
        ):
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="data_unavailable" if _is_pv_only() else "data_unavailable_battery",
                translation_placeholders={
                    "device_sn": devicesn,
                    "elevation": str(wake_elevation),
                    "grace": str(wake_grace),
                },
            )
            if issue is None:
                _LOGGER.warning(
                    "FoxESS data unavailable for %s for %d+ minutes during expected operating hours",
                    devicesn,
                    wake_grace,
                )

    async def async_update_data():
        nonlocal timeslice, last_error, confirmed_offline
        now = dt_util.utcnow()
        elev_now, elev_grace_ago = _elevations(now)
        is_expected_online = expected_online(elev_now, wake_elevation)
        if confirmed_offline and should_offload(
            staleness["detail_seen"], allData["online"], _is_pv_only(), elev_now, elev_grace_ago, wake_elevation
        ):
            _update_status(now, is_expected_online)
            timeslice = RETRY_NEXT_SLOT
            return allData
        if allData["operational_state"] == "asleep" and is_expected_online:
            timeslice = RETRY_NEXT_SLOT
        timeslice = (timeslice + 1) % 60
        if allData["last_cloud_sync"] is not None and now - allData["last_cloud_sync"] > RAW_MAX_AGE:
            allData["online"] = False
            allData["raw"] = {}
        if not (_is_pv_only() and not is_expected_online):
            for section, minutes in (("report", 16), ("reportDailyGeneration", 61), ("battery", 61)):
                updated = allData["updated_at"].get(section)
                if updated is not None and now - updated > timedelta(minutes=minutes):
                    allData[section] = {}

        if timeslice % 5 == 0:
            try:
                if timeslice % 15 == 0 or confirmed_offline or not staleness["detail_seen"]:
                    details = await async_device_details(hass, apiKey, devicesn, evo=Evo is True, v1_api=V1_Api)
                    allData["addressbook"] = {**details, "plantName": details.get("stationName")}
                    if not details["hasBattery"]:
                        allData["addressbook"][ATTR_BATTERYLIST] = "No Battery"
                    staleness["detail_seen"] = True
                    allData["hasBattery"] = (
                        hasBatteryOverride if hasBatteryOverride is not None else allData["addressbook"]["hasBattery"]
                    )
                confirmed_offline = int(allData["addressbook"]["status"]) == 3
                if confirmed_offline:
                    allData["online"] = False
                    allData["raw"] = {"runningState": "164"}
                else:
                    await getRaw(hass, allData, apiKey, devicesn, v1_api=V1_Api, restrict=RestrictGetVar)
                    confirmed_offline = not allData["online"]
                last_error = None
            except (UpdateFailed, ConfigEntryAuthFailed) as err:
                confirmed_offline = False
                allData["online"] = False
                allData["raw"] = {}
                timeslice = RETRY_IN_5_MINS
                last_error = err

            if last_error is None and allData["online"]:
                queries = []
                if timeslice % 15 == 0:
                    queries.append(("report", getReport))
                if timeslice == 0:
                    queries.append(("reportDailyGeneration", getReportDailyGeneration))
                    queries.append(("battery", getOABatterySettings))
                for section, query in queries:
                    try:
                        await query(hass, allData, apiKey, devicesn)
                    except UpdateFailed as err:
                        allData[section] = {}
                        _LOGGER.warning("FoxESS %s update failed: %s", section, err)
                    else:
                        allData["updated_at"][section] = dt_util.utcnow()

        _update_status(dt_util.utcnow(), is_expected_online)
        if last_error is not None:
            raise last_error
        return allData

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=name,
        config_entry=entry,
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    if entry is not None:
        await coordinator.async_config_entry_first_refresh()
    else:
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            await coordinator.async_shutdown()
            raise PlatformNotReady("Unable to initialise FoxESS Cloud") from coordinator.last_exception

    if hasBatteryOverride is not None:
        hasBattery = hasBatteryOverride
    else:
        hasBattery = allData["addressbook"].get("hasBattery", True)
    _LOGGER.debug("hasBattery: %s (override: %s)", hasBattery, hasBatteryOverride)

    async_add_entities(
        [
            FoxESSCurrent(coordinator, name, deviceID, "PV1 Current", "pv1-current", "pv1Current"),
            FoxESSPower(coordinator, name, deviceID, "PV1 Power", "pv1-power", "pv1Power"),
            FoxESSVolt(coordinator, name, deviceID, "PV1 Volt", "pv1-volt", "pv1Volt"),
            FoxESSCurrent(coordinator, name, deviceID, "PV2 Current", "pv2-current", "pv2Current"),
            FoxESSPower(coordinator, name, deviceID, "PV2 Power", "pv2-power", "pv2Power"),
            FoxESSVolt(coordinator, name, deviceID, "PV2 Volt", "pv2-volt", "pv2Volt"),
            FoxESSCurrent(coordinator, name, deviceID, "PV3 Current", "pv3-current", "pv3Current"),
            FoxESSPower(coordinator, name, deviceID, "PV3 Power", "pv3-power", "pv3Power"),
            FoxESSVolt(coordinator, name, deviceID, "PV3 Volt", "pv3-volt", "pv3Volt"),
            FoxESSCurrent(coordinator, name, deviceID, "PV4 Current", "pv4-current", "pv4Current"),
            FoxESSPower(coordinator, name, deviceID, "PV4 Power", "pv4-power", "pv4Power"),
            FoxESSVolt(coordinator, name, deviceID, "PV4 Volt", "pv4-volt", "pv4Volt"),
            FoxESSCurrent(coordinator, name, deviceID, "PV5 Current", "pv5-current", "pv5Current"),
            FoxESSPower(coordinator, name, deviceID, "PV5 Power", "pv5-power", "pv5Power"),
            FoxESSVolt(coordinator, name, deviceID, "PV5 Volt", "pv5-volt", "pv5Volt"),
            FoxESSCurrent(coordinator, name, deviceID, "PV6 Current", "pv6-current", "pv6Current"),
            FoxESSPower(coordinator, name, deviceID, "PV6 Power", "pv6-power", "pv6Power"),
            FoxESSVolt(coordinator, name, deviceID, "PV6 Volt", "pv6-volt", "pv6Volt"),
            FoxESSPower(coordinator, name, deviceID, "PV Power", "pv-power", "pvPower"),
            FoxESSCurrent(coordinator, name, deviceID, "R Current", "r-current", "RCurrent"),
            FoxESSFreq(coordinator, name, deviceID, "R Freq", "r-freq", "RFreq"),
            FoxESSPower(coordinator, name, deviceID, "R Power", "r-power", "RPower"),
            FoxESSPowerString(
                coordinator,
                name,
                deviceID,
                "Meter2 Power",
                "meter2-power",
                "meterPower2",
            ),
            FoxESSVolt(coordinator, name, deviceID, "R Volt", "r-volt", "RVolt"),
            FoxESSCurrent(coordinator, name, deviceID, "S Current", "s-current", "SCurrent"),
            FoxESSFreq(coordinator, name, deviceID, "S Freq", "s-freq", "SFreq"),
            FoxESSPower(coordinator, name, deviceID, "S Power", "s-power", "SPower"),
            FoxESSVolt(coordinator, name, deviceID, "S Volt", "s-volt", "SVolt"),
            FoxESSCurrent(coordinator, name, deviceID, "T Current", "t-current", "TCurrent"),
            FoxESSFreq(coordinator, name, deviceID, "T Freq", "t-freq", "TFreq"),
            FoxESSPower(coordinator, name, deviceID, "T Power", "t-power", "TPower"),
            FoxESSVolt(coordinator, name, deviceID, "T Volt", "t-volt", "TVolt"),
            FoxESSReactivePower(coordinator, name, deviceID),
            FoxESSPowerFactor(coordinator, name, deviceID),
            FoxESSTemp(
                coordinator,
                name,
                deviceID,
                "Ambient Temperature",
                "ambient-temperature",
                "ambientTemperation",
            ),
            FoxESSTemp(
                coordinator,
                name,
                deviceID,
                "Boost Temperature",
                "boost-temperature",
                "boostTemperation",
            ),
            FoxESSTemp(
                coordinator,
                name,
                deviceID,
                "Inv Temperature",
                "inv-temperature",
                "invTemperation",
            ),
            FoxESSSolarPower(coordinator, name, deviceID),
            FoxESSEnergySolar(coordinator, name, deviceID),
            FoxESSInverter(coordinator, name, deviceID),
            FoxESSPowerString(
                coordinator,
                name,
                deviceID,
                "Generation Power",
                "-generation-power",
                "generationPower",
            ),
            FoxESSPowerString(
                coordinator,
                name,
                deviceID,
                "Grid Consumption Power",
                "grid-consumption-power",
                "gridConsumptionPower",
            ),
            FoxESSPowerString(
                coordinator,
                name,
                deviceID,
                "FeedIn Power",
                "feedIn-power",
                "feedinPower",
            ),
            FoxESSPowerString(coordinator, name, deviceID, "Load Power", "load-power", "loadsPower"),
            FoxESSEnergyGenerated(
                coordinator,
                name,
                deviceID,
                "Energy Generated",
                "energy-generated",
                "value",
            ),
            FoxESSEnergyGenerated(
                coordinator,
                name,
                deviceID,
                "Energy Generated Month",
                "energy-generated-month",
                "month",
            ),
            FoxESSEnergyGenerated(
                coordinator,
                name,
                deviceID,
                "Energy Generated Cumulative",
                "energy-generated-cumulative",
                "cumulative",
            ),
            FoxESSEnergyGridConsumption(coordinator, name, deviceID),
            FoxESSEnergyFeedin(coordinator, name, deviceID),
            FoxESSEnergyLoad(coordinator, name, deviceID),
            FoxESSPVEnergyTotal(coordinator, name, deviceID),
            FoxESSResponseTime(coordinator, name, deviceID),
            FoxESSRunningState(
                coordinator,
                name,
                deviceID,
                "Running State",
                "running-state",
                "runningState",
            ),
            FoxESSStatus(coordinator, name, deviceID),
        ]
    )

    if hasBattery:
        async_add_entities(
            [
                FoxESSTemp(
                    coordinator,
                    name,
                    deviceID,
                    "Bat Temperature",
                    "bat-temperature",
                    "batTemperature",
                ),
                FoxESSTemp(
                    coordinator,
                    name,
                    deviceID,
                    "Bat Temperature2",
                    "bat-temperature2",
                    "batTemperature_2",
                ),
                FoxESSBatSoC(coordinator, name, deviceID, "Bat SoC", "bat-soc", "SoC"),
                FoxESSBatSoC(coordinator, name, deviceID, "Bat SoC1", "bat-soc1", "SoC_1"),
                FoxESSBatSoC(coordinator, name, deviceID, "Bat SoC2", "bat-soc2", "SoC_2"),
                FoxESSBatSoC(coordinator, name, deviceID, "Bat SoH", "bat-soh", "SOH"),
                FoxESSPower(
                    coordinator,
                    name,
                    deviceID,
                    "Inverter Bat Power",
                    "inv-Bat-Power",
                    "invBatPower",
                ),
                FoxESSPower(
                    coordinator,
                    name,
                    deviceID,
                    "Inverter Bat Power2",
                    "inv-Bat-Power2",
                    "invBatPower_2",
                ),
                FoxESSBatMinSoC(coordinator, name, deviceID),
                FoxESSBatMinSoConGrid(coordinator, name, deviceID),
                FoxESSEnergyThroughput(coordinator, name, deviceID),
                FoxESSPowerString(
                    coordinator,
                    name,
                    deviceID,
                    "Bat Discharge Power",
                    "bat-discharge-power",
                    "batDischargePower",
                ),
                FoxESSPowerString(
                    coordinator,
                    name,
                    deviceID,
                    "Bat Charge Power",
                    "bat-charge-power",
                    "batChargePower",
                ),
                FoxESSEnergyBatCharge(coordinator, name, deviceID),
                FoxESSEnergyBatDischarge(coordinator, name, deviceID),
                FoxESSResidualEnergy(coordinator, name, deviceID),
                FoxESSMaxBatChargeCurrent(coordinator, name, deviceID),
                FoxESSMaxBatDischargeCurrent(coordinator, name, deviceID),
            ]
        )

    if ExtPV:
        async_add_entities(
            [
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV7 Current",
                    "pv7-current",
                    "pv7Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV7 Power", "pv7-power", "pv7Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV7 Volt", "pv7-volt", "pv7Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV8 Current",
                    "pv8-current",
                    "pv8Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV8 Power", "pv8-power", "pv8Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV8 Volt", "pv8-volt", "pv8Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV9 Current",
                    "pv9-current",
                    "pv9Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV9 Power", "pv9-power", "pv9Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV9 Volt", "pv9-volt", "pv9Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV10 Current",
                    "pv10-current",
                    "pv10Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV10 Power", "pv10-power", "pv10Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV10 Volt", "pv10-volt", "pv10Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV11 Current",
                    "pv11-current",
                    "pv11Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV11 Power", "pv11-power", "pv11Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV11 Volt", "pv11-volt", "pv11Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV12 Current",
                    "pv12-current",
                    "pv12Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV12 Power", "pv12-power", "pv12Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV12 Volt", "pv12-volt", "pv12Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV13 Current",
                    "pv13-current",
                    "pv13Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV13 Power", "pv13-power", "pv13Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV13 Volt", "pv13-volt", "pv13Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV14 Current",
                    "pv14-current",
                    "pv14Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV14 Power", "pv14-power", "pv14Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV14 Volt", "pv14-volt", "pv14Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV15 Current",
                    "pv15-current",
                    "pv15Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV15 Power", "pv15-power", "pv15Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV15 Volt", "pv15-volt", "pv15Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV16 Current",
                    "pv16-current",
                    "pv16Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV16 Power", "pv16-power", "pv16Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV16 Volt", "pv16-volt", "pv16Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV17 Current",
                    "pv17-current",
                    "pv17Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV17 Power", "pv17-power", "pv17Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV17 Volt", "pv17-volt", "pv17Volt"),
                FoxESSCurrent(
                    coordinator,
                    name,
                    deviceID,
                    "PV18 Current",
                    "pv18-current",
                    "pv18Current",
                ),
                FoxESSPower(coordinator, name, deviceID, "PV18 Power", "pv18-power", "pv18Power"),
                FoxESSVolt(coordinator, name, deviceID, "PV18 Volt", "pv18-volt", "pv18Volt"),
            ]
        )


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up FoxESS sensor from a config entry."""
    async_add_entities(entry.runtime_data)


async def async_prepare_entry(hass, entry):
    """Fetch initial data before Home Assistant forwards platform setup."""
    config = {
        CONF_NAME: entry.data.get(CONF_NAME, DEFAULT_NAME),
        CONF_DEVICEID: entry.data[CONF_DEVICEID],
        CONF_DEVICESN: entry.data[CONF_DEVICESN],
        CONF_APIKEY: entry.data[CONF_APIKEY],
        CONF_HAS_BATTERY: entry.data.get(CONF_HAS_BATTERY),
        CONF_EXTPV: entry.options.get(CONF_EXTPV, False),
        CONF_EVO: entry.options.get(CONF_EVO, False),
        CONF_WAKE_ELEVATION: entry.options.get(CONF_WAKE_ELEVATION, DEFAULT_WAKE_ELEVATION),
        CONF_WAKE_GRACE: entry.options.get(CONF_WAKE_GRACE, DEFAULT_WAKE_GRACE_MINUTES),
        CONF_GET_VARIABLES: False,
        CONF_V1_API: True,
    }
    entities = []
    await _async_setup_sensors(hass, config, entities.extend, entry)
    return entities


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise UpdateFailed("FoxESS returned a non-numeric measurement")
    return value


async def getOABatterySettings(hass, allData, apiKey, devicesn):
    if allData["hasBattery"] is False:
        allData["battery"] = {}
        return
    result, _ = await async_request(hass, METHOD_GET, _ENDPOINT_OA_BATTERY_SETTINGS, apiKey, params={"sn": devicesn})
    if not isinstance(result, dict):
        raise UpdateFailed("FoxESS returned invalid battery settings")
    allData["battery"] = {
        key: _number(result[key]) for key in ("minSoc", "minSocOnGrid") if result.get(key) is not None
    }


async def getReport(hass, allData, apiKey, devicesn):
    now = dt_util.now()
    result, _ = await async_request(
        hass,
        METHOD_POST,
        _ENDPOINT_OA_REPORT,
        apiKey,
        payload={
            "sn": devicesn,
            "year": now.year,
            "month": now.month,
            "dimension": "month",
            "variables": [
                "feedin",
                "generation",
                "gridConsumption",
                "chargeEnergyToTal",
                "dischargeEnergyToTal",
                "loads",
                "PVEnergyTotal",
            ],
        },
    )
    if not isinstance(result, list):
        raise UpdateFailed("FoxESS returned an invalid energy report")
    report = {}
    for item in result:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("variable"), str)
            or not isinstance(item.get("values"), list)
        ):
            raise UpdateFailed("FoxESS returned an invalid energy report item")
        value = _number(item["values"][now.day - 1]) if len(item["values"]) >= now.day else None
        if value is not None:
            report[item["variable"]] = round(value, 3)
    allData["report"] = report


async def getReportDailyGeneration(hass, allData, apiKey, devicesn):
    result, _ = await async_request(hass, METHOD_GET, _ENDPOINT_OA_DAILY_GENERATION, apiKey, params={"sn": devicesn})
    if not isinstance(result, dict):
        raise UpdateFailed("FoxESS returned invalid generation data")
    allData["reportDailyGeneration"] = {
        key: _number(result[source])
        for key, source in (("value", "today"), ("month", "month"), ("cumulative", "cumulative"))
        if result.get(source) is not None
    }


async def getRaw(hass, allData, apiKey, devicesn, *, v1_api=True, restrict=False):
    payload = {"sns": [devicesn]} if v1_api else {"sn": devicesn}
    if restrict:
        payload["variables"] = (
            "ambientTemperation batChargePower batCurrent batCurrent_1 batCurrent_2 batDischargePower "
            "batTemperature batTemperature_1 batTemperature_2 batVolt batVolt_1 batVolt_2 boostTemperation "
            "chargeTemperature dspTemperature epsCurrentR epsCurrentS epsCurrentT epsPower epsPowerR epsPowerS "
            "epsPowerT epsVoltR epsVoltS epsVoltT feedinPower generationPower gridConsumptionPower input "
            "invBatCurrent invBatPower invBatVolt invTemperation loadsPower loadsPowerR loadsPowerS loadsPowerT "
            "meterPower meterPower2 meterPowerR meterPowerS meterPowerT PowerFactor pv1Current pv1Power pv1Volt "
            "pv2Current pv2Power pv2Volt pv3Current pv3Power pv3Volt pv4Current pv4Power pv4Volt pvPower RCurrent "
            "ReactivePower RFreq RPower RVolt SCurrent SFreq SoC SPower SVolt TCurrent TFreq TPower TVolt "
            "SoC_1 SoC_2 ResidualEnergy energyThroughput runningState currentFaultCount"
        ).split()
    path = _ENDPOINT_OA_DEVICE_VARIABLES_V1 if v1_api else _ENDPOINT_OA_DEVICE_VARIABLES
    result, elapsed_ms = await async_request(hass, METHOD_POST, path, apiKey, payload=payload)
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise UpdateFailed("FoxESS returned invalid real-time data")
    sample = result[0]
    if sample.get("deviceSN", sample.get("sn", devicesn)) != devicesn:
        raise DeviceNotFound("FoxESS returned readings for a different inverter")
    if not isinstance(sample.get("time"), str) or not isinstance(sample.get("datas"), list):
        raise UpdateFailed("FoxESS returned incomplete real-time data")
    try:
        sample_time = dt_util.as_utc(datetime.strptime(sample["time"], "%Y-%m-%d %H:%M:%S GMT%z"))
    except ValueError as err:
        raise UpdateFailed("FoxESS returned an invalid sample timestamp") from err

    raw = {}
    for item in sample["datas"]:
        if not isinstance(item, dict) or not isinstance(item.get("variable"), str):
            raise UpdateFailed("FoxESS returned an invalid real-time measurement")
        variable = item["variable"]
        value = item.get("value")
        if value is None:
            continue
        if variable == "runningState":
            raw[variable] = str(value)
            continue
        value = _number(value)
        variable = {"batTemperature_1": "batTemperature", "invBatPower_1": "invBatPower"}.get(variable, variable)
        if variable == "ResidualEnergy":
            scale = {"kWh": 1, "1.0kWh": 1, "0.1kWh": 0.1}.get(item.get("unit"))
            if scale is None:
                _LOGGER.warning("FoxESS returned an unsupported ResidualEnergy unit")
                continue
            value = round(value * scale, 3)
        raw[variable] = value

    age = dt_util.utcnow() - sample_time
    if age > RAW_MAX_AGE:
        if allData["hasBattery"] is False and raw.get("runningState") in ("161", "162"):
            allData["online"] = False
            allData["raw"] = {"runningState": raw["runningState"]}
            return
        raise UpdateFailed("FoxESS real-time data is stale")
    if age < timedelta(minutes=-1):
        raise UpdateFailed("FoxESS sample timestamp is in the future")
    if not any(key != "runningState" for key in raw):
        raise UpdateFailed("FoxESS returned no usable real-time measurements")
    raw["ResponseTime"] = elapsed_ms
    allData["raw"] = raw
    allData["last_cloud_sync"] = sample_time
    allData["online"] = True


class FoxESSPowerString(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSCurrent(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSFreq(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSPower(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSVolt(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSReactivePower(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.REACTIVE_POWER
    _attr_native_unit_of_measurement = UnitOfReactivePower.VOLT_AMPERE_REACTIVE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Reactive Power")
        self._attr_name = name + " - Reactive Power"
        self._attr_unique_id = deviceID + "reactive-power"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "ReactivePower" not in self.coordinator.data["raw"]:
                _LOGGER.debug("ReactivePower None")
            else:
                return self.coordinator.data["raw"]["ReactivePower"] * 1000
        return None


class FoxESSPowerFactor(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Power Factor")
        self._attr_name = name + " - Power Factor"
        self._attr_unique_id = deviceID + "power-factor"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "PowerFactor" not in self.coordinator.data["raw"]:
                _LOGGER.debug("PowerFactor None")
            else:
                return self.coordinator.data["raw"]["PowerFactor"]
        return None


class FoxESSEnergyGenerated(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self._keyValue not in self.coordinator.data["reportDailyGeneration"]:
            _LOGGER.debug("%s None", self._keyValue)
        else:
            if self.coordinator.data["reportDailyGeneration"][self._keyValue] == 0:
                energygenerated = 0
            else:
                energygenerated = self.coordinator.data["reportDailyGeneration"][self._keyValue]
                if energygenerated > 0:
                    energygenerated = round(energygenerated, 3)
                else:
                    energygenerated = 0
            return energygenerated
        return None


class FoxESSEnergyThroughput(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Energy Throughput")
        self._attr_name = name + " - Energy Throughput"
        self._attr_unique_id = deviceID + "energy-throughput"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "energyThroughput" not in self.coordinator.data["raw"]:
            _LOGGER.debug("raw Energy Throughput None")
        else:
            if self.coordinator.data["raw"]["energyThroughput"] == 0:
                energygenerated = 0
            else:
                energygenerated = self.coordinator.data["raw"]["energyThroughput"]
                if energygenerated > 0:
                    energygenerated = round(energygenerated, 3)
                else:
                    energygenerated = 0
            return energygenerated
        return None


class FoxESSEnergyGridConsumption(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Grid Consumption")
        self._attr_name = name + " - Grid Consumption"
        self._attr_unique_id = deviceID + "grid-consumption"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "gridConsumption" not in self.coordinator.data["report"]:
            _LOGGER.debug("report gridConsumption None")
        else:
            if self.coordinator.data["report"]["gridConsumption"] == 0:
                energygrid = 0
            else:
                energygrid = self.coordinator.data["report"]["gridConsumption"]
            return energygrid
        return None


class FoxESSEnergyFeedin(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - FeedIn")
        self._attr_name = name + " - FeedIn"
        self._attr_unique_id = deviceID + "feedIn"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "feedin" not in self.coordinator.data["report"]:
            _LOGGER.debug("report feedin None")
        else:
            if self.coordinator.data["report"]["feedin"] == 0:
                energyfeedin = 0
            else:
                energyfeedin = self.coordinator.data["report"]["feedin"]
            return energyfeedin
        return None


class FoxESSEnergyBatCharge(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat Charge")
        self._attr_name = name + " - Bat Charge"
        self._attr_unique_id = deviceID + "bat-charge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "chargeEnergyToTal" not in self.coordinator.data["report"]:
            _LOGGER.debug("report chargeEnergyToTal None")
        else:
            if self.coordinator.data["report"]["chargeEnergyToTal"] == 0:
                energycharge = 0
            else:
                energycharge = self.coordinator.data["report"]["chargeEnergyToTal"]
            return energycharge
        return None


class FoxESSMaxBatChargeCurrent(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Max Bat Charge Current")
        self._attr_name = name + " - Max Bat Charge Current"
        self._attr_unique_id = deviceID + "max-bat-charge-charge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "maxChargeCurrent" not in self.coordinator.data["raw"]:
            _LOGGER.debug("report maxChargeCurrent None")
        else:
            if self.coordinator.data["raw"]["maxChargeCurrent"] == 0:
                charge = 0
            else:
                charge = self.coordinator.data["raw"]["maxChargeCurrent"]
            return charge
        return None


class FoxESSMaxBatDischargeCurrent(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Max Bat Discharge Current")
        self._attr_name = name + " - Max Bat Discharge Current"
        self._attr_unique_id = deviceID + "max-bat-discharge-charge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "maxDischargeCurrent" not in self.coordinator.data["raw"]:
            _LOGGER.debug("report maxDischargeCurrent None")
        else:
            if self.coordinator.data["raw"]["maxDischargeCurrent"] == 0:
                charge = 0
            else:
                charge = self.coordinator.data["raw"]["maxDischargeCurrent"]
            return charge
        return None


class FoxESSEnergyBatDischarge(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat Discharge")
        self._attr_name = name + " - Bat Discharge"
        self._attr_unique_id = deviceID + "bat-discharge"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "dischargeEnergyToTal" not in self.coordinator.data["report"]:
            _LOGGER.debug("report dischargeEnergyToTal None")
        else:
            if self.coordinator.data["report"]["dischargeEnergyToTal"] == 0:
                energydischarge = 0
            else:
                energydischarge = self.coordinator.data["report"]["dischargeEnergyToTal"]
            return energydischarge
        return None


class FoxESSEnergyLoad(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Load")
        self._attr_name = name + " - Load"
        self._attr_unique_id = deviceID + "load"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "loads" not in self.coordinator.data["report"]:
            _LOGGER.debug("report loads None")
        else:
            if self.coordinator.data["report"]["loads"] == 0:
                energyload = 0
            else:
                energyload = self.coordinator.data["report"]["loads"]
            # round
            return round(energyload, 3)
        return None


class FoxESSPVEnergyTotal(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - PV Energy Total")
        self._attr_name = name + " - PVEnergyTotal"
        self._attr_unique_id = deviceID + "PVEnergyTotal"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if "PVEnergyTotal" not in self.coordinator.data["report"]:
            _LOGGER.debug("report PVEnergyTotal None")
        else:
            if self.coordinator.data["report"]["PVEnergyTotal"] == 0:
                energyload = 0
            else:
                energyload = self.coordinator.data["report"]["PVEnergyTotal"]
            # round
            return round(energyload, 3)
        return None


class FoxESSInverter(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Inverter")
        self._attr_name = name + " - Inverter"
        self._attr_unique_id = deviceID + "Inverter"
        self._attr_icon = "mdi:solar-power"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
                ATTR_DEVICE_SN,
                ATTR_PLANTNAME,
                ATTR_MODULESN,
                ATTR_DEVICE_TYPE,
                ATTR_MASTER,
                ATTR_MANAGER,
                ATTR_SLAVE,
                ATTR_BATTERYLIST,
                ATTR_LASTCLOUDSYNC,
            ],
        )

    @property
    def native_value(self) -> str | None:
        status = self.coordinator.data.get("addressbook", {}).get("status")
        return {"1": "on-line", "2": "in-alarm", "3": "off-line"}.get(str(status))

    @property
    def extra_state_attributes(self):
        details = self.coordinator.data.get("addressbook", {})
        attributes = {
            key: details[key]
            for key in (
                ATTR_DEVICE_SN,
                ATTR_PLANTNAME,
                ATTR_MODULESN,
                ATTR_DEVICE_TYPE,
                ATTR_MASTER,
                ATTR_MANAGER,
                ATTR_SLAVE,
                ATTR_BATTERYLIST,
            )
            if key in details
        }
        attributes[ATTR_LASTCLOUDSYNC] = self.coordinator.data.get("last_cloud_sync")
        return attributes


class FoxESSRunningState(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self._attr_icon = "mdi:state-machine"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                res = self.coordinator.data["raw"][self._keyValue]
                if res == "160":
                    resText = f"{res}: self-test"
                elif res == "161":
                    resText = f"{res}: waiting"
                elif res == "162":
                    resText = f"{res}: checking"
                elif res == "163":
                    resText = f"{res}: on-grid"
                elif res == "164":
                    resText = f"{res}: off-grid"
                elif res == "165":
                    resText = f"{res}: fault"
                elif res == "166":
                    resText = f"{res}: permanent-fault"
                elif res == "167":
                    resText = f"{res}: standby"
                elif res == "168":
                    resText = f"{res}: upgrading"
                elif res == "169":
                    resText = f"{res}: fct"
                elif res == "170":
                    resText = f"{res}: illegal"
                else:
                    _LOGGER.debug("runcode %s", res)
                    resText = f"{res}: unknown code"
                return resText
        return None


class FoxESSStatus(CoordinatorEntity, SensorEntity):
    """Diagnostic sun-aware operational state: online / asleep / offline."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = OPERATIONAL_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "operational_state"
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Status")
        self._attr_name = name + " - Status"
        self._attr_unique_id = deviceID + "status"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("operational_state")

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None


def _solar_total(data, section, keys):
    if not data["online"]:
        return None
    values = data[section]
    total = 0
    for key, sign, battery in keys:
        value = values.get(key)
        if value is None:
            if battery and data.get("hasBattery") is False:
                continue
            return None
        total += sign * value
    return round(max(0, total), 3)


class FoxESSEnergySolar(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Solar")
        self._attr_name = name + " - Solar"
        self._attr_unique_id = deviceID + "solar"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        return _solar_total(
            self.coordinator.data,
            "report",
            (
                ("loads", 1, False),
                ("chargeEnergyToTal", 1, True),
                ("feedin", 1, False),
                ("gridConsumption", -1, False),
                ("dischargeEnergyToTal", -1, True),
            ),
        )


class FoxESSSolarPower(CoordinatorEntity, SensorEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Solar Power")
        self._attr_name = name + " - Solar Power"
        self._attr_unique_id = deviceID + "solar-power"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        return _solar_total(
            self.coordinator.data,
            "raw",
            (
                ("loadsPower", 1, False),
                ("batChargePower", 1, True),
                ("feedinPower", 1, False),
                ("gridConsumptionPower", -1, False),
                ("batDischargePower", -1, True),
            ),
        )


class FoxESSBatSoC(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None

    @property
    def icon(self):
        return icon_for_battery_level(battery_level=self.native_value, charging=None)


class FoxESSBatMinSoC(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat MinSoC")
        self._attr_name = name + " - Bat MinSoC"
        self._attr_unique_id = deviceID + "bat-minsoc"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["battery"]:
            if "minSoc" not in self.coordinator.data["battery"]:
                _LOGGER.debug("minSoc None")
            else:
                return self.coordinator.data["battery"]["minSoc"]
        return None

    @property
    def icon(self):
        return icon_for_battery_level(battery_level=self.native_value, charging=None)


class FoxESSBatMinSoConGrid(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Bat minSocOnGrid")
        self._attr_name = name + " - Bat minSocOnGrid"
        self._attr_unique_id = deviceID + "bat-minSocOnGrid"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["battery"]:
            if "minSocOnGrid" not in self.coordinator.data["battery"]:
                _LOGGER.debug("minSocOnGrid None")
            else:
                return self.coordinator.data["battery"]["minSocOnGrid"]
        return None

    @property
    def icon(self):
        return icon_for_battery_level(battery_level=self.native_value, charging=None)


class FoxESSTemp(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, name, deviceID, nameValue, uniqueValue, keyValue):
        super().__init__(coordinator=coordinator)
        self._nameValue = nameValue
        self._uniqueValue = uniqueValue
        self._keyValue = keyValue
        _LOGGER.debug("Initiating Entity - %s", self._nameValue)
        self._attr_name = f"{name} - {self._nameValue}"
        self._attr_unique_id = f"{deviceID}{self._uniqueValue}"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if self._keyValue not in self.coordinator.data["raw"]:
                _LOGGER.debug("%s None", self._keyValue)
            else:
                return self.coordinator.data["raw"][self._keyValue]
        return None


class FoxESSResidualEnergy(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Residual Energy")
        self._attr_name = name + " - Residual Energy"
        self._attr_unique_id = deviceID + "residual-energy"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data["online"] and self.coordinator.data["raw"]:
            if "ResidualEnergy" not in self.coordinator.data["raw"]:
                _LOGGER.debug("ResidualEnergy None")
            else:
                return self.coordinator.data["raw"]["ResidualEnergy"]
        return None


class FoxESSResponseTime(CoordinatorEntity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS

    def __init__(self, coordinator, name, deviceID):
        super().__init__(coordinator=coordinator)
        _LOGGER.debug("Initiating Entity - Response Time")
        self._attr_name = name + " - Response Time"
        self._attr_unique_id = deviceID + "response-time"
        self.status = namedtuple(
            "status",
            [
                ATTR_DATE,
                ATTR_TIME,
            ],
        )

    @property
    def native_value(self) -> float | None:
        if "ResponseTime" not in self.coordinator.data["raw"]:
            _LOGGER.debug("ResponseTime None")
        else:
            return self.coordinator.data["raw"]["ResponseTime"]
        return None
