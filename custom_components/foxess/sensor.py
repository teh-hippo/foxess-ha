from __future__ import annotations

import logging
from collections import namedtuple

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
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
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    UpdateFailed,
)

from .capabilities import (
    CORE,
    ESTIMATES,
    KNOWN_ENTITIES,
    RAW_ENTITIES,
    VERSION,
    async_detect,
    compatible_entities,
    make_record,
    valid_record,
)
from .const import (
    CONF_APIKEY,
    CONF_CAPABILITIES,
    CONF_DEVICEID,
    CONF_DEVICESN,
    CONF_EVO,
    CONF_EXTPV,
    CONF_HAS_BATTERY,
    CONF_WAKE_ELEVATION,
    CONF_WAKE_GRACE,
    DEFAULT_NAME,
    DEFAULT_WAKE_ELEVATION,
    DEFAULT_WAKE_GRACE_MINUTES,
    DOMAIN,
)
from .coordinator import FoxESSCoordinator
from .estimated_energy import FoxESSEstimatedEnergy
from .sun_state import OPERATIONAL_STATES

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_SN = "deviceSN"
ATTR_PLANTNAME = "plantName"
ATTR_MODULESN = "moduleSN"
ATTR_DEVICE_TYPE = "deviceType"
ATTR_MASTER = "masterVersion"
ATTR_MANAGER = "managerVersion"
ATTR_SLAVE = "slaveVersion"
ATTR_BATTERYLIST = "batteryList"
ATTR_LASTCLOUDSYNC = "lastCloudSync"

# Accepted only to import the old identity; YAML no longer creates sensors.
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_APIKEY): cv.string,
        vol.Required(CONF_DEVICESN): cv.string,
        vol.Required(CONF_DEVICEID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_EXTPV): cv.boolean,
        vol.Optional(CONF_EVO): cv.boolean,
        vol.Optional(CONF_HAS_BATTERY): cv.boolean,
        vol.Optional(CONF_WAKE_ELEVATION): vol.All(vol.Coerce(float), vol.Range(min=-6, max=15)),
        vol.Optional(CONF_WAKE_GRACE): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Import YAML once without changing its deviceID or creating a second platform."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "yaml_migration",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="yaml_migration",
    )
    hass.async_create_task(hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data=dict(config)))


def _create_sensors(coordinator, name, deviceID):
    entities = []
    entities.extend(
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

    entities.extend(
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

    for n in range(7, 19):
        for cls, label, suffix in (
            (FoxESSCurrent, "Current", "current"),
            (FoxESSPower, "Power", "power"),
            (FoxESSVolt, "Volt", "volt"),
        ):
            entities.append(cls(coordinator, name, deviceID, f"PV{n} {label}", f"pv{n}-{suffix}", f"pv{n}{label}"))
    return entities


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up FoxESS sensor from a config entry."""
    async_add_entities(entry.runtime_data)
    record = entry.data[CONF_CAPABILITIES]
    registry = er.async_get(hass)
    coordinator = entry.runtime_data[0].coordinator
    estimates = []
    for suffix, (source, _, label) in ESTIMATES.items():
        if suffix not in record["entities"]:
            continue
        unique_id = f"{entry.data[CONF_DEVICEID]}{source}"
        source_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if source_id is None:
            # Register the identity before entity-platform tasks finish; never guess its entity_id.
            source_entity = next(entity for entity in entry.runtime_data if entity.unique_id == unique_id)
            source_id = registry.async_get_or_create(
                "sensor",
                DOMAIN,
                unique_id,
                suggested_object_id=source_entity.name,
                config_entry=entry,
            ).entity_id
        estimates.append(
            FoxESSEstimatedEnergy(
                hass,
                coordinator,
                source_id,
                f"{entry.data[CONF_DEVICEID]}{suffix}",
                f"{entry.data.get(CONF_NAME, DEFAULT_NAME)} - {label}",
            )
        )
    async_add_entities(estimates)


async def async_prepare_entry(hass, entry):
    """Fetch initial data before Home Assistant forwards platform setup."""
    config = {
        CONF_NAME: entry.data.get(CONF_NAME, DEFAULT_NAME),
        CONF_DEVICEID: entry.data[CONF_DEVICEID],
        CONF_DEVICESN: entry.data[CONF_DEVICESN],
        CONF_APIKEY: entry.data[CONF_APIKEY],
        CONF_EVO: entry.options.get(CONF_EVO, False),
        CONF_WAKE_ELEVATION: entry.options.get(CONF_WAKE_ELEVATION, DEFAULT_WAKE_ELEVATION),
        CONF_WAKE_GRACE: entry.options.get(CONF_WAKE_GRACE, DEFAULT_WAKE_GRACE_MINUTES),
    }
    coordinator = FoxESSCoordinator(hass, entry, config)
    await coordinator.async_config_entry_first_refresh()
    record = entry.data.get(CONF_CAPABILITIES)
    if not valid_record(record) or record["version"] != VERSION:
        previous = (
            set(record["entities"])
            if valid_record(record)
            else compatible_entities(coordinator.data["addressbook"], extended=entry.options.get(CONF_EXTPV, False))
        )
        try:
            record = await async_detect(
                hass,
                config[CONF_APIKEY],
                config[CONF_DEVICESN],
                coordinator.data["addressbook"],
                previous,
            )
        except (UpdateFailed, ConfigEntryAuthFailed) as err:
            _LOGGER.warning("FoxESS capability detection failed; retaining compatible entities: %s", err)
            record = make_record(previous, "failed", {"history": str(err)})
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CAPABILITIES: record})
    coordinator.data["detection"] = {key: record[key] for key in ("outcome", "detected_at") if key in record}
    if record["outcome"] != "complete":
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"capabilities_{config[CONF_DEVICESN]}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="capabilities_inconclusive",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, f"capabilities_{config[CONF_DEVICESN]}")
    if CONF_EXTPV in entry.options:
        hass.config_entries.async_update_entry(
            entry, options={key: value for key, value in entry.options.items() if key != CONF_EXTPV}
        )
    selected = set(record["entities"])
    candidates = _create_sensors(coordinator, config[CONF_NAME], config[CONF_DEVICEID])
    entities = [entity for entity in candidates if entity.unique_id[len(config[CONF_DEVICEID]) :] in selected]
    info = device_info(config[CONF_DEVICESN], config[CONF_NAME], coordinator.data["addressbook"])
    coordinator.device_info = info
    for entity in entities:
        entity._attr_device_info = info
        suffix = entity.unique_id[len(config[CONF_DEVICEID]) :]
        entity.raw_sensor = suffix in RAW_ENTITIES or suffix in ("running-state", "response-time", "solar-power")
        entity.device_diagnostic = suffix in CORE and suffix not in ("running-state", "response-time")
    if record["outcome"] == "complete":
        registry = er.async_get(hass)
        obsolete = {f"{config[CONF_DEVICEID]}{suffix}" for suffix in KNOWN_ENTITIES - selected}
        for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
            if registered.platform == DOMAIN and registered.domain == "sensor" and registered.unique_id in obsolete:
                registry.async_remove(registered.entity_id)
    return entities


def device_info(serial, name, details):
    info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        manufacturer="FoxESS",
        name=name,
        serial_number=serial,
        configuration_url="https://www.foxesscloud.com",
    )
    if details.get("deviceType"):
        info["model"] = details["deviceType"]
    firmware = [
        f"{part}: {details[key]}"
        for part, key in (("Master", ATTR_MASTER), ("Manager", ATTR_MANAGER), ("Slave", ATTR_SLAVE))
        if details.get(key) not in (None, "", "--")
    ]
    if firmware:
        info["sw_version"] = ", ".join(firmware)
    if details.get("hardwareVersion") not in (None, "", "--"):
        info["hw_version"] = details["hardwareVersion"]
    return info


class FoxESSEntity(CoordinatorEntity, SensorEntity):
    """Shared availability and sample-triggered writes, without cached power reports."""

    raw_sensor = False
    device_diagnostic = False
    _reported_sample = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._reported_sample = self._sample_token()

    @property
    def available(self):
        return super().available and (
            self.device_diagnostic or not self.coordinator.data.get("device_unavailable", False)
        )

    def _sample_token(self):
        return (
            self.coordinator.data.get("last_cloud_sync"),
            self.coordinator.data["online"],
            bool(self.coordinator.data.get("raw")),
            self.available,
        )

    def _handle_coordinator_update(self):
        token = self._sample_token()
        if not self.raw_sensor or token != self._reported_sample:
            self._reported_sample = token
            super()._handle_coordinator_update()


class FoxESSPowerString(FoxESSEntity):
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


class FoxESSCurrent(FoxESSEntity):
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


class FoxESSFreq(FoxESSEntity):
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


class FoxESSPower(FoxESSEntity):
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


class FoxESSVolt(FoxESSEntity):
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


class FoxESSReactivePower(FoxESSEntity):
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


class FoxESSPowerFactor(FoxESSEntity):
    _attr_state_class: SensorStateClass = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_native_unit_of_measurement = None

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


class FoxESSEnergyGenerated(FoxESSEntity):
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
            return round(self.coordinator.data["reportDailyGeneration"][self._keyValue], 3)
        return None


class FoxESSEnergyThroughput(FoxESSEntity):
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


class FoxESSEnergyGridConsumption(FoxESSEntity):
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


class FoxESSEnergyFeedin(FoxESSEntity):
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


class FoxESSEnergyBatCharge(FoxESSEntity):
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


class FoxESSMaxBatChargeCurrent(FoxESSEntity):
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


class FoxESSMaxBatDischargeCurrent(FoxESSEntity):
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


class FoxESSEnergyBatDischarge(FoxESSEntity):
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


class FoxESSEnergyLoad(FoxESSEntity):
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


class FoxESSPVEnergyTotal(FoxESSEntity):
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


class FoxESSInverter(FoxESSEntity):
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
        attributes["lastCloudSuccess"] = self.coordinator.data.get("last_cloud_success")
        attributes["capabilityDetection"] = self.coordinator.data.get("detection")
        return attributes


class FoxESSRunningState(FoxESSEntity):
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


class FoxESSStatus(FoxESSEntity):
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


class FoxESSEnergySolar(FoxESSEntity):
    """Keep an unresolved legacy identity unknown until completed detection can prune it."""

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


class FoxESSSolarPower(FoxESSEntity):
    """Keep an unresolved legacy identity unknown until completed detection can prune it."""

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


class FoxESSBatSoC(FoxESSEntity):
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


class FoxESSBatMinSoC(FoxESSEntity):
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


class FoxESSBatMinSoConGrid(FoxESSEntity):
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


class FoxESSTemp(FoxESSEntity):
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


class FoxESSResidualEnergy(FoxESSEntity):
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


class FoxESSResponseTime(FoxESSEntity):
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
