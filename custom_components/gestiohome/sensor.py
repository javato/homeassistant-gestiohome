"""Sensor platform for GestioHome.

These sensors are for display only and have no state class, so Home Assistant
does not build statistics from them with the time they were read. The Energy
dashboard uses the external statistics instead (see statistics.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_METERS, SUPPLIES, Supply
from .coordinator import GestioHomeConfigEntry, GestioHomeCoordinator, SupplyData
from .entity import GestioHomeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SupplySensorDescription(SensorEntityDescription):
    """Describes a sensor of one supply."""

    value_fn: Callable[[SupplyData], Any]
    attrs_fn: Callable[[SupplyData], dict[str, Any]] = lambda _: {}


SUPPLY_SENSORS: tuple[SupplySensorDescription, ...] = (
    SupplySensorDescription(
        key="reading",
        value_fn=lambda d: d.last_reading.value if d.last_reading else None,
        attrs_fn=lambda d: {
            "reading_datetime": d.last_reading.datetime if d.last_reading else None,
            "meter_id": d.meter.meter_id,
            "meter_name": d.meter.name,
            "statistic_id": d.stat_id,
        },
        suggested_display_precision=3,
    ),
    SupplySensorDescription(
        key="last_day",
        value_fn=lambda d: d.last_day,
        attrs_fn=lambda d: {"date": d.last_day_date},
        suggested_display_precision=3,
    ),
    SupplySensorDescription(
        key="month",
        value_fn=lambda d: d.month,
        suggested_display_precision=3,
    ),
    SupplySensorDescription(
        key="last_month",
        value_fn=lambda d: d.last_month,
        suggested_display_precision=3,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GestioHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""

    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [GestioHomeInfoSensor(coordinator)]
    entities.extend(
        GestioHomeSupplySensor(coordinator, supply, description)
        for supply in SUPPLIES
        if supply.key in entry.data.get(CONF_METERS, {})
        for description in SUPPLY_SENSORS
    )
    async_add_entities(entities)


class GestioHomeInfoSensor(GestioHomeEntity, SensorEntity):
    """When the newest reading was taken, and what the entry found."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "info"

    def __init__(self, coordinator: GestioHomeCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "info")

    @property
    def native_value(self):
        """Return the time of the newest reading of any supply."""
        times = [
            d.last_reading.datetime
            for d in (self.coordinator.data or {}).values()
            if d.last_reading
        ]
        return max(times, default=None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the dwelling and the statistic IDs."""
        return {
            "dwelling": self.coordinator.dwelling,
            "statistic_ids": [
                d.stat_id for d in (self.coordinator.data or {}).values()
            ],
        }


class GestioHomeSupplySensor(GestioHomeEntity, SensorEntity):
    """A value of one supply (domestic hot water, heating or cooling)."""

    entity_description: SupplySensorDescription

    def __init__(
        self,
        coordinator: GestioHomeCoordinator,
        supply: Supply,
        description: SupplySensorDescription,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(coordinator, f"{supply.key}_{description.key}")
        self.entity_description = description
        self._supply = supply
        self._attr_translation_key = f"{supply.key}_{description.key}"
        self._attr_native_unit_of_measurement = supply.unit
        self._attr_device_class = (
            SensorDeviceClass.WATER
            if supply.unit_class == SensorDeviceClass.VOLUME
            else SensorDeviceClass.ENERGY
        )

    @property
    def _supply_data(self) -> SupplyData | None:
        return (self.coordinator.data or {}).get(self._supply.key)

    @property
    def available(self) -> bool:
        """Unavailable when the dwelling has no such meter (or no data yet)."""
        return super().available and self._supply_data is not None

    @property
    def native_value(self):
        """Return the value."""
        if (data := self._supply_data) is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes."""
        if (data := self._supply_data) is None:
            return {}
        return self.entity_description.attrs_fn(data)
