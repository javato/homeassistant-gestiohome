"""Data update coordinator: syncs GestioHome and writes the statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotConnect, GestioHomeClient, InvalidAuth, Meter, Reading
from .const import CONF_KNOWN, CONF_METERS, CONF_NAME, CONF_SCODE, CONF_STAT, DOMAIN
from .statistics import (
    EPOCH,
    add_statistics,
    build_statistics,
    clear_statistics,
    create_metadata,
    daily_consumption,
    day_start,
    get_last_stat,
    get_sum_before,
    make_stat_id,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=60)
# GestioHome publishes about one reading a day (usually 24 h apart, rarely
# less): after a new one, there is no point in asking again for a while. A
# reading that comes sooner is only seen later; it still lands in its own hour
MIN_READING_AGE = timedelta(hours=20)
# the tabs of the web are looked at again this often, to report meters that
# went away or appeared
DISCOVERY_INTERVAL = timedelta(days=1)
# the oldest date asked for on a full import (the web answers what it has)
HISTORY_START = date(2015, 1, 1)
# readings can reach GestioHome late, also for past days: every sync rewrites
# the statistics of this many days before the last recorded one
REWRITE_DAYS = 31
# extra days asked for before the rewritten ones, so the reading right before
# them is always there (readings have gaps)
LOOKBACK = timedelta(days=31)


@dataclass
class MeterData:
    """What the sensors show for one meter."""

    meter_id: str
    name: str
    stat_id: str
    unit: str | None = None
    readings: list[Reading] = field(default_factory=list)
    last_reading: Reading | None = None
    last_day: float | None = None
    last_day_date: date | None = None
    month: float | None = None
    last_month: float | None = None


type GestioHomeConfigEntry = ConfigEntry[GestioHomeCoordinator]


def summarize(readings: list[Reading], now: datetime) -> dict:
    """Compute the last day with readings, this month and last month.

    Same daily figures as the GestioHome web.
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


class GestioHomeCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
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
        self._last_discovery: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.id}",
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )

    @property
    def meters(self) -> dict[str, dict[str, str]]:
        """Return the meters the user chose: {meter id: {name, stat}}."""
        return self.config_entry.options.get(CONF_METERS, {})

    def stat_id(self, meter_id: str) -> str:
        """Return the statistic ID of a chosen meter (fixed when it was added)."""
        return make_stat_id(self.id, self.meters[meter_id][CONF_STAT])

    async def _async_update_data(self) -> dict[str, MeterData]:
        try:
            now = dt_util.utcnow()
            if (
                self._last_discovery is None
                or now - self._last_discovery >= DISCOVERY_INTERVAL
            ):
                await self._check_meters()
                self._last_discovery = now
            data: dict[str, MeterData] = {}
            for meter_id in self.meters:
                data[meter_id] = await self._sync_meter(meter_id)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CannotConnect as err:
            raise UpdateFailed(f"GestioHome: {err}") from err
        return data

    async def _check_meters(self) -> None:
        """Report chosen meters the web no longer lists, and new ones.

        Nothing changes on its own: the user decides in the integration's
        options. A meter is the same one only if its numeric ID is the same.
        """

        _, found = await self.client.async_discover()
        found_ids = {meter.meter_id: meter for meter in found}
        entry_id = self.config_entry.entry_id
        for meter_id, meter in self.meters.items():
            issue_id = f"meter_missing_{entry_id}_{meter_id}"
            if meter_id in found_ids:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue
            _LOGGER.warning(
                "%s: GestioHome no longer lists meter %s (%s)",
                self.id,
                meter_id,
                meter[CONF_NAME],
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="meter_missing",
                translation_placeholders={
                    "name": meter[CONF_NAME],
                    "meter_id": meter_id,
                    "stat_id": self.stat_id(meter_id),
                },
            )
        known = set(self.config_entry.options.get(CONF_KNOWN, [])) | set(self.meters)
        new: list[Meter] = [m for m in found if m.meter_id not in known]
        issue_id = f"new_meters_{entry_id}"
        if not new:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        _LOGGER.info("%s: new meters %s", self.id, new)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="new_meters",
            translation_placeholders={
                "dwelling": self.dwelling,
                "meters": ", ".join(f"{m.tab} {m.meter_id}" for m in new),
            },
        )

    async def _sync_meter(self, meter_id: str) -> MeterData:
        """Fetch new readings of a meter and extend its statistic."""

        name = self.meters[meter_id][CONF_NAME]
        stat_id = self.stat_id(meter_id)
        previous = (self.data or {}).get(meter_id)
        now = dt_util.utcnow()
        last_start, _, _ = await get_last_stat(self.hass, stat_id)

        if (
            previous is not None
            and previous.last_reading is not None
            and now - previous.last_reading.datetime < MIN_READING_AGE
            and last_start > EPOCH
        ):
            previous.name = name
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
        answer = await self.client.async_get_readings(meter_id, start, today, tz)
        readings = answer.readings
        if since > EPOCH and readings and readings[0].datetime >= since:
            # a gap in the readings longer than the look-back: ask for it all
            answer = await self.client.async_get_readings(
                meter_id, HISTORY_START, today, tz
            )
            readings = answer.readings

        # the last days are written again, each reading in its own hour:
        # readings that reached GestioHome late land where they belong (the
        # recorder overwrites rows with the same start)
        base_sum = (
            await get_sum_before(self.hass, stat_id, since) if since > EPOCH else 0.0
        )
        rows = build_statistics(readings, since, base_sum)
        if rows:
            _LOGGER.debug(
                "%s: %s, %d hours up to %s",
                self.id,
                meter_id,
                len(rows),
                rows[-1]["start"],
            )
            add_statistics(
                self.hass, create_metadata(stat_id, name, answer.unit), rows, self.id
            )

        result = MeterData(
            meter_id=meter_id,
            name=name,
            stat_id=stat_id,
            unit=answer.unit,
            readings=readings,
        )
        if readings:
            result.last_reading = readings[-1]
            summary = summarize(readings, now)
            result.last_day = summary.get("last_day")
            result.last_day_date = summary.get("last_day_date")
            result.month = summary.get("month")
            result.last_month = summary.get("last_month")
        return result

    async def async_soft_reset(self) -> None:
        """Rebuild the statistic of each chosen meter from its whole history.

        A statistic is only cleared once GestioHome has given the readings to
        build it again: a meter the web no longer serves keeps its statistic.
        """

        tz = dt_util.get_default_time_zone()
        today = dt_util.as_local(dt_util.utcnow()).date()
        try:
            for meter_id, meter in self.meters.items():
                answer = await self.client.async_get_readings(
                    meter_id, HISTORY_START, today, tz
                )
                stat_id = self.stat_id(meter_id)
                if not answer.readings:
                    _LOGGER.warning(
                        "%s: no readings for meter %s, keeping %s as it is",
                        self.id,
                        meter_id,
                        stat_id,
                    )
                    continue
                await clear_statistics(self.hass, [stat_id], self.id)
                add_statistics(
                    self.hass,
                    create_metadata(stat_id, meter[CONF_NAME], answer.unit),
                    build_statistics(answer.readings, EPOCH, 0.0),
                    self.id,
                )
        except (CannotConnect, InvalidAuth) as err:
            raise HomeAssistantError(f"GestioHome: {err}") from err
        self.data = {}
        await self.async_refresh()

    async def async_force_sync(self) -> None:
        """Ask GestioHome again right away, also for the meters it lists."""

        self._last_discovery = None
        self.data = {}
        await self.async_refresh()
