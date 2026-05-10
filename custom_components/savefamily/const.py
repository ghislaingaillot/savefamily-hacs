from __future__ import annotations

from datetime import timedelta

from .core.protocol import DEFAULT_APP_ID, DEFAULT_REGION

DOMAIN = "savefamily"
TITLE = "SaveFamily"
MANUFACTURER = "SaveFamily"

CONF_LOGINNAME = "loginname"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_APP_ID = "app_id"

POLL_INTERVAL = timedelta(minutes=5)
LOCATION_STALE_AFTER = timedelta(minutes=30)
ONLINE_THRESHOLD = timedelta(minutes=15)
REQUEST_LOCATION_REFRESH_DELAY = 20
