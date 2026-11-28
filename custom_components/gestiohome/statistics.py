"""Long-term statistics with the real date of each consumption.

Same strategy as edata: external statistics (``gestiohome:<scode>_<supply>``)
written with ``async_add_external_statistics``, with ``state`` the
consumption of the period and ``sum`` the running total. Each sync continues
from the last recorded row and its sum, so totals never reset.

GestioHome serves absolute meter readings (about one a day, at varying times),
not hourly consumption like Datadis. Each reading is written in the hour it was
taken (statistics rows must start on the hour), with what the meter counted
since the previous reading, as Home Assistant does with a meter that reports
once a day. Days, and so months, add up exactly as on the web.
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
    statistics_during_period,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import Reading
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=dt_util.UTC)
# decimals kept in values (hot water meters read litres: 0.001 m³)
DECIMALS = 6
# a reading below this share of the previous one is a meter reset (as in
# the recorder for total_increasing sensors); a smaller drop is a correction
RESET_RATIO = 0.9


# units GestioHome gives (seriesUnit), lower case without spaces, mapped to
# Home Assistant's unit and device class (which is also the unit class)
_UNITS: dict[str, tuple[str, SensorDeviceClass]] = {
    "wh": (UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY),
    "kwh": (UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
    "mwh": (UnitOfEnergy.MEGA_WATT_HOUR, SensorDeviceClass.ENERGY),
    "gj": (UnitOfEnergy.GIGA_JOULE, SensorDeviceClass.ENERGY),
    "m3": (UnitOfVolume.CUBIC_METERS, SensorDeviceClass.VOLUME),
    "m³": (UnitOfVolume.CUBIC_METERS, SensorDeviceClass.VOLUME),
    "l": (UnitOfVolume.LITERS, SensorDeviceClass.VOLUME),
    "litros": (UnitOfVolume.LITERS, SensorDeviceClass.VOLUME),
}


def unit_info(unit: str | None) -> tuple[str | None, SensorDeviceClass | None]:
    """Return Home Assistant's unit and device class for a GestioHome unit.

    A unit Home Assistant does not know is kept as it comes, without a class.
    """

    if unit is None:
        return None, None
    return _UNITS.get(unit.replace(" ", "").lower(), (unit, None))


def make_stat_id(integration_id: str, suffix: str) -> str:
    """Create a statistic ID."""
    return f"{DOMAIN}:{integration_id}_{suffix}"


def create_metadata(stat_id: str, name: str, unit: str | None) -> StatisticMetaData:
    """Create the metadata of a meter statistic."""

    ha_unit, device_class = unit_info(unit)
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"GestioHome {name}",
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=device_class.value if device_class else None,
        unit_of_measurement=ha_unit,
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

    Like Home Assistant does with ``total_increasing`` sensors, a reading that
    drops by more than 10 % is a replaced or reset meter: it adds nothing and
    becomes the new baseline. A smaller drop is a correction of the meter and
    is subtracted, as the GestioHome web does.
    """

    points: list[tuple[float, float]] = []
    total = 0.0
    previous: float | None = None
    for reading in readings:
        if previous is not None:
            if reading.value >= RESET_RATIO * previous:
                total += reading.value - previous
            else:
                _LOGGER.warning(
                    "Meter reading dropped at %s (%s -> %s), taking it as a reset",
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


def hourly_consumption(readings: list[Reading]) -> dict[datetime, float]:
    """Return the consumption of each hour that has readings (start in UTC).

    What the meter counted since the previous reading goes to the hour of the
    new reading, as Home Assistant does with a meter that reports once a day.
    """

    points = continuous_counter(readings)
    hours: dict[datetime, float] = {}
    for reading, (_, total), (_, before) in zip(
        readings[1:], points[1:], points, strict=False
    ):
        local = dt_util.as_local(reading.datetime)
        hour = dt_util.as_utc(local.replace(minute=0, second=0, microsecond=0))
        hours[hour] = hours.get(hour, 0.0) + total - before
    return {hour: round(value, DECIMALS) for hour, value in hours.items()}


def day_start(day: date) -> datetime:
    """Return the local midnight starting ``day``, in UTC."""
    return dt_util.as_utc(
        datetime.combine(day, time.min, tzinfo=dt_util.get_default_time_zone())
    )


def build_statistics(
    readings: list[Reading], since: datetime, base_sum: float
) -> list[StatisticData]:
    """Return one row per hour with readings, from ``since`` on.

    ``base_sum`` is the running total right before ``since``. Rows that are
    written again replace the old ones (the recorder overwrites a row with the
    same start), so readings that arrive late land in their own hour.
    """

    rows: list[StatisticData] = []
    running = base_sum
    for start, consumption in sorted(hourly_consumption(readings).items()):
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
    chunk = 24 * 31  # a month of hourly rows
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
