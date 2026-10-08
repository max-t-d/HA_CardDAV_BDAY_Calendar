"""Constants for the CardDAV Birthday Calendar integration."""

from datetime import timedelta

DOMAIN = "carddav_bday_calendar"

CONF_ADDRESSBOOKS = "addressbooks"

DEFAULT_URL = "https://contacts.icloud.com"
SCAN_INTERVAL = timedelta(hours=6)
