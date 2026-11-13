"""HTTP client, status parser, and state-transition tracker for `power-detect`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

try:
    import aiohttp
except ImportError:  # pragma: no cover - allows standalone CLI/unit tests without HA venv
    aiohttp = None  # type: ignore[assignment]

if TYPE_CHECKING:
    import aiohttp

from .const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SLUG,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLUG,
    DEFAULT_TIMEOUT,
    EVENT_POWER_DETECT,
    STATUS_BATTERY,
    STATUS_POWERED,
    STATUS_SHUTDOWN,
    STATUS_UNKNOWN,
)


class PowerDetectError(Exception):
    """Raised when fetching or parsing a `power-detect` endpoint fails."""


class PowerStatus(StrEnum):
    """Status values reported by `power-detect`."""

    POWERED = STATUS_POWERED
    BATTERY = STATUS_BATTERY
    SHUTDOWN = STATUS_SHUTDOWN
    UNKNOWN = STATUS_UNKNOWN


def slugify_name(value: str) -> str:
    """Convert a display name or host into a clean Home Assistant entity slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or DEFAULT_SLUG


def build_server_url(
    url: str | None = None,
    host: str | None = None,
    port: int = DEFAULT_PORT,
) -> str:
    """Validate and normalize a `power-detect` endpoint URL from `url` or `host`+`port`."""
    if url and str(url).strip():
        cleaned = str(url).strip()
        if "://" not in cleaned:
            cleaned = f"http://{cleaned}"
        parts = urlsplit(cleaned)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise PowerDetectError(f"Invalid power-detect URL: {url!r}")
        path = "" if parts.path == "/" and not parts.query else parts.path
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))

    if host and str(host).strip():
        cleaned_host = str(host).strip()
        if "://" in cleaned_host:
            return build_server_url(url=cleaned_host, port=port)
        if not (1 <= int(port) <= 65535):
            raise PowerDetectError(f"Invalid port: {port!r}")
        if ":" in cleaned_host and not cleaned_host.startswith("["):
            cleaned_host = f"[{cleaned_host}]"
        return f"http://{cleaned_host}:{int(port)}"

    raise PowerDetectError("Either 'url' or 'host' must be configured")


def _derive_name_from_url(url: str) -> str:
    """Derive a human-friendly name from a URL hostname when multiple unnamed servers exist."""
    hostname = urlsplit(url).hostname or DEFAULT_SLUG
    first_label = hostname.split(".")[0] or DEFAULT_SLUG
    pretty = re.sub(r"[^a-zA-Z0-9]+", " ", first_label).strip().title()
    if not pretty:
        return DEFAULT_NAME
    if "power" not in pretty.lower() and "ups" not in pretty.lower():
        return f"{pretty} Power"
    return pretty


@dataclass(slots=True)
class ServerConfig:
    """Resolved runtime configuration for a single monitored `power-detect` server."""

    name: str
    entity_slug: str
    url: str
    scan_interval: timedelta = DEFAULT_SCAN_INTERVAL
    timeout: float = DEFAULT_TIMEOUT


def resolve_server_configs(raw_entries: list[dict[str, Any]]) -> list[ServerConfig]:
    """Resolve names, deduplicated entity slugs, URLs, and intervals for configured servers."""
    if not raw_entries:
        raise PowerDetectError("At least one power-detect server must be configured")

    single_server = len(raw_entries) == 1
    intermediate: list[tuple[str, str, str, timedelta, float]] = []
    slug_counts: dict[str, int] = {}

    for entry in raw_entries:
        url = build_server_url(
            url=entry.get(CONF_URL),
            host=entry.get(CONF_HOST),
            port=entry.get(CONF_PORT, DEFAULT_PORT),
        )
        raw_name = str(entry.get(CONF_NAME) or "").strip()
        raw_slug = str(entry.get(CONF_SLUG) or "").strip()

        if raw_name and raw_slug:
            name = raw_name
            base_slug = slugify_name(raw_slug)
        elif raw_name:
            name = raw_name
            base_slug = slugify_name(raw_name)
        elif raw_slug:
            base_slug = slugify_name(raw_slug)
            name = (
                DEFAULT_NAME
                if base_slug == DEFAULT_SLUG
                else base_slug.replace("_", " ").title()
            )
        elif single_server:
            name = DEFAULT_NAME
            base_slug = DEFAULT_SLUG
        else:
            name = _derive_name_from_url(url)
            base_slug = slugify_name(name)

        raw_interval = entry.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        scan_interval = (
            raw_interval
            if isinstance(raw_interval, timedelta)
            else timedelta(seconds=float(raw_interval))
        )
        timeout = float(entry.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))

        slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
        intermediate.append((name, base_slug, url, scan_interval, timeout))

    seen_indices: dict[str, int] = {}
    resolved: list[ServerConfig] = []
    for name, base_slug, url, scan_interval, timeout in intermediate:
        if slug_counts[base_slug] > 1:
            idx = seen_indices.get(base_slug, 0) + 1
            seen_indices[base_slug] = idx
            entity_slug = f"{base_slug}_{idx}"
            display_name = f"{name} {idx}" if name == DEFAULT_NAME else name
        else:
            entity_slug = base_slug
            display_name = name

        resolved.append(
            ServerConfig(
                name=display_name,
                entity_slug=entity_slug,
                url=url,
                scan_interval=scan_interval,
                timeout=timeout,
            )
        )

    return resolved


def parse_status_response(raw_text: str) -> PowerStatus:
    """Parse a plaintext HTTP body from `power-detect` into a `PowerStatus`."""
    cleaned = raw_text.strip().lower()
    try:
        return PowerStatus(cleaned)
    except ValueError as err:
        preview = raw_text.strip()[:80]
        raise PowerDetectError(f"Unexpected status response: {preview!r}") from err


def is_power_present(status: PowerStatus) -> bool | None:
    """Map `PowerStatus` to binary sensor state (`True`=powered, `False`=outage, `None`=unknown)."""
    if status == PowerStatus.POWERED:
        return True
    if status in (PowerStatus.BATTERY, PowerStatus.SHUTDOWN):
        return False
    return None


@dataclass(slots=True)
class PowerEvent:
    """Home Assistant bus event emitted on a `PowerStatus` transition."""

    event_type: str
    data: dict[str, Any]


@dataclass(slots=True)
class PowerSnapshot:
    """Point-in-time state snapshot for a monitored `power-detect` server."""

    status: PowerStatus
    previous_status: PowerStatus | None
    last_known_status: PowerStatus | None
    power_present: bool | None
    reachable: bool
    last_checked: datetime
    last_changed: datetime
    outage_started: datetime | None
    last_error: str | None = None


def format_snapshot_attributes(
    cfg: ServerConfig, snapshot: PowerSnapshot
) -> dict[str, Any]:
    """Format state attributes shared by both `binary_sensor` and `sensor` entities."""
    return {
        "name": cfg.name,
        "url": cfg.url,
        "status": snapshot.status.value,
        "previous_status": (
            snapshot.previous_status.value if snapshot.previous_status else None
        ),
        "last_known_status": (
            snapshot.last_known_status.value if snapshot.last_known_status else None
        ),
        "reachable": snapshot.reachable,
        "last_checked": snapshot.last_checked.isoformat(timespec="seconds"),
        "last_changed": snapshot.last_changed.isoformat(timespec="seconds"),
        "outage_started": (
            snapshot.outage_started.isoformat(timespec="seconds")
            if snapshot.outage_started
            else None
        ),
        "last_error": snapshot.last_error,
    }


class PowerTrackerState:
    """Tracks `PowerStatus` transitions, outage durations, and `power_detect` bus events."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._status: PowerStatus | None = None
        self._previous_status: PowerStatus | None = None
        self._last_known_status: PowerStatus | None = None
        self._last_changed: datetime | None = None
        self._outage_started: datetime | None = None

    def record_poll(
        self,
        status: PowerStatus,
        *,
        reachable: bool,
        now: datetime,
        error: str | None = None,
    ) -> tuple[PowerSnapshot, PowerEvent | None]:
        """Record a poll result and return the updated snapshot plus an optional transition event."""
        prior_status = self._status
        prior_known = self._last_known_status
        prior_outage_started = self._outage_started

        if status in (PowerStatus.BATTERY, PowerStatus.SHUTDOWN):
            if self._outage_started is None:
                self._outage_started = now
        elif status == PowerStatus.POWERED:
            self._outage_started = None

        event: PowerEvent | None = None

        if prior_status is None:
            # Initial startup poll: initialize state without firing transition events.
            self._status = status
            self._previous_status = None
            self._last_changed = now
            if status != PowerStatus.UNKNOWN:
                self._last_known_status = status
        elif status != prior_status:
            self._previous_status = prior_status
            self._status = status
            self._last_changed = now

            was_outage = prior_known in (PowerStatus.BATTERY, PowerStatus.SHUTDOWN)
            relevant_outage_start = (
                prior_outage_started
                if status == PowerStatus.POWERED
                else self._outage_started
            )
            outage_duration = (
                round(max(0.0, (now - relevant_outage_start).total_seconds()), 1)
                if relevant_outage_start is not None
                else None
            )

            if status != PowerStatus.UNKNOWN:
                self._last_known_status = status

            event_data: dict[str, Any] = {
                "name": self.config.name,
                "slug": self.config.entity_slug,
                "entity_id": f"sensor.{self.config.entity_slug}",
                "binary_sensor_id": f"binary_sensor.{self.config.entity_slug}",
                "url": self.config.url,
                "status": status.value,
                "previous_status": prior_status.value,
                "last_known_status": prior_known.value if prior_known else None,
                "power_present": is_power_present(status),
                "reachable": reachable,
                "was_outage": was_outage,
                "changed_at": now.isoformat(timespec="seconds"),
                "outage_started": (
                    relevant_outage_start.isoformat(timespec="seconds")
                    if relevant_outage_start
                    else None
                ),
                "outage_duration_seconds": outage_duration,
            }
            if error:
                event_data["error"] = error

            event = PowerEvent(EVENT_POWER_DETECT, event_data)

        assert self._status is not None
        assert self._last_changed is not None

        snapshot = PowerSnapshot(
            status=self._status,
            previous_status=self._previous_status,
            last_known_status=self._last_known_status,
            power_present=is_power_present(self._status),
            reachable=reachable,
            last_checked=now,
            last_changed=self._last_changed,
            outage_started=self._outage_started,
            last_error=error,
        )
        return snapshot, event


class PowerDetectClient:
    """Async HTTP client scoped to a single `power-detect` server URL."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        request_timeout: float = DEFAULT_TIMEOUT,
        retries: int = 1,
        retry_delay: float = 0.25,
    ) -> None:
        self._session = session
        self.url = url
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._retries = max(0, retries)
        self._retry_delay = retry_delay

    async def async_fetch_status(self) -> PowerStatus:
        """Fetch plaintext status (`powered`, `battery`, `shutdown`, `unknown`) from the server."""
        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with self._session.get(self.url, timeout=self._timeout) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    return parse_status_response(text)
            except (
                aiohttp.ClientError,
                TimeoutError,
                ValueError,
                PowerDetectError,
            ) as err:
                last_err = err
                if attempt < self._retries:
                    await asyncio.sleep(self._retry_delay)

        raise PowerDetectError(f"Error fetching {self.url}: {last_err}") from last_err
