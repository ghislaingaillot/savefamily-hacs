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
ATTR_PERIODS = "periods"

POLL_INTERVAL = timedelta(minutes=5)
LOCATION_STALE_AFTER = timedelta(minutes=30)
ONLINE_THRESHOLD = timedelta(minutes=15)
REQUEST_LOCATION_REFRESH_DELAY = 20

# Settings change less often than location and are polled independently.
DND_POLL_INTERVAL = timedelta(minutes=30)
