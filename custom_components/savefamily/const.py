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
# Sent with a watch's did once its device is removed, formatted with the entry id.
SIGNAL_WATCH_REMOVED = f"{DOMAIN}_{{}}_watch_removed"
# Sent with a watch's did when another entry takes it over, formatted with the entry id.
SIGNAL_WATCH_HANDED_OVER = f"{DOMAIN}_{{}}_watch_handed_over"
# hass.data keys, shared by all entries: did -> id of the entry that handles the watch, when
# several accounts list it, and did -> highest alarm id seen.
DATA_WATCH_OWNERS = f"{DOMAIN}_watch_owners"
DATA_LAST_ALARM_IDS = f"{DOMAIN}_last_alarm_ids"

POLL_INTERVAL = timedelta(minutes=5)
# A watch missing from every successful poll for this long has left the account.
STALE_DEVICE_AFTER = timedelta(minutes=15)
LOCATION_STALE_AFTER = timedelta(minutes=30)
ONLINE_THRESHOLD = timedelta(minutes=15)
REQUEST_LOCATION_REFRESH_DELAY = 20
# The watch uploads a remote photo some time after the capture command.
TAKE_PHOTO_REFRESH_DELAY = 60
MAX_POSITION_HISTORY = timedelta(days=1)

# Settings change less often than location and are polled independently.
SETTINGS_POLL_INTERVAL = timedelta(minutes=30)
