"""Constants for the FoxESS Cloud integration."""

DOMAIN = "foxess"

ENDPOINT_OA_DOMAIN = "https://www.foxesscloud.com"
ENDPOINT_OA_DEVICE_DETAIL = "/op/v1/device/detail"

CONF_APIKEY = "apiKey"
CONF_DEVICESN = "deviceSN"
CONF_DEVICEID = "deviceID"
CONF_EXTPV = "extendPV"
CONF_XTZONE = "xtZone"
CONF_GET_VARIABLES = "Restrict"
CONF_V1_API = "Use_V1_Api"
CONF_EVO = "Evo"
CONF_HAS_BATTERY = "hasBattery"

DEFAULT_NAME = "FoxESS"

# Staleness detection — raise a Repairs issue when data has been unavailable
# for too long during expected operating hours.
DATA_STALENESS_HOURS = 6
DATA_STALENESS_WINDOW = 12
DEFAULT_ONLINE_START_HOUR = 6
