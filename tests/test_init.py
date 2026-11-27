"""Tests for setting up the integration and syncing."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.recorder.util import get_instance
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.gestiohome.api import Dwelling
from custom_components.gestiohome.const import DOMAIN
from custom_components.gestiohome.statistics import day_start, get_last_stat

from .conftest import build_readings

DATA = {
    "url": "https://test.gestiohome.com",
    "username": "user",
    "password": "secret",
    "code": "12345678TEST9",
    "scode": "est9",
    "meters": {"heating": ["2", "Heating"]},
}


async def test_setup_imports_history(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Setting up imports the readings as statistics and fills the sensors."""
    readings = build_readings(20)
    client = AsyncMock()
    client.base_url = "https://test.gestiohome.com/GestioHome/"
    client.async_login.return_value = Dwelling("12345678TEST9", "Test Building")
    client.async_get_readings.return_value = readings

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="12345678TEST9", title="Test Building", data=DATA
    )
    entry.add_to_hass(hass)
    with patch("custom_components.gestiohome.GestioHomeClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

    assert entry.state is ConfigEntryState.LOADED
    last_start, last_sum, last_state = await get_last_stat(
        hass, "gestiohome:est9_heating"
    )
    # 19 days of 10 units, one row a day
    assert last_sum == 190
    assert last_state == 10
    assert last_start < readings[-1].datetime

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "est9_heating_reading")
    assert entity_id == "sensor.gestiohome_est9_heating_meter_reading"
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == readings[-1].value
    assert state.attributes["statistic_id"] == "gestiohome:est9_heating"
    # no state class: Home Assistant must not build statistics from it
    assert "state_class" not in state.attributes
    # only the supplies the dwelling has get sensors
    assert registry.async_get_entity_id("sensor", DOMAIN, "est9_dhw_reading") is None

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_late_reading_goes_to_its_day(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A reading that reaches GestioHome late is written on its own day."""
    readings = build_readings(20)
    late = readings[10]
    client = AsyncMock()
    client.base_url = "https://test.gestiohome.com/GestioHome/"
    client.async_get_readings.return_value = [r for r in readings if r is not late]

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="12345678TEST9", title="Test Building", data=DATA
    )
    entry.add_to_hass(hass)
    with patch("custom_components.gestiohome.GestioHomeClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

        stat_id = "gestiohome:est9_heating"
        late_day = day_start(dt_util.as_local(late.datetime).date())
        before = await _daily_states(hass, stat_id)
        assert late_day not in before
        assert before[late_day + timedelta(days=1)] == 20

        # the missing reading shows up later
        client.async_get_readings.return_value = readings
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

    after = await _daily_states(hass, stat_id)
    assert after[late_day] == 10
    assert after[late_day + timedelta(days=1)] == 10
    _, last_sum, _ = await get_last_stat(hass, stat_id)
    assert last_sum == 190
    assert await hass.config_entries.async_unload(entry.entry_id)


async def _daily_states(hass: HomeAssistant, stat_id: str) -> dict:
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
