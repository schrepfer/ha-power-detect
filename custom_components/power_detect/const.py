"""Constants for the Power Detect integration (`power_detect`)."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "power_detect"

DEFAULT_NAME = "Power Detect"
DEFAULT_SLUG = "power_detect"
DEFAULT_PORT = 1999
DEFAULT_TIMEOUT = 3.0
DEFAULT_SCAN_INTERVAL = timedelta(seconds=10)

# Configuration keys
CONF_SERVERS = "servers"
CONF_URL = "url"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_NAME = "name"
CONF_SLUG = "slug"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TIMEOUT = "timeout"

# Power statuses served by https://github.com/schrepfer/power-detect
STATUS_POWERED = "powered"
STATUS_BATTERY = "battery"
STATUS_SHUTDOWN = "shutdown"
STATUS_UNKNOWN = "unknown"

VALID_STATUSES: tuple[str, ...] = (
    STATUS_POWERED,
    STATUS_BATTERY,
    STATUS_SHUTDOWN,
    STATUS_UNKNOWN,
)
