"""Long-term statistics with the real date of each consumption.

Same strategy as edata: external statistics (``gestiohome:<scode>_<supply>``)
written with ``async_add_external_statistics``, with ``state`` the
consumption of the period and ``sum`` the running total. Each sync continues
from the last recorded row and its sum, so totals never reset.

GestioHome serves absolute meter readings (about one a day, at varying times),
not hourly consumption like Datadis. So there is one row per day, at local
midnight (statistics rows must start on the hour), with what the meter counted
since the previous reading, assigned to the day of the reading as the web does.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging

from homeassistant.components.recorder import util as recorder_util
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import Reading
from .const import DOMAIN, Supply

_LOGGER = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=dt_util.UTC)
# decimals kept in values (ACS meters read litres: 0.001 m³)
DECIMALS = 6


def make_stat_id(integration_id: str, suffix: str) -> str:
    """Create a statistic ID."""
    return f"{DOMAIN}:{integration_id}_{suffix}"


def create_metadata(stat_id: str, supply: Supply) -> StatisticMetaData:
    """Create the metadata of a supply statistic."""
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=stat_id,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=supply.unit_class,
        unit_of_measurement=supply.unit,
    )


async def get_last_stat(
    hass: HomeAssistant, stat_id: str
) -> tuple[datetime, float, float]:
    """Return the (start, cumulative sum, state) of the last recorded row."""

    recorder = recorder_util.get_instance(hass)
    last_stats = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum", "state"}
    )
    if not (rows := last_stats.get(stat_id)):
        return EPOCH, 0.0, 0.0
    row = rows[0]
    start = row.get("start")
    return (
        dt_util.utc_from_timestamp(start) if start is not None else EPOCH,
        row.get("sum") or 0.0,
        row.get("state") or 0.0,
    )


async def get_sum_before(hass: HomeAssistant, stat_id: str, before: datetime) -> float:
    """Return the running total of the last row that starts before ``before``.

    Looks back a month first and then over the whole history (as edata does),
    so a gap in the data does not restart the sum from 0.
    """

    recorder = recorder_util.get_instance(hass)
    for start in (before - timedelta(days=31), EPOCH):
        stats = await recorder.async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            before,
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
        if rows := stats.get(stat_id):
            return rows[-1].get("sum") or 0.0
    return 0.0


async def get_integration_stat_ids(
    hass: HomeAssistant, integration_id: str
) -> list[str]:
    """Get all statistic IDs of this integration entry."""

    preamble = f"{DOMAIN}:{integration_id}_"
    recorder = recorder_util.get_instance(hass)
    all_ids = await recorder.async_add_executor_job(list_statistic_ids, hass)
    return [
        x["statistic_id"] for x in all_ids if x["statistic_id"].startswith(preamble)
    ]


async def clear_statistics(
    hass: HomeAssistant, stat_ids: list[str], scode: str
) -> None:
    """Clear statistics and wait until the recorder has done it."""

    if not stat_ids:
        return
    _LOGGER.warning("%s: clearing statistics %s", scode, stat_ids)
    done = hass.loop.create_future()

    def _on_done() -> None:
        hass.loop.call_soon_threadsafe(done.set_result, None)

    recorder_util.get_instance(hass).async_clear_statistics(stat_ids, on_done=_on_done)
    await done


def continuous_counter(readings: list[Reading]) -> list[tuple[float, float]]:
    """Return (UTC timestamp, total consumed so far) for each reading.

    The total only grows: a reading below the previous one (a replaced or reset
    meter) adds nothing and becomes the new baseline.
    """

    points: list[tuple[float, float]] = []
    total = 0.0
    previous: float | None = None
    for reading in readings:
        if previous is not None:
            if reading.value >= previous:
                total += reading.value - previous
            else:
                _LOGGER.warning(
                    "Meter reading went down at %s (%s -> %s), taking it as a reset",
                    reading.datetime,
                    previous,
                    reading.value,
                )
        previous = reading.value
        points.append((reading.datetime.timestamp(), total))
    return points


def daily_consumption(readings: list[Reading]) -> dict[date, float]:
    """Return the consumption of each local day that has readings.

    Like the GestioHome web: what the meter counted since the previous reading
    goes to the day of the new reading. A day without readings gets nothing and
    the next reading carries it.
    """

    points = continuous_counter(readings)
    days: dict[date, float] = {}
    for reading, (_, total), (_, before) in zip(
        readings[1:], points[1:], points, strict=False
    ):
        day = dt_util.as_local(reading.datetime).date()
        days[day] = days.get(day, 0.0) + total - before
    return {day: round(value, DECIMALS) for day, value in days.items()}


def day_start(day: date) -> datetime:
    """Return the local midnight starting ``day``, in UTC."""
    return dt_util.as_utc(
        datetime.combine(day, time.min, tzinfo=dt_util.get_default_time_zone())
    )


def build_statistics(
    readings: list[Reading], since: datetime, base_sum: float
) -> list[StatisticData]:
    """Return one row per day from ``since`` on, at local midnight.

    ``base_sum`` is the running total right before ``since``. The row of the
    last day is rewritten on later syncs while more readings of that day come
    in (the recorder overwrites a row with the same start).
    """

    rows: list[StatisticData] = []
    running = base_sum
    for day, consumption in sorted(daily_consumption(readings).items()):
        start = day_start(day)
        if start < since:
            continue
        running += consumption
        rows.append(
            StatisticData(start=start, state=consumption, sum=round(running, DECIMALS))
        )
    return rows


def add_statistics(
    hass: HomeAssistant,
    metadata: StatisticMetaData,
    statistics: list[StatisticData],
    scode: str,
) -> None:
    """Add statistics to the recorder, a month of rows at a time."""

    if not statistics:
        return
    _LOGGER.debug(
        "%s: adding %d values for statistic '%s'",
        scode,
        len(statistics),
        metadata["statistic_id"],
    )
    chunk = 24 * 31
    for i in range(0, len(statistics), chunk):
        try:
            async_add_external_statistics(hass, metadata, statistics[i : i + chunk])
        except (ValueError, KeyError) as err:
            _LOGGER.error(
                "%s: failed to add statistics for '%s': %s",
                scode,
                metadata["statistic_id"],
                err,
            )
            return
