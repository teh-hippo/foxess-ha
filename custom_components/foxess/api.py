"""Shared FoxESS OpenAPI requests."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, ENDPOINT_OA_DEVICE_DETAIL, ENDPOINT_OA_DOMAIN

DEFAULT_TIMEOUT = 75
REQUEST_INTERVAL = 1.0
_DATA_REQUEST_GATE = "request_gate"


@dataclass
class _RequestGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request: float | None = None


class DeviceNotFound(UpdateFailed):
    """The requested inverter does not belong to this account."""


def _build_foxess_headers(api_key: str, path: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    # FoxESS signs literal backslashes, not CR/LF characters.
    signature_text = rf"{path}\r\n{api_key}\r\n{timestamp}"
    signature = hashlib.md5(signature_text.encode()).hexdigest()  # noqa: S324
    return {
        "token": api_key,
        "timestamp": timestamp,
        "signature": signature,
        "lang": "en",
        "Content-Type": "application/json",
        "User-Agent": "foxess-ha",
    }


async def async_request(
    hass: HomeAssistant,
    method: str,
    path: str,
    api_key: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | list[Any], int]:
    """Return a successful result or a classified, credential-free error."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    gate = domain_data.setdefault(_DATA_REQUEST_GATE, _RequestGate())
    loop = asyncio.get_running_loop()
    async with gate.lock:
        if gate.last_request is not None:
            await asyncio.sleep(max(0.0, REQUEST_INTERVAL - (loop.time() - gate.last_request)))
        gate.last_request = loop.time()
        headers = _build_foxess_headers(api_key, path)

    session = async_get_clientsession(hass)
    started = loop.time()
    try:
        async with session.request(
            method,
            f"{ENDPOINT_OA_DOMAIN}{path}",
            headers=headers,
            params=params,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
        ) as response:
            if response.status in (401, 403):
                raise ConfigEntryAuthFailed("FoxESS rejected the API key")
            if response.status == 429:
                raise UpdateFailed("FoxESS request limit reached")
            if response.status != 200:
                raise UpdateFailed(f"FoxESS returned HTTP {response.status}")
            try:
                data = await response.json()
            except (ValueError, aiohttp.ContentTypeError) as err:
                raise UpdateFailed("FoxESS returned an invalid JSON response") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise UpdateFailed("Unable to connect to FoxESS Cloud") from err

    elapsed_ms = round((loop.time() - started) * 1000)
    if not isinstance(data, dict) or type(data.get("errno")) is not int:
        raise UpdateFailed("FoxESS returned an invalid response envelope")
    errno = data["errno"]
    if errno in (41807, 41808, 41809):
        raise ConfigEntryAuthFailed("FoxESS rejected the API key")
    if errno in (41930, 40261):
        raise DeviceNotFound("FoxESS could not find the requested inverter")
    if errno == 40400:
        raise UpdateFailed("FoxESS request limit reached")
    if errno != 0:
        raise UpdateFailed(f"FoxESS returned API error {errno}")
    result = data.get("result")
    if not isinstance(result, (dict, list)):
        raise UpdateFailed("FoxESS returned an invalid result")
    return result, elapsed_ms


def validate_device(result: Any, device_sn: str) -> dict[str, Any]:
    """Validate identity and the capabilities required before entity creation."""
    if not isinstance(result, dict):
        raise UpdateFailed("FoxESS returned invalid device details")
    if result.get("deviceSN") != device_sn:
        raise DeviceNotFound("FoxESS returned a different inverter")
    if not isinstance(result.get("hasBattery"), bool) or str(result.get("status")) not in ("1", "2", "3"):
        raise UpdateFailed("FoxESS returned incomplete device capabilities")
    return result


async def async_device_details(
    hass: HomeAssistant, api_key: str, device_sn: str, *, evo: bool = False, v1_api: bool = True
) -> dict[str, Any]:
    """Find the same device during setup, polling and reauthentication."""
    if not evo:
        path = ENDPOINT_OA_DEVICE_DETAIL if v1_api else "/op/v0/device/detail"
        result, _ = await async_request(hass, "GET", path, api_key, params={"sn": device_sn})
        return validate_device(result, device_sn)
    page = 1
    while True:
        result, _ = await async_request(
            hass, "POST", "/op/v0/device/list", api_key, payload={"currentPage": page, "pageSize": 10}
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("data"), list)
            or type(result.get("total")) is not int
            or result["total"] < 0
            or result.get("currentPage") != page
            or result.get("pageSize") != 10
        ):
            raise UpdateFailed("FoxESS returned an invalid device list")
        for item in result["data"]:
            if not isinstance(item, dict):
                raise UpdateFailed("FoxESS returned an invalid device list item")
            if item.get("deviceSN") == device_sn:
                return validate_device(item, device_sn)
        if not result["data"] or page * 10 >= result["total"]:
            raise DeviceNotFound("FoxESS could not find the requested inverter")
        page += 1
