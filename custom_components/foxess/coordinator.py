"""Cloud polling with independent sample, request and daylight deadlines."""

from __future__ import annotations

import logging
from datetime import timedelta

from astral.sun import elevation
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.sun import get_astral_observer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DEFAULT_TIMEOUT,
    DeviceNotFound,
    DeviceOffline,
    RateLimited,
    async_device_details,
    async_request,
    number,
    sample_timestamp,
)
from .capabilities import battery_support
from .const import (
    CONF_APIKEY,
    CONF_DEVICESN,
    CONF_EVO,
    CONF_WAKE_ELEVATION,
    CONF_WAKE_GRACE,
    DEFAULT_WAKE_ELEVATION,
    DEFAULT_WAKE_GRACE_MINUTES,
    DOMAIN,
)
from .sun_state import expected_online, operational_state, should_offload, should_raise_issue

_LOGGER = logging.getLogger(__name__)
RAW_MAX_AGE = timedelta(seconds=361)
RAW_INTERVAL = timedelta(minutes=3)
FAULT_GRACE = timedelta(minutes=15)
AUTH_BACKOFF = timedelta(minutes=15)
SCAN_INTERVAL = timedelta(minutes=1)


def _solar_elevation(hass, when):
    return elevation(get_astral_observer(hass), dateandtime=dt_util.as_utc(when))


class FoxESSCoordinator(DataUpdateCoordinator[dict]):
    """Keep missing telemetry separate from cloud and authentication failures."""

    def __init__(self, hass, entry, config):
        super().__init__(hass, _LOGGER, name=config["name"], config_entry=entry, update_interval=SCAN_INTERVAL)
        self.api_key = config[CONF_APIKEY]
        self.serial = config[CONF_DEVICESN]
        self.evo = config.get(CONF_EVO, False)
        self.started_at = dt_util.utcnow()
        self.wake_elevation = config.get(CONF_WAKE_ELEVATION, DEFAULT_WAKE_ELEVATION)
        self.wake_grace = config.get(CONF_WAKE_GRACE, DEFAULT_WAKE_GRACE_MINUTES)
        self.values = {
            "report": {},
            "reportDailyGeneration": {},
            "raw": {},
            "battery": {},
            "addressbook": {},
            "online": False,
            "operational_state": None,
            "hasBattery": None,
            "last_cloud_sync": None,
            "last_cloud_success": None,
            "updated_at": {},
            "device_unavailable": False,
        }
        self.next_raw = dt_util.utcnow()
        self.next_detail = self.next_raw
        self.next_auxiliary = {}
        self.offline_since = None
        self.confirmed_offline = False
        self.fault_since = None
        self.last_error = None
        self.auth_since = None
        self.auth_count = 0
        self._expiry_cancel = None
        self._fault_cancel = None
        self._started_reauth = False
        self._was_offloaded = False

    async def async_shutdown(self):
        for cancel in (self._expiry_cancel, self._fault_cancel):
            if cancel:
                cancel()
        await super().async_shutdown()

    def _authenticated(self):
        self.values["last_cloud_success"] = dt_util.utcnow()
        self.auth_count = 0
        self.auth_since = None
        if self._started_reauth and self.config_entry:
            for flow in self.config_entry.async_get_active_flows(self.hass, {"reauth"}):
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
            self._started_reauth = False

    def _clear_fault(self):
        self.fault_since = None
        self.last_error = None
        if self._fault_cancel:
            self._fault_cancel()
            self._fault_cancel = None

    def _record_failure(self, err, started):
        now = dt_util.utcnow()
        self.confirmed_offline = False
        self.values["operational_state"] = None
        self.last_error = err
        if self.fault_since is None:
            self.fault_since = started
            self._fault_cancel = async_track_point_in_utc_time(self.hass, self._fault_expired, started + FAULT_GRACE)
            _LOGGER.debug("FoxESS request failed; retrying without extending sample freshness: %s", err)
        if isinstance(err, ConfigEntryAuthFailed):
            if self.auth_since is None:
                self.auth_since = started
            self.auth_count += 1
            if self.auth_count >= 3 and now - self.auth_since >= FAULT_GRACE:
                if self.config_entry:
                    self.config_entry.async_start_reauth(self.hass)
                    self._started_reauth = True
                self.next_raw = now + AUTH_BACKOFF
            else:
                self.next_raw = now + SCAN_INTERVAL
        elif isinstance(err, RateLimited):
            self.auth_count = 0
            self.auth_since = None
            self.next_raw = now + timedelta(seconds=err.retry_after)
        else:
            self.auth_count = 0
            self.auth_since = None
            self.next_raw = now + (SCAN_INTERVAL if started == self.fault_since else RAW_INTERVAL)

    @callback
    def _fault_expired(self, now):
        if self.fault_since is not None and dt_util.utcnow() - self.fault_since >= FAULT_GRACE:
            self._fault_cancel = None
            self.values["operational_state"] = None
            self.async_set_update_error(UpdateFailed(str(self.last_error)))

    @callback
    def _expire_sample(self, now):
        sample = self.values["last_cloud_sync"]
        if sample is not None and dt_util.utcnow() < sample + RAW_MAX_AGE:
            return
        if self._expiry_cancel:
            self._expiry_cancel()
        self._expiry_cancel = None
        self.values["raw"] = {}
        self.values["online"] = False
        self._update_status(now)
        self.async_update_listeners()

    def _schedule_freshness(self):
        if self._expiry_cancel:
            self._expiry_cancel()
            self._expiry_cancel = None
        sample = self.values["last_cloud_sync"]
        if self.values["raw"] and sample:
            self._expiry_cancel = async_track_point_in_utc_time(self.hass, self._expire_sample, sample + RAW_MAX_AGE)

    def _update_status(self, now):
        data = self.values
        pv_only = data["hasBattery"] is False
        awake = expected_online(_solar_elevation(self.hass, now), self.wake_elevation)
        issue_id = f"data_unavailable_{self.serial}"
        if data["online"]:
            self.offline_since = None
            data["operational_state"] = "online"
            data["device_unavailable"] = False
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        if pv_only and not awake:
            self.offline_since = None
        if self.last_error is not None or not self.confirmed_offline:
            data["operational_state"] = None
            data["device_unavailable"] = False
            return
        if not pv_only or awake:
            if self.offline_since is None:
                self.offline_since = now
            elapsed = (now - self.offline_since).total_seconds() / 60
        else:
            elapsed = 0
        data["operational_state"] = operational_state(False, pv_only, awake, in_grace=elapsed < self.wake_grace)
        data["device_unavailable"] = (not pv_only or awake) and elapsed >= self.wake_grace
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, issue_id)
        if should_raise_issue(
            "offline" if data["device_unavailable"] else data["operational_state"],
            elapsed,
            self.wake_grace,
            issue is not None,
        ) or (issue is not None and (not issue.active or not issue.is_persistent)):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="data_unavailable" if pv_only else "data_unavailable_battery",
                translation_placeholders={
                    "device_sn": self.serial,
                    "elevation": str(self.wake_elevation),
                    "grace": str(self.wake_grace),
                },
            )
            if issue is None:
                _LOGGER.warning("FoxESS inverter %s has no fresh data after its operating-hours grace", self.serial)

    def _expire_periods(self, now):
        today = dt_util.as_local(now).date()
        for section in ("report", "reportDailyGeneration"):
            updated = self.values["updated_at"].get(section)
            if updated is None:
                continue
            previous = dt_util.as_local(updated).date()
            if previous != today:
                if section == "report":
                    self.values[section] = {}
                else:
                    self.values[section].pop("value", None)
                    if (previous.year, previous.month) != (today.year, today.month):
                        self.values[section].pop("month", None)

    async def _refresh_details(self):
        data = self.values
        details = await async_device_details(self.hass, self.api_key, self.serial, evo=self.evo)
        self._authenticated()
        data["addressbook"] = {**details, "plantName": details.get("stationName")}
        previous_battery = data["hasBattery"]
        data["hasBattery"] = battery_support(details)
        if previous_battery is False and data["hasBattery"] is not False:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"capabilities_{self.serial}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="capabilities_changed",
            )
        self.next_detail = dt_util.utcnow() + timedelta(minutes=3 if str(details["status"]) == "3" else 15)

    async def _async_update_data(self):
        data = self.values
        now = dt_util.utcnow()
        initial = self.data is None
        sample = data["last_cloud_sync"]
        if data["raw"] and sample and now - sample >= RAW_MAX_AGE:
            self._expire_sample(now)
        self._expire_periods(now)
        elev_now = _solar_elevation(self.hass, now)
        offload = (
            self.confirmed_offline
            and self.last_error is None
            and should_offload(
                bool(data["addressbook"]),
                data["online"],
                data["hasBattery"] is False,
                elev_now,
                _solar_elevation(self.hass, now - timedelta(minutes=self.wake_grace)),
                self.wake_elevation,
            )
        )
        if offload:
            self._was_offloaded = True
        else:
            if self._was_offloaded:
                self.next_raw = self.next_detail = now
                self._was_offloaded = False
            if now >= self.next_raw:
                started = now
                try:
                    if not data["addressbook"]:
                        await self._refresh_details()
                    previously_online = data["online"]
                    try:
                        offline_evidence = await get_raw(self.hass, data, self.api_key, self.serial)
                    except DeviceOffline:
                        data["online"] = False
                        data["raw"] = {}
                        offline_evidence = True
                    self._authenticated()
                    self._clear_fault()
                    self.confirmed_offline = not data["online"] and (
                        offline_evidence or str(data["addressbook"]["status"]) == "3"
                    )
                    completed = dt_util.utcnow()
                    self.next_raw = completed + RAW_INTERVAL
                    if data["online"]:
                        # Reserve the full request timeout plus dispatch time before expiry.
                        deadline = data["last_cloud_sync"] + RAW_MAX_AGE - timedelta(seconds=DEFAULT_TIMEOUT + 15)
                        if deadline > completed:
                            self.next_raw = min(self.next_raw, deadline)
                        else:
                            remaining = (data["last_cloud_sync"] + RAW_MAX_AGE - completed).total_seconds()
                            self.next_raw = completed + timedelta(
                                seconds=max(1, min(SCAN_INTERVAL.total_seconds(), remaining / 2))
                            )
                        if not previously_online:
                            self.next_auxiliary["reportDailyGeneration"] = completed
                    self._schedule_freshness()
                except (UpdateFailed, ConfigEntryAuthFailed) as err:
                    if initial:
                        raise
                    self._record_failure(err, started)

            if self.last_error is None:
                if now >= self.next_detail and dt_util.utcnow() + timedelta(seconds=DEFAULT_TIMEOUT) < self.next_raw:
                    try:
                        await self._refresh_details()
                    except (UpdateFailed, ConfigEntryAuthFailed) as err:
                        _LOGGER.warning("FoxESS device details update failed: %s", err)
                        self.next_detail = dt_util.utcnow() + timedelta(minutes=3)
                        if isinstance(err, RateLimited):
                            self._record_failure(err, now)
                for section, query, minutes in (
                    ("reportDailyGeneration", get_generation, 60),
                    ("report", get_report, 15),
                    ("battery", get_battery_settings, 60),
                ):
                    if section == "battery" and data["hasBattery"] is not True:
                        continue
                    if dt_util.utcnow() < self.next_auxiliary.get(section, now):
                        continue
                    if (
                        self.last_error is not None
                        or dt_util.utcnow() + timedelta(seconds=DEFAULT_TIMEOUT) >= self.next_raw
                    ):
                        break
                    try:
                        await query(self.hass, data, self.api_key, self.serial)
                        self._authenticated()
                        data["updated_at"][section] = dt_util.utcnow()
                    except (UpdateFailed, ConfigEntryAuthFailed) as err:
                        _LOGGER.warning("FoxESS %s update failed: %s", section, err)
                        if isinstance(err, RateLimited):
                            self._record_failure(err, now)
                            break
                    self.next_auxiliary[section] = dt_util.utcnow() + timedelta(minutes=minutes)
        self._expire_periods(dt_util.utcnow())
        self._update_status(dt_util.utcnow())
        delay = max(1, (self.next_raw - dt_util.utcnow()).total_seconds())
        self.update_interval = timedelta(seconds=min(60, delay)) if not offload else SCAN_INTERVAL
        if self.fault_since is not None and dt_util.utcnow() - self.fault_since >= FAULT_GRACE:
            raise UpdateFailed(str(self.last_error))
        return data


async def get_battery_settings(hass, data, api_key, serial):
    result, _ = await async_request(hass, "GET", "/op/v0/device/battery/soc/get", api_key, params={"sn": serial})
    if not isinstance(result, dict):
        raise UpdateFailed("FoxESS returned invalid battery settings")
    data["battery"] = {key: number(result[key]) for key in ("minSoc", "minSocOnGrid") if result.get(key) is not None}


async def get_report(hass, data, api_key, serial):
    now = dt_util.now()
    result, _ = await async_request(
        hass,
        "POST",
        "/op/v0/device/report/query",
        api_key,
        payload={
            "sn": serial,
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
    if dt_util.now().date() != now.date():
        _LOGGER.debug("Discarding FoxESS energy report requested for the previous local day")
        data["report"] = {}
        return
    report = {}
    for item in result:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("variable"), str)
            or not isinstance(item.get("values"), list)
        ):
            raise UpdateFailed("FoxESS returned an invalid energy report item")
        value = number(item["values"][now.day - 1]) if len(item["values"]) >= now.day else None
        if value is not None:
            report[item["variable"]] = round(value, 3)
    data["report"] = report


async def get_generation(hass, data, api_key, serial):
    result, _ = await async_request(hass, "GET", "/op/v0/device/generation", api_key, params={"sn": serial})
    if not isinstance(result, dict):
        raise UpdateFailed("FoxESS returned invalid generation data")
    generation = {
        key: number(result[source])
        for key, source in (("value", "today"), ("month", "month"), ("cumulative", "cumulative"))
        if result.get(source) is not None
    }
    sample = data.get("last_cloud_sync")
    today = dt_util.now().date()
    sample_date = dt_util.as_local(sample).date() if sample is not None else None
    if not data.get("online") or sample_date != today:
        generation.pop("value", None)
    if sample_date is None or (sample_date.year, sample_date.month) != (today.year, today.month):
        generation.pop("month", None)
    if "cumulative" not in generation and "cumulative" in data.get("reportDailyGeneration", {}):
        generation["cumulative"] = data["reportDailyGeneration"]["cumulative"]
    data["reportDailyGeneration"] = generation


async def get_raw(hass, data, api_key, serial):
    result, elapsed_ms = await async_request(
        hass, "POST", "/op/v1/device/real/query", api_key, payload={"sns": [serial]}
    )
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise UpdateFailed("FoxESS returned invalid real-time data")
    sample = result[0]
    if sample.get("deviceSN", sample.get("sn", serial)) != serial:
        raise DeviceNotFound("FoxESS returned readings for a different inverter")
    if not isinstance(sample.get("time"), str) or not isinstance(sample.get("datas"), list):
        raise UpdateFailed("FoxESS returned incomplete real-time data")
    sample_time = sample_timestamp(sample["time"])
    if sample_time - dt_util.utcnow() > timedelta(minutes=1):
        raise UpdateFailed("FoxESS sample timestamp is in the future")
    raw = {}
    for item in sample["datas"]:
        if not isinstance(item, dict) or not isinstance(item.get("variable"), str):
            raise UpdateFailed("FoxESS returned an invalid real-time measurement")
        variable, value = item["variable"], item.get("value")
        if value is None:
            continue
        if variable == "runningState":
            code = number(value)
            if code != int(code):
                raise UpdateFailed("FoxESS returned an invalid running state")
            raw[variable] = str(int(code))
            continue
        try:
            value = number(value)
        except UpdateFailed:
            if not isinstance(item["value"], str):
                raise
            _LOGGER.debug("Ignoring text-valued FoxESS variable %s", variable)
            continue
        variable = {"batTemperature_1": "batTemperature", "invBatPower_1": "invBatPower"}.get(variable, variable)
        if variable == "ResidualEnergy":
            scale = {"kWh": 1, "1.0kWh": 1, "0.1kWh": 0.1, "0.01kWh": 0.01}.get(item.get("unit"))
            if scale is None:
                _LOGGER.warning("FoxESS returned an unsupported ResidualEnergy unit")
                continue
            value = round(value * scale, 3)
        elif variable == "PowerFactor" and item.get("unit") == "%":
            value /= 100
        raw[variable] = value
    if dt_util.utcnow() - sample_time >= RAW_MAX_AGE:
        data["online"] = False
        data["raw"] = {}
        return True
    measurements_present = any(key != "runningState" for key in raw)
    previous = data.get("last_cloud_sync")
    if previous is not None and sample_time < previous:
        raise UpdateFailed("FoxESS sample timestamp moved backwards")
    if raw:
        data["last_cloud_sync"] = sample_time
        raw["ResponseTime"] = elapsed_ms
    data["raw"] = raw
    data["online"] = measurements_present
    return data.get("hasBattery") is False and raw.get("runningState") in ("161", "162", "167")
