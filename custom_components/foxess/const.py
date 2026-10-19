"""Constants for the FoxESS Cloud integration."""

DOMAIN = "foxess"

ENDPOINT_OA_DOMAIN = "https://www.foxesscloud.com"
ENDPOINT_OA_DEVICE_DETAIL = "/op/v1/device/detail"

CONF_APIKEY = "apiKey"
CONF_DEVICESN = "deviceSN"
CONF_DEVICEID = "deviceID"
CONF_EXTPV = "extendPV"
CONF_EVO = "Evo"
CONF_HAS_BATTERY = "hasBattery"
CONF_CAPABILITIES = "capabilities"
CONF_REDETECT = "redetect"

DEFAULT_NAME = "FoxESS"

# Sun-aware sleep/offline handling. A PV-only inverter is expected to be online
# only while the sun is above the wake elevation; a Repairs issue is raised only
# if it stays offline once the sun has been sufficiently up for the grace window.
CONF_WAKE_ELEVATION = "wake_elevation"
CONF_WAKE_GRACE = "wake_grace_minutes"

DEFAULT_WAKE_ELEVATION = 5  # degrees of solar elevation
DEFAULT_WAKE_GRACE_MINUTES = 60  # minutes
