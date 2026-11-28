"""Tests for the GestioHome config and options flows."""

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
METERS = [Meter("2", "P1 / Consumo Calef", "Calor"), Meter("1", "P1 / ACS", "ACS")]
DWELLING = Dwelling("12345678TEST9", "Test Building / P1 / 2 / A")


def _patch_client(discover=None):
    client = AsyncMock()
    client.async_discover.side_effect = discover or (lambda: (DWELLING, METERS))
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
    """The user picks meters and names; IDs come from name and number."""
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
        assert result["step_id"] == "meters"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"meters": ["2"]}
        )
        assert result["step_id"] == "names"
        # the name suggested is the tab's
        key = next(iter(result["data_schema"].schema))
        assert key.default() == "Calor"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {key: "Calefacción"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Building / P1 / 2 / A"
    assert result["result"].unique_id == "12345678TEST9"
    assert result["data"]["url"] == "https://test.gestiohome.com"
    assert result["data"]["scode"] == "est9"
    assert result["options"]["meters"] == {
        "2": {"name": "Calefacción", "stat": "calefaccion_2"}
    }
    assert result["options"]["known"] == ["2", "1"]


async def test_user_flow_needs_a_meter(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Choosing no meter shows an error."""
    with _patch_client():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"meters": []}
        )
    assert result["errors"] == {"base": "no_meter_chosen"}


async def test_user_flow_invalid_auth(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A rejected login shows the error on the form."""
    with _patch_client(discover=InvalidAuth("no")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="12345678TEST9",
        data={
            "url": "https://test.gestiohome.com",
            "username": "user",
            "password": "old",
            "code": "12345678TEST9",
            "scode": "est9",
        },
        options={
            "meters": {"2": {"name": "Calor", "stat": "calor_2"}},
            "known": ["2"],
        },
    )


async def test_reauth_flow(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """A new password is stored and the entry reloaded."""
    entry = _entry()
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


async def test_options_flow_keeps_statistic_ids(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
) -> None:
    """Renaming keeps the statistic; a new meter gets name and number."""
    entry = _entry()
    entry.add_to_hass(hass)
    with (
        _patch_client(),
        patch("custom_components.gestiohome.async_setup_entry", return_value=True),
        patch("custom_components.gestiohome.async_unload_entry", return_value=True),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["step_id"] == "init"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"meters": ["2", "1"]}
        )
        keys = list(result["data_schema"].schema)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {keys[0]: "Calorcito", keys[1]: "ACS"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["meters"] == {
        "2": {"name": "Calorcito", "stat": "calor_2"},
        "1": {"name": "ACS", "stat": "acs_1"},
    }
