"""Data update coordinator: syncs GestioHome and writes the statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    create_metadata,
    daily_consumption,
    day_start,
    get_integration_stat_ids,
    get_last_stat,
    get_sum_before,
    make_stat_id,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=60)
# GestioHome publishes about one reading a day: after a new one, there is no
# point in asking again for a while
MIN_READING_AGE = timedelta(hours=6)
# the oldest date asked for on a full import (the web answers what it has)
HISTORY_START = date(2015, 1, 1)
# readings can reach GestioHome late, also for past days: every sync rewrites
# the statistics of this many days before the last recorded one
REWRITE_DAYS = 31
# extra days asked for before the rewritten ones, so the reading right before
# them is always there (readings have gaps)
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


def summarize(readings: list[Reading], now: datetime) -> dict:
    """Compute the last day with readings, this month and last month.

    Same daily figures as the statistics (and the GestioHome web).
    """

    days = daily_consumption(readings)
    if not days:
        return {}
    month_start = dt_util.as_local(now).date().replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    last_day_date = max(days)

    def total(start: date, end: date) -> float:
        return round(sum(v for d, v in days.items() if start <= d < end), 3)

    return {
        "last_day_date": last_day_date,
        "last_day": round(days[last_day_date], 3),
        "month": total(month_start, month_start + timedelta(days=32)),
        "last_month": total(prev_month_start, month_start),
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
        last_start, _, _ = await get_last_stat(self.hass, stat_id)

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
            since = day_start(
                dt_util.as_local(last_start).date() - timedelta(days=REWRITE_DAYS)
            )
            start = min(dt_util.as_local(since - LOOKBACK).date(), sensors_start)
        else:
            since = EPOCH
            start = HISTORY_START
        readings = await self.client.async_get_readings(
            meter.meter_id, start, today, tz
        )
        if since > EPOCH and readings and readings[0].datetime >= since:
            # a gap in the readings longer than the look-back: ask for it all
            readings = await self.client.async_get_readings(
                meter.meter_id, HISTORY_START, today, tz
            )

        # the last days are written again, with the real day of each reading:
        # readings that reached GestioHome late land where they belong (the
        # recorder overwrites rows with the same start)
        base_sum = (
            await get_sum_before(self.hass, stat_id, since) if since > EPOCH else 0.0
        )
        rows = build_statistics(readings, since, base_sum)
        if rows:
            _LOGGER.debug(
                "%s: %s, %d days up to %s",
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
