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

from .coordinator import GestioHomeConfigEntry, GestioHomeCoordinator, MeterData
from .entity import GestioHomeEntity
from .statistics import unit_info

PARALLEL_UPDATES = 0


def as_given(value: float | None) -> float | int | None:
    """Return a value with the decimals GestioHome gives, and no more.

    Whole numbers (meters read in whole kWh) show without decimals; the
    others keep theirs (m³ readings come with three).
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


@dataclass(frozen=True, kw_only=True)
class MeterSensorDescription(SensorEntityDescription):
    """Describes a sensor of one meter."""

    value_fn: Callable[[MeterData], Any]
    attrs_fn: Callable[[MeterData], dict[str, Any]] = lambda _: {}


METER_SENSORS: tuple[MeterSensorDescription, ...] = (
    MeterSensorDescription(
        key="reading",
        value_fn=lambda d: d.last_reading.value if d.last_reading else None,
        attrs_fn=lambda d: {
            "reading_datetime": d.last_reading.datetime if d.last_reading else None,
            "meter_id": d.meter_id,
            "statistic_id": d.stat_id,
        },
    ),
    MeterSensorDescription(
        key="last_day",
        value_fn=lambda d: d.last_day,
        attrs_fn=lambda d: {"date": d.last_day_date},
    ),
    MeterSensorDescription(
        key="month",
        value_fn=lambda d: d.month,
    ),
    MeterSensorDescription(
        key="last_month",
        value_fn=lambda d: d.last_month,
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
        GestioHomeMeterSensor(coordinator, meter_id, description)
        for meter_id in coordinator.meters
        for description in METER_SENSORS
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
        """Return the time of the newest reading of any meter."""
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
                self.coordinator.stat_id(meter_id)
                for meter_id in self.coordinator.meters
            ],
        }


class GestioHomeMeterSensor(GestioHomeEntity, SensorEntity):
    """A value of one meter, named after the name the user gave it."""

    entity_description: MeterSensorDescription

    def __init__(
        self,
        coordinator: GestioHomeCoordinator,
        meter_id: str,
        description: MeterSensorDescription,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(coordinator, f"{meter_id}_{description.key}")
        self.entity_description = description
        self._meter_id = meter_id
        self._attr_translation_key = description.key
        self._attr_translation_placeholders = {
            "meter": coordinator.meters[meter_id]["name"]
        }

    @property
    def _meter_data(self) -> MeterData | None:
        return (self.coordinator.data or {}).get(self._meter_id)

    @property
    def available(self) -> bool:
        """Unavailable until there is data for the meter."""
        return super().available and self._meter_data is not None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit GestioHome gives for the meter."""
        data = self._meter_data
        return unit_info(data.unit)[0] if data else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return energy or volume, after the unit."""
        data = self._meter_data
        return unit_info(data.unit)[1] if data else None

    @property
    def suggested_display_precision(self) -> int | None:
        """Show the decimals GestioHome gives this meter, not the HA default."""
        data = self._meter_data
        return data.decimals if data else None

    @property
    def native_value(self):
        """Return the value."""
        if (data := self._meter_data) is None:
            return None
        return as_given(self.entity_description.value_fn(data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes."""
        if (data := self._meter_data) is None:
            return {}
        return self.entity_description.attrs_fn(data)
