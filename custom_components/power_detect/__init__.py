"""The Power Detect integration (`power_detect`) for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import (
    PowerDetectClient,
    PowerDetectError,
    PowerSnapshot,
    PowerStatus,
    PowerTrackerState,
    ServerConfig,
    resolve_server_configs,
)
from .const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SERVERS,
    CONF_SLUG,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: tuple[Platform, ...] = (Platform.BINARY_SENSOR, Platform.SENSOR)


def _validate_url_or_host(conf: dict[str, Any]) -> dict[str, Any]:
    """Ensure exactly one of `url` or `host` is provided on a server entry."""
    has_url = bool(conf.get(CONF_URL))
    has_host = bool(conf.get(CONF_HOST))
    if not has_url and not has_host:
        raise vol.Invalid("Must specify either 'url' or 'host'")
    if has_url and has_host:
        raise vol.Invalid("Specify either 'url' or 'host', not both")
    return conf


TIMEOUT_VALIDATOR = vol.All(vol.Coerce(float), vol.Range(min=0.5, max=60.0))

SERVER_ITEM_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(CONF_URL): cv.string,
            vol.Optional(CONF_HOST): cv.string,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
            vol.Optional(CONF_NAME): cv.string,
            vol.Optional(CONF_SLUG): cv.string,
            vol.Optional(CONF_SCAN_INTERVAL): cv.positive_time_period,
            vol.Optional(CONF_TIMEOUT): TIMEOUT_VALIDATOR,
        }
    ),
    _validate_url_or_host,
)

MULTI_SERVER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVERS): vol.All(cv.ensure_list, [SERVER_ITEM_SCHEMA]),
        vol.Optional(
            CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
        ): cv.positive_time_period,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): TIMEOUT_VALIDATOR,
    }
)


def _normalize_domain_config(conf: dict[str, Any]) -> dict[str, Any]:
    """Support either flat single-server config or a `servers:` list."""
    if not isinstance(conf, dict):
        raise vol.Invalid("Expected a dictionary for 'power_detect' configuration")

    if CONF_SERVERS in conf:
        validated = MULTI_SERVER_SCHEMA(conf)
        default_interval = validated[CONF_SCAN_INTERVAL]
        default_timeout = validated[CONF_TIMEOUT]
        servers: list[dict[str, Any]] = []
        for item in validated[CONF_SERVERS]:
            merged = dict(item)
            merged.setdefault(CONF_SCAN_INTERVAL, default_interval)
            merged.setdefault(CONF_TIMEOUT, default_timeout)
            servers.append(merged)
        return {CONF_SERVERS: servers}

    single = dict(SERVER_ITEM_SCHEMA(conf))
    single.setdefault(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    single.setdefault(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    return {CONF_SERVERS: [single]}


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): _normalize_domain_config,
    },
    extra=vol.ALLOW_EXTRA,
)


class PowerDetectCoordinator(DataUpdateCoordinator[PowerSnapshot]):
    """Polls a single `power-detect` HTTP server and fires `power_detect` bus events."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: PowerDetectClient,
        server_cfg: ServerConfig,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{server_cfg.entity_slug}",
            update_interval=server_cfg.scan_interval,
        )
        self.client = client
        self.server_cfg = server_cfg
        self.tracker = PowerTrackerState(server_cfg)
        self._was_unreachable = False

    async def _async_update_data(self) -> PowerSnapshot:
        """Poll `power-detect`, update state machine, and emit `power_detect` event on change."""
        now = dt_util.now()
        try:
            status = await self.client.async_fetch_status()
        except PowerDetectError as err:
            if not self._was_unreachable:
                _LOGGER.warning(
                    "Unable to reach %s (%s): %s; status is now 'unknown'",
                    self.server_cfg.name,
                    self.server_cfg.url,
                    err,
                )
                self._was_unreachable = True
            snapshot, event = self.tracker.record_poll(
                PowerStatus.UNKNOWN,
                reachable=False,
                now=now,
                error=str(err),
            )
        else:
            if self._was_unreachable:
                _LOGGER.info(
                    "Reconnected to %s (%s): status='%s'",
                    self.server_cfg.name,
                    self.server_cfg.url,
                    status.value,
                )
                self._was_unreachable = False
            snapshot, event = self.tracker.record_poll(
                status,
                reachable=True,
                now=now,
            )

        if event is not None:
            self._log_transition(snapshot)
            self.hass.bus.async_fire(event.event_type, event.data)

        return snapshot

    def _log_transition(self, snapshot: PowerSnapshot) -> None:
        """Log meaningful status transitions cleanly."""
        prev = snapshot.previous_status.value if snapshot.previous_status else "none"
        curr = snapshot.status.value
        msg = "%s status changed: %s -> %s (%s)"
        args = (self.server_cfg.name, prev, curr, self.server_cfg.url)

        if snapshot.status == PowerStatus.SHUTDOWN:
            _LOGGER.error(msg, *args)
        elif snapshot.status in (PowerStatus.BATTERY, PowerStatus.UNKNOWN):
            _LOGGER.warning(msg, *args)
        else:
            _LOGGER.info(msg, *args)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up `power_detect:` from `configuration.yaml`."""
    if DOMAIN not in config:
        return True

    conf: dict[str, Any] = config[DOMAIN]
    server_configs = resolve_server_configs(conf[CONF_SERVERS])

    session = async_get_clientsession(hass)
    coordinators: list[PowerDetectCoordinator] = []

    for server_cfg in server_configs:
        client = PowerDetectClient(
            session=session,
            url=server_cfg.url,
            request_timeout=server_cfg.timeout,
        )
        coord = PowerDetectCoordinator(
            hass=hass,
            client=client,
            server_cfg=server_cfg,
        )
        await coord.async_refresh()
        coordinators.append(coord)

    hass.data[DOMAIN] = coordinators
    for platform in PLATFORMS:
        hass.async_create_task(
            async_load_platform(hass, platform, DOMAIN, {}, config)
        )
    return True
