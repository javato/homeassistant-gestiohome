"""Tests for setting up the integration and syncing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.gestiohome.api import Dwelling
from custom_components.gestiohome.const import DOMAIN
from custom_components.gestiohome.statistics import get_last_stat

from .conftest import build_readings

DATA = {
    "url": "https://test.gestiohome.com",
    "username": "user",
    "password": "secret",
    "code": "12345678TEST9",
    "scode": "est9",
    "meters": {"calor": ["2", "Calor"]},
}


async def test_setup_imports_history(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Setting up imports the readings as statistics and fills the sensors."""
    readings = build_readings(20)
    client = AsyncMock()
    client.base_url = "https://test.gestiohome.com/GestioHome/"
    client.async_login.return_value = Dwelling("12345678TEST9", "Edificio Test")
    client.async_get_readings.return_value = readings

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="12345678TEST9", title="Edificio Test", data=DATA
    )
    entry.add_to_hass(hass)
    with patch("custom_components.gestiohome.GestioHomeClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)

    assert entry.state is ConfigEntryState.LOADED
    last_start, last_sum = await get_last_stat(hass, "gestiohome:est9_calor")
    # 19 days of 10 units, minus the part of the last hour not yet covered
    assert 189 < last_sum <= 190
    assert last_start < readings[-1].datetime

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "est9_calor_reading")
    assert entity_id == "sensor.gestiohome_est9_heating_meter_reading"
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == readings[-1].value
    assert state.attributes["statistic_id"] == "gestiohome:est9_calor"
    # no state class: Home Assistant must not build statistics from it
    assert "state_class" not in state.attributes
    # only the supplies the dwelling has get sensors
    assert registry.async_get_entity_id("sensor", DOMAIN, "est9_acs_reading") is None

    assert await hass.config_entries.async_unload(entry.entry_id)
