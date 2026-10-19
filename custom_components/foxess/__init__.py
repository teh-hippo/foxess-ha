"""The FoxESS Cloud integration."""

from __future__ import annotations

from homeassistant.config import config_per_platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .capabilities import KNOWN_ENTITIES
from .const import CONF_DEVICEID, CONF_DEVICESN, CONF_EVO, CONF_WAKE_ELEVATION, CONF_WAKE_GRACE, DOMAIN
from .sensor import async_prepare_entry

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the FoxESS integration from YAML."""
    if not any(platform == DOMAIN for platform, _ in config_per_platform(config, "sensor")):
        ir.async_delete_issue(hass, DOMAIN, "yaml_migration")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FoxESS Cloud from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    if entry.data.get("yaml_import"):
        registry = er.async_get(hass)
        identities = {f"{entry.data[CONF_DEVICEID]}{suffix}" for suffix in KNOWN_ENTITIES}
        for entity in list(registry.entities.values()):
            if entity.platform == DOMAIN and entity.config_entry_id is None and entity.unique_id in identities:
                registry.async_update_entity(entity.entity_id, config_entry_id=entry.entry_id)
    entry.runtime_data = await async_prepare_entry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a FoxESS config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Keep legacy identity while retiring operational YAML and obsolete options."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            version=2,
            minor_version=1,
            unique_id=entry.unique_id or entry.data[CONF_DEVICESN],
            options={
                key: value
                for key, value in entry.options.items()
                if key in (CONF_EVO, CONF_WAKE_ELEVATION, CONF_WAKE_GRACE, "extendPV")
            },
        )
    return True
