"""Fixtures for the GestioHome tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from custom_components.gestiohome.api import Reading

pytest_plugins = "pytest_homeassistant_custom_component"


def build_readings(days: int, start: datetime | None = None) -> list[Reading]:
    """One reading a day at varying times, 10 units a day."""
    tz = dt_util.get_default_time_zone()
    start = start or datetime(2026, 10, 1, 12, 30, tzinfo=tz)
    return [
        Reading(start + timedelta(days=i, minutes=7 * (i % 5)), 1000.0 + 10 * i)
        for i in range(days)
    ]


def payload(meter_id: str, readings: list[Reading]) -> dict:
    """Build a getStatisticData.php answer (JavaScript months)."""
    return {
        meter_id: {
            "xYear": [str(r.datetime.year) for r in readings],
            "xMonth": [str(r.datetime.month - 1) for r in readings],
            "xDay": [str(r.datetime.day) for r in readings],
            "xHour": [str(r.datetime.hour) for r in readings],
            "xMinute": [str(r.datetime.minute) for r in readings],
            "xSecond": [str(r.datetime.second) for r in readings],
            "yvalues": [r.value for r in readings],
            "hayDatos": [True],
            "seriesUnit": "kWh",
        }
    }
