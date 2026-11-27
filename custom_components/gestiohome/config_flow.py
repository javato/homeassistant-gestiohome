"""Configuration flow (GUI)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector as sel
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import voluptuous as vol

from .api import CannotConnect, GestioHomeClient, InvalidAuth, normalize_url
from .const import (
    CONF_CODE,
    CONF_METERS,
    CONF_PASSWORD,
    CONF_SCODE,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
    SUPPLIES,
)

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


class GestioHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GestioHome."""

    VERSION = 1

    async def _async_validate(
        self, url: str, username: str, password: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Log in and find the dwelling and its meters; return (info, error)."""

        # a throwaway session: detached (not closed) when done, as HA asks
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=CookieJar()
        )
        client = GestioHomeClient(session, url, username, password)
        try:
            dwelling = await client.async_login()
            meters = {}
            for supply in SUPPLIES:
                if found := await client.async_get_meters(supply.page):
                    meters[supply.key] = [found[0].meter_id, found[0].name]
        except InvalidAuth:
            return None, "invalid_auth"
        except CannotConnect:
            _LOGGER.debug("Cannot connect to GestioHome", exc_info=True)
            return None, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating GestioHome")
            return None, "unknown"
        finally:
            session.detach()
        if not dwelling.code:
            return None, "unknown"
        if not meters:
            return None, "no_meters"
        return {"dwelling": dwelling, "meters": meters}, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the building URL and the credentials."""

        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            info, error = await self._async_validate(
                url, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                dwelling = info["dwelling"]
                await self.async_set_unique_id(dwelling.code.upper())
                self._abort_if_unique_id_configured()
                taken = {
                    entry.data.get(CONF_SCODE)
                    for entry in self._async_current_entries(include_ignore=False)
                }
                return self.async_create_entry(
                    title=dwelling.name or dwelling.code,
                    data={
                        CONF_URL: url,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CODE: dwelling.code.upper(),
                        CONF_SCODE: get_scode(dwelling.code, taken),
                        CONF_METERS: info["meters"],
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
            description_placeholders={
                "example_url": "https://mybuilding.gestiohome.com"
            },
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
            _, error = await self._async_validate(
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
