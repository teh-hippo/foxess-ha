"""Config flow for FoxESS Cloud integration."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_APIKEY,
    CONF_DEVICEID,
    CONF_DEVICESN,
    CONF_EVO,
    CONF_EXTPV,
    CONF_HAS_BATTERY,
    DEFAULT_NAME,
    DOMAIN,
    ENDPOINT_OA_DEVICE_DETAIL,
    ENDPOINT_OA_DOMAIN,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APIKEY): str,
        vol.Required(CONF_DEVICESN): str,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


def _build_foxess_headers(api_key: str, path: str) -> dict[str, str]:
    """Build authentication headers for the FoxESS OpenAPI."""
    timestamp = str(int(time.time() * 1000))
    # Uses literal \r\n (raw string), matching GetAuth in sensor.py
    signature_text = rf"{path}\r\n{api_key}\r\n{timestamp}"
    signature = hashlib.md5(signature_text.encode()).hexdigest()  # noqa: S324 — FoxESS API requires MD5
    return {
        "token": api_key,
        "timestamp": timestamp,
        "signature": signature,
        "lang": "en",
        "Content-Type": "application/json",
    }


async def _validate_api(session: aiohttp.ClientSession, api_key: str, device_sn: str) -> dict[str, Any]:
    """Validate credentials by calling the FoxESS device detail endpoint."""
    path = ENDPOINT_OA_DEVICE_DETAIL
    url = f"{ENDPOINT_OA_DOMAIN}{path}?sn={device_sn}"
    headers = _build_foxess_headers(api_key, path)

    try:
        async with session.get(url, headers=headers, ssl=False) as resp:
            if resp.status == 401:
                raise ValueError("invalid_auth")
            if resp.status != 200:
                raise ValueError("cannot_connect")
            data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ValueError("cannot_connect") from err

    errno = data.get("errno", -1)
    if errno != 0:
        msg = data.get("msg", "").lower()
        if errno in (41807, 41808, 41809, 40256) or "token" in msg or "sign" in msg:
            raise ValueError("invalid_auth")
        if errno in (41930, 40261, 40257) or "device" in msg:
            raise ValueError("device_not_found")
        if errno == 40400:
            raise ValueError("cannot_connect")
        raise ValueError("unknown")

    result = data.get("result")
    if result is None:
        raise ValueError("unknown")
    return result


class FoxESSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FoxESS Cloud."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        # Abort if YAML platform is already configured
        for state in self.hass.states.async_all("sensor"):
            if state.entity_id.startswith("sensor.foxess_"):
                return self.async_abort(reason="yaml_in_use")

        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_APIKEY]
            device_sn = user_input[CONF_DEVICESN]
            name = user_input.get(CONF_NAME, DEFAULT_NAME)

            session = async_get_clientsession(self.hass)
            try:
                result = await _validate_api(session, api_key, device_sn)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                await self.async_set_unique_id(device_sn)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"{name} ({device_sn})",
                    data={
                        CONF_APIKEY: api_key,
                        CONF_DEVICESN: device_sn,
                        CONF_DEVICEID: device_sn,
                        CONF_NAME: name,
                        CONF_HAS_BATTERY: bool(result.get("hasBattery")),
                    },
                    options={
                        CONF_EXTPV: False,
                        CONF_EVO: False,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
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
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EXTPV,
                        default=options.get(CONF_EXTPV, False),
                    ): bool,
                    vol.Optional(
                        CONF_EVO,
                        default=options.get(CONF_EVO, False),
                    ): bool,
                }
            ),
        )
