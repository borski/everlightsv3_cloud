"""Constants for the Everlights v3 Cloud integration."""
from __future__ import annotations

from datetime import timedelta
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "everlightsv3_cloud"
# Domain of the original local integration (theOrakle/everlightsv3).
LEGACY_DOMAIN = "everlights"

CLOUD_URL = "https://appserver-v2.myeverlights.com/v1"
REMOTE_URL = "https://remote.myeverlights.com/bridges"

UPDATE_INTERVAL = timedelta(seconds=60)
ACCOUNT_REFRESH_INTERVAL = timedelta(hours=1)
SEQUENCES_REFRESH_INTERVAL = timedelta(hours=6)
# A zone that has not answered its bridge for this long is shown unavailable.
ZONE_STALE_AFTER = timedelta(minutes=15)

OFF_SEQUENCE: dict[str, list] = {"pattern": [], "effects": []}
DEFAULT_COLOR = "FFFFFF"
