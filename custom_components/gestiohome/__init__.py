"""Home Assistant GestioHome integration."""

from __future__ import annotations

import logging

from aiohttp import CookieJar
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import GestioHomeClient
from .const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from .coordinator import GestioHomeConfigEntry, GestioHomeCoordinator

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: GestioHomeConfigEntry) -> bool:
    """Set up GestioHome from a config entry."""

    # its own cookie jar: the web keeps the login in a PHP session cookie
    session = async_create_clientsession(hass, cookie_jar=CookieJar())
    client = GestioHomeClient(
        session,
        entry.data[CONF_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = GestioHomeCoordinator(hass, entry, client)
    entry.runtime_data = coordinator

    # like edata, the first sync (a full history import the first time) waits
    # until Home Assistant has started, so it does not slow the startup down
    async def _first_refresh(_event: Event | None = None) -> None:
        await coordinator.async_refresh()

    if hass.state is CoreState.running:
        entry.async_create_background_task(
            hass, _first_refresh(), f"{entry.entry_id}_first_refresh"
        )
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _first_refresh)
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GestioHomeConfigEntry) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
