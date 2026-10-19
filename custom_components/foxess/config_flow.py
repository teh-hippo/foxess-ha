"""Config flow for FoxESS Cloud integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import DeviceNotFound, async_device_details
from .capabilities import async_detect, compatible_entities, valid_record
from .const import (
    CONF_APIKEY,
    CONF_CAPABILITIES,
    CONF_DEVICEID,
    CONF_DEVICESN,
    CONF_EVO,
    CONF_EXTPV,
    CONF_HAS_BATTERY,
    CONF_REDETECT,
    CONF_WAKE_ELEVATION,
    CONF_WAKE_GRACE,
    DEFAULT_NAME,
    DEFAULT_WAKE_ELEVATION,
    DEFAULT_WAKE_GRACE_MINUTES,
    DOMAIN,
)
from .coordinator import get_report

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APIKEY): str,
        vol.Required(CONF_DEVICESN): str,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Optional(CONF_EVO, default=False): bool,
    }
)


async def _validate_api(hass, api_key: str, device_sn: str, *, evo=False) -> dict[str, Any]:
    """Validate credentials using the configured inverter's lookup path."""
    try:
        return await async_device_details(hass, api_key, device_sn, evo=evo)
    except DeviceNotFound as err:
        raise ValueError("device_not_found") from err
    except ConfigEntryAuthFailed as err:
        raise ValueError("invalid_auth") from err
    except UpdateFailed as err:
        raise ValueError("cannot_connect") from err


class FoxESSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FoxESS Cloud."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        if any(
            entity.platform == DOMAIN and entity.config_entry_id is None
            for entity in er.async_get(self.hass).entities.values()
        ):
            return self.async_abort(reason="yaml_in_use")
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_APIKEY].strip()
            device_sn = user_input[CONF_DEVICESN].strip()
            name = user_input.get(CONF_NAME, DEFAULT_NAME)
            evo = user_input.get(CONF_EVO, False)

            await self.async_set_unique_id(device_sn)
            self._abort_if_unique_id_configured()
            try:
                result = await _validate_api(self.hass, api_key, device_sn, evo=evo)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(
                    title=f"{name} ({device_sn})",
                    data={
                        CONF_APIKEY: api_key,
                        CONF_DEVICESN: device_sn,
                        CONF_DEVICEID: device_sn,
                        CONF_NAME: name,
                        CONF_HAS_BATTERY: result["hasBattery"],
                    },
                    options={
                        CONF_EVO: evo,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        """Adopt legacy YAML entities using their exact stored identity prefix."""
        serial = user_input[CONF_DEVICESN]
        await self.async_set_unique_id(serial)
        existing = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, serial)
        if existing:
            if existing.data[CONF_DEVICEID] != user_input[CONF_DEVICEID]:
                return self.async_abort(reason="identity_conflict")
            return self.async_abort(reason="already_configured")
        name = user_input.get(CONF_NAME, DEFAULT_NAME)
        return self.async_create_entry(
            title=f"{name} ({serial})",
            data={
                CONF_APIKEY: user_input[CONF_APIKEY],
                CONF_DEVICESN: serial,
                CONF_DEVICEID: user_input[CONF_DEVICEID],
                CONF_NAME: name,
                CONF_HAS_BATTERY: user_input.get(CONF_HAS_BATTERY),
                "yaml_import": True,
            },
            options={
                key: user_input[key]
                for key in (CONF_EVO, CONF_EXTPV, CONF_WAKE_ELEVATION, CONF_WAKE_GRACE)
                if key in user_input
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            api_key = user_input[CONF_APIKEY].strip()
            try:
                await _validate_api(
                    self.hass, api_key, entry.data[CONF_DEVICESN], evo=entry.options.get(CONF_EVO, False)
                )
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_APIKEY: api_key})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_APIKEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FoxESSOptionsFlow()


class FoxESSOptionsFlow(OptionsFlow):
    """Handle FoxESS options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            options = {key: value for key, value in user_input.items() if key != CONF_REDETECT}
            if user_input.get(CONF_REDETECT):
                entry = self.config_entry
                previous = entry.data.get(CONF_CAPABILITIES)
                try:
                    details = await async_device_details(
                        self.hass,
                        entry.data[CONF_APIKEY],
                        entry.data[CONF_DEVICESN],
                        evo=options.get(CONF_EVO, False),
                    )
                    selected = set(previous["entities"]) if valid_record(previous) else compatible_entities(details)
                    data = {}
                    await get_report(self.hass, data, entry.data[CONF_APIKEY], entry.data[CONF_DEVICESN])
                    record = await async_detect(
                        self.hass, entry.data[CONF_APIKEY], entry.data[CONF_DEVICESN], details, selected, data["report"]
                    )
                    if record["outcome"] != "complete":
                        errors["base"] = "detection_inconclusive"
                except ConfigEntryAuthFailed:
                    errors["base"] = "invalid_auth"
                except UpdateFailed:
                    errors["base"] = "cannot_connect"
                if not errors:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_CAPABILITIES: record}, options=options
                    )
            if not errors:
                return self.async_create_entry(title="", data=options)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_EVO, default=options.get(CONF_EVO, False)): bool,
                vol.Optional(CONF_REDETECT, default=False): bool,
                vol.Optional(
                    CONF_WAKE_ELEVATION, default=options.get(CONF_WAKE_ELEVATION, DEFAULT_WAKE_ELEVATION)
                ): NumberSelector(NumberSelectorConfig(min=-6, max=15, step=0.5, mode=NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_WAKE_GRACE, default=options.get(CONF_WAKE_GRACE, DEFAULT_WAKE_GRACE_MINUTES)
                ): NumberSelector(NumberSelectorConfig(min=0, max=180, step=5, mode=NumberSelectorMode.BOX)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
