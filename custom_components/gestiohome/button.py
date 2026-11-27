"""Button platform for GestioHome."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GestioHomeConfigEntry, GestioHomeCoordinator
from .entity import GestioHomeEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GestioHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""

    coordinator = entry.runtime_data
    async_add_entities(
        [
            GestioHomeButton(coordinator, "soft_reset", coordinator.async_soft_reset),
            GestioHomeButton(
                coordinator, "import_all_data", coordinator.async_force_sync
            ),
        ]
    )


class GestioHomeButton(GestioHomeEntity, ButtonEntity):
    """A GestioHome action (same buttons as edata)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GestioHomeCoordinator,
        key: str,
        action: Callable[[], Awaitable[None]],
    ) -> None:
        """Initialize the button."""

        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._action = action

    @property
    def available(self) -> bool:
        """Buttons work even while GestioHome fails (that is when they help)."""
        return True

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._action()
