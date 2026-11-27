"""Configuration flow (GUI)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import CookieJar
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector as sel
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import slugify
import voluptuous as vol

from .api import (
    CannotConnect,
    Dwelling,
    GestioHomeClient,
    InvalidAuth,
    Meter,
    normalize_url,
)
from .const import (
    CONF_CODE,
    CONF_KNOWN,
    CONF_METERS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCODE,
    CONF_STAT,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import GestioHomeConfigEntry

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): sel.TextSelector(
            sel.TextSelectorConfig(type=sel.TextSelectorType.URL)
        ),
        vol.Required(CONF_USERNAME): sel.TextSelector(),
        vol.Required(CONF_PASSWORD): sel.TextSelector(
            sel.TextSelectorConfig(type=sel.TextSelectorType.PASSWORD)
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): sel.TextSelector(
            sel.TextSelectorConfig(type=sel.TextSelectorType.PASSWORD)
        ),
    }
)


def get_scode(code: str, taken: set[str]) -> str:
    """Return the shortest code suffix (4+ chars, lower case) not in ``taken``.

    The same rule edata follows for the CUPS ("scups").
    """

    code = code.lower()
    for i in range(4, len(code)):
        if (scode := code[-i:]) not in taken:
            return scode
    return code


async def async_discover(
    hass: HomeAssistant, url: str, username: str, password: str
) -> tuple[Dwelling | None, list[Meter], str | None]:
    """Log in and list the meters; return (dwelling, meters, error)."""

    # a throwaway session: detached (not closed) when done, as HA asks
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=CookieJar()
    )
    client = GestioHomeClient(session, url, username, password)
    try:
        dwelling, meters = await client.async_discover()
    except InvalidAuth:
        return None, [], "invalid_auth"
    except CannotConnect:
        _LOGGER.debug("Cannot connect to GestioHome", exc_info=True)
        return None, [], "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error reading GestioHome")
        return None, [], "unknown"
    finally:
        session.detach()
    if not dwelling.code:
        return None, [], "unknown"
    if not meters:
        return None, [], "no_meters"
    return dwelling, meters, None


def _label(meter: Meter) -> str:
    return f"{meter.tab}: {meter.name} ({meter.meter_id})"


def select_schema(meters: list[Meter], chosen: list[str]) -> vol.Schema:
    """Ask which meters to add."""

    return vol.Schema(
        {
            vol.Required(CONF_METERS, default=chosen): sel.SelectSelector(
                sel.SelectSelectorConfig(
                    options=[
                        sel.SelectOptionDict(value=m.meter_id, label=_label(m))
                        for m in meters
                    ],
                    multiple=True,
                    mode=sel.SelectSelectorMode.LIST,
                )
            )
        }
    )


def names_schema(
    meters: list[Meter], chosen: list[str], current: Mapping[str, Mapping[str, str]]
) -> vol.Schema:
    """Ask a name for each chosen meter: the current one, else its tab's."""

    by_id = {m.meter_id: m for m in meters}
    return vol.Schema(
        {
            vol.Required(
                _label(by_id[meter_id]),
                default=current.get(meter_id, {}).get(CONF_NAME) or by_id[meter_id].tab,
            ): sel.TextSelector()
            for meter_id in chosen
        }
    )


def chosen_meters(
    meters: list[Meter],
    chosen: list[str],
    names: Mapping[str, str],
    current: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """Return the meters option. A meter already added keeps its statistic."""

    by_id = {m.meter_id: m for m in meters}
    result: dict[str, dict[str, str]] = {}
    for meter_id in chosen:
        name = names[_label(by_id[meter_id])].strip() or by_id[meter_id].tab
        stat = current.get(meter_id, {}).get(CONF_STAT) or (
            f"{slugify(name) or 'meter'}_{meter_id}"
        )
        result[meter_id] = {CONF_NAME: name, CONF_STAT: stat}
    return result


class GestioHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GestioHome."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._title = ""
        self._found: list[Meter] = []
        self._chosen: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(entry: GestioHomeConfigEntry) -> GestioHomeOptionsFlow:
        """Return the options flow (choose the meters again)."""
        return GestioHomeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the building URL and the credentials."""

        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            dwelling, meters, error = await async_discover(
                self.hass, url, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                assert dwelling is not None and dwelling.code
                await self.async_set_unique_id(dwelling.code.upper())
                self._abort_if_unique_id_configured()
                taken = {
                    entry.data.get(CONF_SCODE)
                    for entry in self._async_current_entries(include_ignore=False)
                }
                self._title = dwelling.name or dwelling.code
                self._data = {
                    CONF_URL: url,
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_CODE: dwelling.code.upper(),
                    CONF_SCODE: get_scode(dwelling.code, taken),
                }
                self._found = meters
                return await self.async_step_meters()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
            description_placeholders={
                "example_url": "https://mybuilding.gestiohome.com"
            },
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which meters to add."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_METERS]:
                self._chosen = user_input[CONF_METERS]
                return await self.async_step_names()
            errors["base"] = "no_meter_chosen"
        return self.async_show_form(
            step_id="meters",
            data_schema=select_schema(self._found, [m.meter_id for m in self._found]),
            errors=errors,
            description_placeholders={"dwelling": self._title},
        )

    async def async_step_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask a name for each meter."""

        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data=self._data,
                options={
                    CONF_METERS: chosen_meters(
                        self._found, self._chosen, user_input, {}
                    ),
                    CONF_KNOWN: [m.meter_id for m in self._found],
                },
            )
        return self.async_show_form(
            step_id="names", data_schema=names_schema(self._found, self._chosen, {})
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when GestioHome rejects the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""

        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            _, _, error = await async_discover(
                self.hass,
                entry.data[CONF_URL],
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )


class GestioHomeOptionsFlow(OptionsFlowWithReload):
    """Choose the meters again: add new ones, remove or rename others."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._found: list[Meter] = []
        self._chosen: list[str] = []

    @property
    def _current(self) -> dict[str, dict[str, str]]:
        return self.config_entry.options.get(CONF_METERS, {})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List the meters GestioHome shows now, plus the ones already added."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_METERS]:
                self._chosen = user_input[CONF_METERS]
                return await self.async_step_names()
            errors["base"] = "no_meter_chosen"
        elif not self._found:
            data = self.config_entry.data
            _, found, error = await async_discover(
                self.hass, data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD]
            )
            if error and error != "no_meters":
                return self.async_abort(reason=error)
            found_ids = {m.meter_id for m in found}
            # meters already added that GestioHome no longer lists can be kept
            # or removed; their statistics are never deleted
            found.extend(
                Meter(meter_id, "?", meter[CONF_NAME])
                for meter_id, meter in self._current.items()
                if meter_id not in found_ids
            )
            self._found = found
        return self.async_show_form(
            step_id="init",
            data_schema=select_schema(self._found, list(self._current)),
            errors=errors,
        )

    async def async_step_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask a name for each meter."""

        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_METERS: chosen_meters(
                        self._found, self._chosen, user_input, self._current
                    ),
                    CONF_KNOWN: [m.meter_id for m in self._found],
                }
            )
        return self.async_show_form(
            step_id="names",
            data_schema=names_schema(self._found, self._chosen, self._current),
        )
