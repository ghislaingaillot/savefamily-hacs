from __future__ import annotations

from datetime import timedelta

DOMAIN = "savefamily"
TITLE = "SaveFamily"
MANUFACTURER = "SaveFamily"

CONF_LOGINNAME = "loginname"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_APP_ID = "app_id"

SERVICE_SET_DND_SCHEDULE = "set_dnd_schedule"
SERVICE_SET_SOS_NUMBERS = "set_sos_numbers"
SERVICE_CALL_BACK = "call_back"
SERVICE_GET_POSITION_HISTORY = "get_position_history"
ATTR_PERIODS = "periods"
ATTR_NUMBERS = "numbers"
ATTR_PHONE_NUMBER = "phone_number"
ATTR_START = "start"
ATTR_END = "end"

EVENT_ALARM = f"{DOMAIN}_alarm"

POLL_INTERVAL = timedelta(minutes=5)
LOCATION_STALE_AFTER = timedelta(minutes=30)
ONLINE_THRESHOLD = timedelta(minutes=15)
REQUEST_LOCATION_REFRESH_DELAY = 20
# The watch uploads a remote photo some time after the capture command.
TAKE_PHOTO_REFRESH_DELAY = 60
MAX_POSITION_HISTORY = timedelta(days=1)

# Settings change less often than location and are polled independently.
SETTINGS_POLL_INTERVAL = timedelta(minutes=30)
