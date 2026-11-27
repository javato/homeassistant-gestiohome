"""Data update coordinator: syncs GestioHome and writes the statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotConnect, GestioHomeClient, InvalidAuth, Meter, Reading
from .const import CONF_METERS, CONF_SCODE, DOMAIN, SUPPLIES, Supply
from .statistics import (
    EPOCH,
    add_statistics,
    build_statistics,
    clear_statistics,
    continuous_counter,
    create_metadata,
    get_integration_stat_ids,
    get_last_stat,
    make_stat_id,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=60)
# GestioHome publishes about one reading a day: after a new one, there is no
# point in asking again for a while
MIN_READING_AGE = timedelta(hours=6)
# the oldest date asked for on a full import (the web answers what it has)
HISTORY_START = date(2015, 1, 1)
# extra days asked for before the last recorded hour, so the reading right
# before it is always there to interpolate from (readings have gaps)
LOOKBACK = timedelta(days=31)


@dataclass
class SupplyData:
    """What the sensors show for one supply."""

    meter: Meter
    readings: list[Reading] = field(default_factory=list)
    last_reading: Reading | None = None
    last_day: float | None = None
    last_day_date: date | None = None
    month: float | None = None
    last_month: float | None = None
    stat_id: str | None = None


type GestioHomeConfigEntry = ConfigEntry[GestioHomeCoordinator]


def _counter_at(points: list[tuple[float, float]], moment: datetime) -> float | None:
    """Return the interpolated total at ``moment`` (None outside the readings)."""

    ts = moment.timestamp()
    if not points or ts < points[0][0] or ts > points[-1][0]:
        return None
    for (t0, c0), (t1, c1) in zip(points, points[1:], strict=False):
        if t0 <= ts <= t1:
            return c0 if t1 == t0 else c0 + (c1 - c0) * (ts - t0) / (t1 - t0)
    return points[-1][1]


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=dt_util.get_default_time_zone())


def summarize(readings: list[Reading], now: datetime) -> dict:
    """Compute the last full day, this month so far and last month."""

    points = continuous_counter(readings)
    if len(points) < 2:
        return {}
    last = readings[-1]
    last_local = dt_util.as_local(last.datetime)
    today = dt_util.as_local(now).date()

    def between(start: datetime, end: datetime) -> float | None:
        a, b = _counter_at(points, start), _counter_at(points, end)
        return None if a is None or b is None else round(b - a, 3)

    last_day_date = last_local.date() - timedelta(days=1)
    month_start = today.replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    month_end = min(last.datetime, now)
    return {
        "last_day_date": last_day_date,
        "last_day": between(
            _local_midnight(last_day_date), _local_midnight(last_local.date())
        ),
        "month": between(_local_midnight(month_start), month_end)
        if month_end >= _local_midnight(month_start)
        else None,
        "last_month": between(
            _local_midnight(prev_month_start), _local_midnight(month_start)
        ),
    }


class GestioHomeCoordinator(DataUpdateCoordinator[dict[str, SupplyData]]):
    """Sync GestioHome readings into statistics and sensor data."""

    config_entry: GestioHomeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GestioHomeConfigEntry,
        client: GestioHomeClient,
    ) -> None:
        """Initialize the coordinator."""

        self.client = client
        self.id: str = entry.data[CONF_SCODE]
        self.dwelling: str = entry.title
        self._meters: dict[str, Meter] | None = {
            key: Meter(*value) for key, value in entry.data.get(CONF_METERS, {}).items()
        } or None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.id}",
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, SupplyData]:
        try:
            if self._meters is None:
                await self._discover()
            data: dict[str, SupplyData] = {}
            for supply in SUPPLIES:
                if (meter := (self._meters or {}).get(supply.key)) is None:
                    continue
                data[supply.key] = await self._sync_supply(supply, meter)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CannotConnect as err:
            raise UpdateFailed(f"GestioHome: {err}") from err
        return data

    async def _discover(self) -> None:
        """Find the dwelling and its meter for each supply."""

        await self.client.async_login()
        meters: dict[str, Meter] = {}
        for supply in SUPPLIES:
            found = await self.client.async_get_meters(supply.page)
            if not found:
                _LOGGER.debug("%s: no %s meter", self.id, supply.key)
                continue
            if len(found) > 1:
                _LOGGER.warning(
                    "%s: several %s meters %s, using the first one",
                    self.id,
                    supply.key,
                    found,
                )
            meters[supply.key] = found[0]
        if not meters:
            raise CannotConnect("no meter found on any supply page")
        _LOGGER.info("%s: meters found %s", self.id, meters)
        self._meters = meters
        stored = {k: [m.meter_id, m.name] for k, m in meters.items()}
        if stored != self.config_entry.data.get(CONF_METERS):
            # a meter appeared, went away or was replaced: reload with it
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_METERS: stored}
            )
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    async def _sync_supply(self, supply: Supply, meter: Meter) -> SupplyData:
        """Fetch new readings of a supply and extend its statistic."""

        previous = (self.data or {}).get(supply.key)
        now = dt_util.utcnow()
        stat_id = make_stat_id(self.id, supply.key)
        last_start, last_sum = await get_last_stat(self.hass, stat_id)

        if (
            previous is not None
            and previous.last_reading is not None
            and now - previous.last_reading.datetime < MIN_READING_AGE
            and last_start > EPOCH
        ):
            return previous

        tz = dt_util.get_default_time_zone()
        today = dt_util.as_local(now).date()
        # also the previous month, for the monthly sensors
        sensors_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        if last_start > EPOCH:
            start = min(dt_util.as_local(last_start - LOOKBACK).date(), sensors_start)
        else:
            start = HISTORY_START
        readings = await self.client.async_get_readings(
            meter.meter_id, start, today, tz
        )
        if last_start > EPOCH and readings and readings[0].datetime > last_start:
            # a gap in the readings longer than the look-back: ask for it all
            readings = await self.client.async_get_readings(
                meter.meter_id, HISTORY_START, today, tz
            )

        rows = build_statistics(readings, last_start, last_sum)
        if rows:
            _LOGGER.debug(
                "%s: %s, %d new hours up to %s",
                self.id,
                supply.key,
                len(rows),
                rows[-1]["start"],
            )
            add_statistics(self.hass, create_metadata(stat_id, supply), rows, self.id)

        result = SupplyData(meter=meter, readings=readings, stat_id=stat_id)
        if readings:
            result.last_reading = readings[-1]
            summary = summarize(readings, now)
            result.last_day = summary.get("last_day")
            result.last_day_date = summary.get("last_day_date")
            result.month = summary.get("month")
            result.last_month = summary.get("last_month")
        return result

    async def async_soft_reset(self) -> None:
        """Clear this entry's statistics and rebuild them from GestioHome."""

        stat_ids = await get_integration_stat_ids(self.hass, self.id)
        await clear_statistics(self.hass, stat_ids, self.id)
        self.data = {}
        await self.async_refresh()

    async def async_force_sync(self) -> None:
        """Ask GestioHome again right away, also for the meters."""

        self._meters = None
        self.data = {}
        await self.async_refresh()
