"""FoxESS-owned native Integral entities, limited to observed valid power."""

from __future__ import annotations

import inspect
import logging
import math
from dataclasses import dataclass

from homeassistant.components.integration.sensor import IntegrationSensor, IntegrationSensorExtraStoredData
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import PowerConverter

_LOGGER = logging.getLogger(__name__)
_REQUIRES_HASS = "hass" in inspect.signature(IntegrationSensor.__init__).parameters


@dataclass
class _StoredEnergy(IntegrationSensorExtraStoredData):
    def as_dict(self):
        data = super().as_dict()
        if self.last_valid_state is not None:
            data["last_valid_state"] = str(self.last_valid_state)
        return data


class FoxESSEstimatedEnergy(IntegrationSensor):
    """Reuse native accumulation and restoration, never holding power through gaps."""

    def __init__(self, hass, coordinator, source_id, unique_id, name):
        kwargs = {
            "integration_method": "trapezoidal",
            "name": name,
            "round_digits": 3,
            "source_entity": source_id,
            "unique_id": unique_id,
            "unit_prefix": None,
            "unit_time": UnitOfTime.HOURS,
            "max_sub_interval": None,
        }
        if _REQUIRES_HASS:
            super().__init__(hass, **kwargs)
        else:
            super().__init__(**kwargs)
        self.coordinator = coordinator
        self._attr_device_info = coordinator.device_info
        self._invalid_source = False
        self._source_interrupted = False

    @property
    def available(self):
        source = er.async_get(self.hass).async_get(self._sensor_source_id)
        state = self.hass.states.get(self._sensor_source_id)
        return (
            super().available
            and source is not None
            and source.disabled_by is None
            and state is not None
            and state.state != STATE_UNAVAILABLE
        )

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            "coverage": "Observed valid samples only; missing intervals are not estimated",
            "invalid_source_reading": self._invalid_source,
        }

    @property
    def extra_restore_state_data(self):
        return _StoredEnergy(
            self.native_value, self.native_unit_of_measurement, self._source_entity, self._last_valid_state
        )

    async def async_get_last_sensor_data(self):
        if (restored := await self.async_get_last_extra_data()) is None:
            return None
        data = restored.as_dict()
        # Native Integral historically serialised a genuine zero total as a missing last_valid_state.
        if data.get("last_valid_state") is None and data.get("native_value") in (0, "0", "0.0"):
            data["last_valid_state"] = "0"
        return IntegrationSensorExtraStoredData.from_dict(data)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._source_registry_updated)
        )

    @callback
    def _source_registry_updated(self, event):
        if self._sensor_source_id not in (event.data["entity_id"], event.data.get("old_entity_id")):
            return
        source = er.async_get(self.hass).async_get(self._sensor_source_id)
        if source is None or source.disabled_by is not None:
            self._source_interrupted = True
        self.async_write_ha_state()
        if event.data.get("old_entity_id") == self._sensor_source_id:
            self.hass.config_entries.async_schedule_reload(self.coordinator.config_entry.entry_id)

    def _power_state(self, state):
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return state
        unit = state.attributes.get("unit_of_measurement")
        try:
            value = float(state.state)
        except ValueError:
            value = math.nan
        valid = math.isfinite(value) and value >= 0 and unit in PowerConverter.VALID_UNITS
        attributes = {**state.attributes, "unit_of_measurement": UnitOfPower.KILO_WATT}
        return State(
            state.entity_id,
            str(PowerConverter.convert(value, unit, UnitOfPower.KILO_WATT)) if valid else STATE_UNKNOWN,
            attributes,
            last_changed=state.last_changed,
            last_updated=state.last_updated,
            last_reported=state.last_reported,
            context=state.context,
        )

    def _derive_and_set_attributes_from_state(self, source_state):
        self._unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_icon = None

    def _integrate_on_state_change(self, old_timestamp, new_timestamp, old_state, new_state):
        source = er.async_get(self.hass).async_get(self._sensor_source_id)
        if new_state is None or source is None or source.disabled_by is not None:
            self._source_interrupted = True
            self._attr_available = False
            self.async_write_ha_state()
            return
        normalised = self._power_state(new_state)
        invalid = normalised.state == STATE_UNKNOWN and new_state.state != STATE_UNKNOWN
        if invalid and not self._invalid_source:
            _LOGGER.warning("Excluding invalid power from %s; its raw FoxESS reading is unchanged", self.entity_id)
        self._invalid_source = invalid
        if self._source_interrupted or (
            old_state is not None and old_state.last_reported < self.coordinator.started_at
        ):
            old_state = None
            old_timestamp = None
        self._source_interrupted = normalised.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        if old_timestamp is not None and old_timestamp < self.coordinator.started_at:
            old_timestamp = None
        super()._integrate_on_state_change(old_timestamp, new_timestamp, self._power_state(old_state), normalised)
