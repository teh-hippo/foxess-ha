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
from .const import (
    CONF_APIKEY,
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

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        if self.hass.data.get(DOMAIN, {}).get("yaml_configured") or any(
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
                        CONF_EXTPV: False,
                        CONF_EVO: evo,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
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
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_EXTPV, default=options.get(CONF_EXTPV, False)): bool,
                vol.Optional(CONF_EVO, default=options.get(CONF_EVO, False)): bool,
                vol.Optional(
                    CONF_WAKE_ELEVATION, default=options.get(CONF_WAKE_ELEVATION, DEFAULT_WAKE_ELEVATION)
                ): NumberSelector(NumberSelectorConfig(min=-6, max=15, step=0.5, mode=NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_WAKE_GRACE, default=options.get(CONF_WAKE_GRACE, DEFAULT_WAKE_GRACE_MINUTES)
                ): NumberSelector(NumberSelectorConfig(min=0, max=180, step=5, mode=NumberSelectorMode.BOX)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
