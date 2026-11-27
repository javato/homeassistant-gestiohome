"""GestioHome entity definition."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GestioHomeCoordinator


class GestioHomeEntity(CoordinatorEntity[GestioHomeCoordinator]):
    """Representation of any GestioHome entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GestioHomeCoordinator, key: str) -> None:
        """Initialize the entity."""

        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.id)},
            name=f"GestioHome {coordinator.id.upper()}",
            manufacturer="Usanca Metering Solutions",
            model="GestioHome",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.base_url,
        )
