"""Sensor platform creating `sensor.<slug>` (`powered`, `battery`, `shutdown`, `unknown`)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PowerDetectCoordinator
from .api import PowerStatus, format_snapshot_attributes
from .const import DOMAIN, VALID_STATUSES

STATUS_ICONS: dict[PowerStatus, str] = {
    PowerStatus.POWERED: "mdi:transmission-tower",
    PowerStatus.BATTERY: "mdi:battery-clock",
    PowerStatus.SHUTDOWN: "mdi:power-off",
    PowerStatus.UNKNOWN: "mdi:lan-disconnect",
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a `sensor.<slug>` enum sensor per configured `power-detect` server."""
    if discovery_info is None:
        return

    coordinators: list[PowerDetectCoordinator] = hass.data.get(DOMAIN, [])
    async_add_entities(
        [PowerDetectStatusSensor(coordinator) for coordinator in coordinators]
    )


class PowerDetectStatusSensor(
    CoordinatorEntity[PowerDetectCoordinator], SensorEntity
):
    """Enum sensor exposing the fine-grained `power-detect` status."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(VALID_STATUSES)

    def __init__(self, coordinator: PowerDetectCoordinator) -> None:
        super().__init__(coordinator)
        cfg = coordinator.server_cfg
        self._attr_unique_id = f"power_detect_{cfg.entity_slug}_status"
        self._attr_name = cfg.name
        self.entity_id = f"sensor.{cfg.entity_slug}"

    @property
    def native_value(self) -> str:
        """Return `powered`, `battery`, `shutdown`, or `unknown`."""
        if not self.coordinator.data:
            return PowerStatus.UNKNOWN.value
        return self.coordinator.data.status.value

    @property
    def icon(self) -> str:
        """Return an icon reflecting the fine-grained status."""
        if not self.coordinator.data:
            return STATUS_ICONS[PowerStatus.UNKNOWN]
        return STATUS_ICONS.get(
            self.coordinator.data.status, STATUS_ICONS[PowerStatus.UNKNOWN]
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose previous status, reachability, server URL, and outage timestamps."""
        if not self.coordinator.data:
            return {}
        return format_snapshot_attributes(
            self.coordinator.server_cfg, self.coordinator.data
        )
