"""Tests for the statistics with the real date of each consumption."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.gestiohome.api import Reading
from custom_components.gestiohome.statistics import (
    EPOCH,
    add_statistics,
    build_statistics,
    create_metadata,
    daily_consumption,
    get_last_stat,
    hourly_consumption,
    make_stat_id,
    unit_info,
)

from .conftest import build_readings


@pytest.fixture(autouse=True)
def madrid_time_zone():
    """Days are local days of the building."""
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Madrid"))
    yield
    dt_util.set_default_time_zone(previous)


def _local(*args: int) -> datetime:
    return datetime(*args, tzinfo=dt_util.get_time_zone("Europe/Madrid"))


def test_each_reading_in_its_hour() -> None:
    """What was counted since the previous reading goes to the reading's hour."""
    readings = [
        Reading(_local(2026, 10, 30, 12, 30), 100.0),
        Reading(_local(2026, 10, 31, 18, 29), 110.0),
        Reading(_local(2026, 11, 1, 6, 43), 124.0),
    ]
    rows = build_statistics(readings, EPOCH, 0.0)
    assert [dt_util.as_local(r["start"]).isoformat() for r in rows] == [
        "2026-10-31T18:00:00+01:00",
        "2026-11-01T06:00:00+01:00",
    ]
    assert [r["state"] for r in rows] == [10.0, 14.0]
    assert [r["sum"] for r in rows] == [10.0, 24.0]


def test_several_readings_a_day_add_up() -> None:
    """Two readings on a day make two hours and one day, like the web."""
    readings = [
        Reading(_local(2026, 11, 1, 12, 30), 100.0),
        Reading(_local(2026, 11, 2, 8, 0), 103.0),
        Reading(_local(2026, 11, 2, 20, 0), 105.0),
    ]
    assert daily_consumption(readings) == {date(2026, 11, 2): 5.0}
    assert list(hourly_consumption(readings).values()) == [3.0, 2.0]


def test_readings_in_one_hour_share_its_row() -> None:
    """Two readings in the same hour add up in that row."""
    readings = [
        Reading(_local(2026, 11, 1, 12, 30), 100.0),
        Reading(_local(2026, 11, 2, 14, 5), 103.0),
        Reading(_local(2026, 11, 2, 14, 40), 105.0),
    ]
    assert list(hourly_consumption(readings).values()) == [5.0]


def test_unit_info() -> None:
    """GestioHome units map to Home Assistant's; unknown ones are kept."""
    assert unit_info("kWh") == ("kWh", "energy")
    assert unit_info("m3") == ("m³", "volume")
    assert unit_info("Litros") == ("L", "volume")
    assert unit_info("tn") == ("tn", None)
    assert unit_info(None) == (None, None)


def test_days_without_readings_go_to_the_next_reading() -> None:
    """A gap is carried by the next reading's day, as the web does."""
    readings = [
        Reading(_local(2026, 11, 1, 12, 0), 0.0),
        Reading(_local(2026, 11, 4, 12, 0), 9.0),
    ]
    assert daily_consumption(readings) == {date(2026, 11, 4): 9.0}


def test_reset_meter_adds_nothing() -> None:
    """A reading below the previous one is a new baseline, not a negative."""
    readings = [
        Reading(_local(2026, 11, 1, 12), 50.0),
        Reading(_local(2026, 11, 2, 12), 52.0),
        Reading(_local(2026, 11, 3, 12), 1.0),
        Reading(_local(2026, 11, 4, 12), 3.0),
    ]
    rows = build_statistics(readings, EPOCH, 0.0)
    assert [r["state"] for r in rows] == [2.0, 0.0, 2.0]
    assert rows[-1]["sum"] == pytest.approx(4.0)


def test_small_drop_is_a_correction() -> None:
    """A reading slightly below the previous one is subtracted, not a reset."""
    readings = [
        Reading(_local(2026, 6, 28, 20), 84.700),
        Reading(_local(2026, 6, 29, 20), 84.801),
        Reading(_local(2026, 6, 30, 20), 84.685),
        Reading(_local(2026, 7, 1, 20), 84.900),
    ]
    rows = build_statistics(readings, EPOCH, 0.0)
    assert [r["state"] for r in rows] == pytest.approx([0.101, -0.116, 0.215])
    assert rows[-1]["sum"] == pytest.approx(0.2)


def test_incremental_build_rewrites_the_last_rows() -> None:
    """A later build starts again at the last day and carries the sum on."""
    readings = build_readings(10)
    full = build_statistics(readings, EPOCH, 0.0)
    cut = full[4]
    rest = build_statistics(readings, cut["start"], cut["sum"] - cut["state"])
    assert rest[0]["start"] == cut["start"]
    assert rest[0]["sum"] == pytest.approx(cut["sum"])
    assert rest[-1]["sum"] == pytest.approx(full[-1]["sum"])


async def test_statistics_are_recorded(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Rows reach the recorder and get_last_stat reads them back."""
    stat_id = make_stat_id("est9", "calor_2")
    rows = build_statistics(build_readings(5), EPOCH, 0.0)
    add_statistics(hass, create_metadata(stat_id, "Calor", "kWh"), rows, "est9")
    await async_wait_recording_done(hass)

    last_start, last_sum, last_state = await get_last_stat(hass, stat_id)
    assert last_start == rows[-1]["start"]
    assert last_sum == pytest.approx(rows[-1]["sum"])
    assert last_state == pytest.approx(rows[-1]["state"])
