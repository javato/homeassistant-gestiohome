"""Tests for the statistics with the real date of each consumption."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.gestiohome.api import Reading
from custom_components.gestiohome.const import SUPPLIES
from custom_components.gestiohome.statistics import (
    EPOCH,
    add_statistics,
    build_statistics,
    create_metadata,
    get_last_stat,
    hourly_consumption,
    make_stat_id,
)

from .conftest import build_readings

UTC = dt_util.UTC


def test_consumption_is_spread_over_the_hours() -> None:
    """24 units in 24 hours become 1 unit an hour, on the right hours."""
    readings = [
        Reading(datetime(2026, 10, 31, 12, 0, tzinfo=UTC), 100.0),
        Reading(datetime(2026, 11, 1, 12, 0, tzinfo=UTC), 124.0),
    ]
    hours = list(hourly_consumption(readings, EPOCH))
    assert len(hours) == 24
    assert all(value == pytest.approx(1.0) for _, value in hours)
    # half of it belongs to October: the month closes where it should
    assert sum(v for h, v in hours if h.month == 10) == pytest.approx(12.0)


def test_partial_hours_wait_for_the_next_reading() -> None:
    """Only full hours covered by readings are written."""
    readings = [
        Reading(datetime(2026, 11, 1, 10, 30, tzinfo=UTC), 0.0),
        Reading(datetime(2026, 11, 1, 13, 30, tzinfo=UTC), 3.0),
    ]
    hours = list(hourly_consumption(readings, EPOCH))
    assert [h.hour for h, _ in hours] == [11, 12]


def test_reset_meter_adds_nothing() -> None:
    """A reading below the previous one is a new baseline, not a negative."""
    readings = [
        Reading(datetime(2026, 11, 1, 0, tzinfo=UTC), 50.0),
        Reading(datetime(2026, 11, 1, 2, tzinfo=UTC), 52.0),
        Reading(datetime(2026, 11, 1, 4, tzinfo=UTC), 1.0),
        Reading(datetime(2026, 11, 1, 6, tzinfo=UTC), 3.0),
    ]
    rows = build_statistics(readings, EPOCH, 0.0)
    assert min(r["state"] for r in rows) >= 0
    assert rows[-1]["sum"] == pytest.approx(4.0)


def test_incremental_build_continues_the_sum() -> None:
    """A later build adds only newer hours and carries the sum on."""
    readings = build_readings(10)
    full = build_statistics(readings, EPOCH, 0.0)
    cut = full[100]
    rest = build_statistics(readings, cut["start"], cut["sum"])
    assert rest[0]["start"] == full[101]["start"]
    assert rest[-1]["sum"] == pytest.approx(full[-1]["sum"])


async def test_statistics_are_recorded(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Rows reach the recorder and get_last_stat reads them back."""
    supply = SUPPLIES[0]
    stat_id = make_stat_id("est9", supply.key)
    rows = build_statistics(build_readings(5), EPOCH, 0.0)
    add_statistics(hass, create_metadata(stat_id, supply), rows, "est9")
    await async_wait_recording_done(hass)

    last_start, last_sum = await get_last_stat(hass, stat_id)
    assert last_start == rows[-1]["start"]
    assert last_sum == pytest.approx(rows[-1]["sum"])
    assert last_start - rows[0]["start"] == timedelta(hours=len(rows) - 1)
