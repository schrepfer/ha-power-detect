"""Binary sensor platform creating `binary_sensor.<slug>` for `power_detect`."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PowerDetectCoordinator
from .api import PowerStatus, format_snapshot_attributes
from .const import DOMAIN

STATUS_ICONS: dict[PowerStatus, str] = {
    PowerStatus.POWERED: "mdi:power-plug",
    PowerStatus.BATTERY: "mdi:battery-alert",
    PowerStatus.SHUTDOWN: "mdi:power-plug-off",
    PowerStatus.UNKNOWN: "mdi:help-network-outline",
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a `binary_sensor.<slug>` entity per configured `power-detect` server."""
    if discovery_info is None:
        return

    coordinators: list[PowerDetectCoordinator] = hass.data.get(DOMAIN, [])
    async_add_entities(
        [PowerDetectBinarySensor(coordinator) for coordinator in coordinators]
    )


class PowerDetectBinarySensor(
    CoordinatorEntity[PowerDetectCoordinator], BinarySensorEntity
):
    """Binary sensor reporting `on` when mains power is present and `off` during an outage."""

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, coordinator: PowerDetectCoordinator) -> None:
        super().__init__(coordinator)
        cfg = coordinator.server_cfg
        self._attr_unique_id = f"power_detect_{cfg.entity_slug}_power"
        self._attr_name = cfg.name
        self.entity_id = f"binary_sensor.{cfg.entity_slug}"

    @property
    def is_on(self) -> bool | None:
        """Return True when powered, False on battery/shutdown, or None when unknown."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.power_present

    @property
    def icon(self) -> str:
        """Return an icon reflecting the current power state."""
        if not self.coordinator.data:
            return STATUS_ICONS[PowerStatus.UNKNOWN]
        return STATUS_ICONS.get(
            self.coordinator.data.status, STATUS_ICONS[PowerStatus.UNKNOWN]
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose fine-grained status, server URL, reachability, and outage timestamps."""
        if not self.coordinator.data:
            return {}
        return format_snapshot_attributes(
            self.coordinator.server_cfg, self.coordinator.data
        )
