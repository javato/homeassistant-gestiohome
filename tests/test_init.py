"""Tests for setting up the integration and syncing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.recorder.util import get_instance
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.gestiohome.api import Dwelling, Meter, Readings
from custom_components.gestiohome.const import DOMAIN
from custom_components.gestiohome.statistics import get_last_stat

from .conftest import build_readings

DATA = {
    "url": "https://test.gestiohome.com",
    "username": "user",
    "password": "secret",
    "code": "12345678TEST9",
    "scode": "est9",
}
OPTIONS = {"meters": {"2": {"name": "Calor", "stat": "calor_2"}}, "known": ["2"]}
STAT_ID = "gestiohome:est9_calor_2_consumption"


def _client(readings, meters=None) -> AsyncMock:
    client = AsyncMock()
    client.base_url = "https://test.gestiohome.com/GestioHome/"
    client.async_discover.return_value = (
        Dwelling("12345678TEST9", "Test Building"),
        meters if meters is not None else [Meter("2", "Consumo Calef", "Calor")],
    )
    client.async_get_readings.return_value = Readings(readings, "kWh")
    return client


def _entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="12345678TEST9",
        title="Test Building",
        data=kwargs.get("data", DATA),
        options=kwargs.get("options", OPTIONS),
    )


async def _setup(hass: HomeAssistant, entry, client) -> None:
    entry.add_to_hass(hass)
    with patch("custom_components.gestiohome.GestioHomeClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)


async def test_setup_imports_history(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Setting up imports the readings as statistics and fills the sensors."""
    readings = build_readings(20)
    entry = _entry()
    await _setup(hass, entry, _client(readings))

    assert entry.state is ConfigEntryState.LOADED
    last_start, last_sum, last_state = await get_last_stat(hass, STAT_ID)
    # 19 readings of 10 units, each in its own hour
    assert last_sum == 190
    assert last_state == 10
    assert last_start == readings[-1].datetime.replace(minute=0)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "est9_2_reading")
    assert entity_id == "sensor.gestiohome_est9_meter_reading_calor"
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == readings[-1].value
    # whole kWh, shown as GestioHome gives them: no ".0"
    assert state.state == str(int(readings[-1].value))
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["statistic_id"] == STAT_ID
    # no state class: Home Assistant must not build statistics from it
    assert "state_class" not in state.attributes
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_late_reading_goes_to_its_hour(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A reading that reaches GestioHome late is written in its own hour."""
    readings = build_readings(20)
    late = readings[10]
    client = _client([r for r in readings if r is not late])
    entry = _entry()
    await _setup(hass, entry, client)

    late_hour = dt_util.as_utc(late.datetime.replace(minute=0))
    next_hour = dt_util.as_utc(readings[11].datetime.replace(minute=0))
    before = await _states(hass, STAT_ID)
    assert late_hour not in before
    assert before[next_hour] == 20

    # the missing reading shows up later
    client.async_get_readings.return_value = Readings(readings, "kWh")
    with patch("custom_components.gestiohome.GestioHomeClient", return_value=client):
        await entry.runtime_data.async_force_sync()
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

    after = await _states(hass, STAT_ID)
    assert after[late_hour] == 10
    assert after[next_hour] == 10
    _, last_sum, _ = await get_last_stat(hass, STAT_ID)
    assert last_sum == 190
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_and_new_meters_are_reported(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A chosen meter the web no longer lists, and a new one, raise issues."""
    entry = _entry()
    await _setup(
        hass, entry, _client(build_readings(5), [Meter("9", "Nuevo", "Calor")])
    )
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"meter_missing_{entry.entry_id}_2")
    assert issues.async_get_issue(DOMAIN, f"new_meters_{entry.entry_id}")
    # nothing changes on its own
    assert list(entry.options["meters"]) == ["2"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_repair_keeps_what_cannot_be_rebuilt(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Repairing only clears a statistic once GestioHome serves its readings."""
    readings = build_readings(10)
    client = _client(readings)
    entry = _entry()
    await _setup(hass, entry, client)
    _, before, _ = await get_last_stat(hass, STAT_ID)
    assert before == 90

    client.async_get_readings.return_value = Readings([], None)
    with patch("custom_components.gestiohome.GestioHomeClient", return_value=client):
        await entry.runtime_data.async_soft_reset()
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
    _, after, _ = await get_last_stat(hass, STAT_ID)
    assert after == 90
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_version_1_must_be_added_again(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Entries of the fixed-supply version are not migrated."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="12345678TEST9",
        title="Test Building",
        data={**DATA, "meters": {"heating": ["2", "Consumo Calef"]}},
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def _states(hass: HomeAssistant, stat_id: str) -> dict:
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {stat_id},
        "hour",
        None,
        {"state"},
    )
    return {
        dt_util.utc_from_timestamp(row["start"]): row["state"]
        for row in stats.get(stat_id, [])
    }
