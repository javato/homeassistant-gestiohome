"""Long-term statistics with the real date of each consumption.

Same strategy as edata: external statistics (``gestiohome:<scode>_<supply>``)
written with ``async_add_external_statistics``, one row per hour whose
``state`` is that hour's consumption and ``sum`` the running total. Each sync
continues from the last recorded hour and its sum, so totals never reset.

GestioHome serves absolute meter readings (about one a day, at varying times),
not hourly consumption: the consumption between two readings is spread evenly
over the hours between them. An hour is only written once a later reading
covers it, so rows never have to be corrected afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
import logging
import math

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
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import Reading
from .const import DOMAIN, Supply

_LOGGER = logging.getLogger(__name__)

HOUR = timedelta(hours=1)
EPOCH = datetime(1970, 1, 1, tzinfo=dt_util.UTC)
# decimals kept in hourly values (ACS meters read litres: 0.001 m³)
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


async def get_last_stat(hass: HomeAssistant, stat_id: str) -> tuple[datetime, float]:
    """Return the (start, cumulative sum) of the last recorded hour."""

    recorder = recorder_util.get_instance(hass)
    last_stats = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    if not (rows := last_stats.get(stat_id)):
        return EPOCH, 0.0
    row = rows[0]
    start = row.get("start")
    return (
        dt_util.utc_from_timestamp(start) if start is not None else EPOCH,
        row.get("sum") or 0.0,
    )


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


def _interpolate(
    points: list[tuple[float, float]], ts: float, idx: int
) -> tuple[float, int]:
    """Return the counter at ``ts`` and the segment index to resume from.

    ``ts`` must lie within the points and grow between calls.
    """

    while idx + 1 < len(points) - 1 and points[idx + 1][0] <= ts:
        idx += 1
    (t0, c0), (t1, c1) = points[idx], points[idx + 1]
    if ts >= t1:
        return c1, idx
    return c0 + (c1 - c0) * (ts - t0) / (t1 - t0), idx


def hourly_consumption(
    readings: list[Reading], after: datetime
) -> Iterator[tuple[datetime, float]]:
    """Yield (hour start, consumption) for each full hour the readings cover.

    Starts with the first hour that begins at or after ``after`` (and at or after
    the first reading) and ends with the last hour that ends by the last reading.
    """

    points = continuous_counter(readings)
    if len(points) < 2:
        return
    first_ts, last_ts = points[0][0], points[-1][0]
    start_ts = max(first_ts, after.timestamp())
    hour = math.ceil(start_ts / 3600) * 3600
    idx = 0
    value, idx = _interpolate(points, hour, idx)
    while hour + 3600 <= last_ts:
        next_value, idx = _interpolate(points, hour + 3600, idx)
        yield dt_util.utc_from_timestamp(hour), round(next_value - value, DECIMALS)
        hour += 3600
        value = next_value


def build_statistics(
    readings: list[Reading], last_start: datetime, last_sum: float
) -> list[StatisticData]:
    """Return the hourly rows after ``last_start``, continuing ``last_sum``."""

    rows: list[StatisticData] = []
    running = last_sum
    after = last_start + HOUR if last_start > EPOCH else EPOCH
    for start, consumption in hourly_consumption(readings, after):
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
