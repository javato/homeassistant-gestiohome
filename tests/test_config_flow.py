"""Tests for the GestioHome config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gestiohome.api import Dwelling, InvalidAuth, Meter
from custom_components.gestiohome.config_flow import get_scode
from custom_components.gestiohome.const import DOMAIN

USER_INPUT = {
    "url": "https://test.gestiohome.com/GestioHome/login.php",
    "username": "user",
    "password": "secret",
}


def _patch_client(login=None):
    client = AsyncMock()
    client.async_login.side_effect = login or (
        lambda: Dwelling("12345678TEST9", "Edificio Test / P1 / 2 / A")
    )

    async def meters(page: str):
        return {
            "iuACS.php": [Meter("1", "ACS")],
            "iuCalor.php": [Meter("2", "Calor")],
        }.get(page, [])

    client.async_get_meters.side_effect = meters
    return patch(
        "custom_components.gestiohome.config_flow.GestioHomeClient",
        return_value=client,
    )


def test_get_scode() -> None:
    """The shortest free suffix of 4+ characters, like edata's scups."""
    assert get_scode("12345678TEST9", set()) == "est9"
    assert get_scode("12345678TEST9", {"est9"}) == "test9"


async def test_user_flow(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A valid login creates the entry with the meters found."""
    with (
        _patch_client(),
        patch("custom_components.gestiohome.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Edificio Test / P1 / 2 / A"
    assert result["result"].unique_id == "12345678TEST9"
    assert result["data"]["url"] == "https://test.gestiohome.com"
    assert result["data"]["scode"] == "est9"
    assert result["data"]["meters"] == {"acs": ["1", "ACS"], "calor": ["2", "Calor"]}


async def test_user_flow_invalid_auth(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A rejected login shows the error on the form."""
    with _patch_client(login=InvalidAuth("no")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_flow(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A new password is stored and the entry reloaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345678TEST9",
        data={
            "url": "https://test.gestiohome.com",
            "username": "user",
            "password": "old",
            "code": "12345678TEST9",
            "scode": "est9",
            "meters": {"acs": ["1", "ACS"]},
        },
    )
    entry.add_to_hass(hass)
    with (
        _patch_client(),
        patch("custom_components.gestiohome.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"
