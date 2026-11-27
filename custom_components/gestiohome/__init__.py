"""Home Assistant GestioHome integration."""

from __future__ import annotations

import logging

from aiohttp import CookieJar
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import slugify

from .api import GestioHomeClient
from .const import (
    CONF_KNOWN,
    CONF_METERS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCODE,
    CONF_STAT,
    CONF_URL,
    CONF_USERNAME,
)
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


# version 1 had one meter per supply (hot water, heating, cooling), found on
# fixed pages; the name each one gets now is the tab it was on
_V1_SUPPLIES = {"dhw": "ACS", "heating": "Calor", "cooling": "Frío"}


async def async_migrate_entry(
    hass: HomeAssistant, entry: GestioHomeConfigEntry
) -> bool:
    """Move version 1 entries to meters chosen by their numeric ID."""

    if entry.version > 2:
        return False
    if entry.version == 1:
        old: dict[str, list[str]] = entry.data.get(CONF_METERS, {})
        meters = {
            meter_id: {
                CONF_NAME: (name := _V1_SUPPLIES.get(key, key)),
                CONF_STAT: f"{slugify(name)}_{meter_id}",
            }
            for key, (meter_id, _) in old.items()
        }
        scode = entry.data[CONF_SCODE]
        renames = {
            f"{scode}_{key}_{kind}": f"{scode}_{meter_id}_{kind}"
            for key, (meter_id, _) in old.items()
            for kind in ("reading", "last_day", "month", "last_month")
        }

        # the sensors keep their entity IDs and history
        def _migrate(entity: er.RegistryEntry) -> dict | None:
            if (new := renames.get(entity.unique_id)) is not None:
                return {"new_unique_id": new}
            return None

        await er.async_migrate_entries(hass, entry.entry_id, _migrate)
        data = {k: v for k, v in entry.data.items() if k != CONF_METERS}
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options={CONF_METERS: meters, CONF_KNOWN: list(meters)},
            version=2,
        )
        _LOGGER.info("%s: migrated to version 2, meters %s", scode, meters)
    return True
