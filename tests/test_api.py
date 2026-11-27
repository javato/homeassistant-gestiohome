"""Tests for the GestioHome web parsing."""

from __future__ import annotations

from homeassistant.util import dt as dt_util

from custom_components.gestiohome.api import (
    normalize_url,
    parse_dwelling,
    parse_meters,
    parse_readings,
)

from .conftest import build_readings, payload

HEADER = (
    '<div id="codCliente" class="izquierda">\n\t\t<strong>Vivienda</strong>: '
    "Edificio Test / P1&nbsp;&nbsp;/&nbsp;2&nbsp;/&nbsp;A\t\t\t"
    '<span class="textoBarra"><strong>Código APP Móvil</strong>: 12345678TEST9 '
    "&nbsp;&nbsp;</span>"
)
SELECT = (
    '<select multiple="" name="measuresSel[]" id="measuresSel" onchange="x();">'
    '\n\t\t<option value="12345" selected="selected">P1  / 2 / A / Energia/Consumo '
    "Calef</option>\n</select>"
    '<select name="other"><option value="1">Año</option></select>'
)


def test_normalize_url() -> None:
    """Pasted URLs are reduced to the building's base URL."""
    assert normalize_url("x.gestiohome.com") == "https://x.gestiohome.com"
    assert (
        normalize_url("https://x.gestiohome.com/GestioHome/login.php")
        == "https://x.gestiohome.com"
    )


def test_parse_dwelling() -> None:
    """The dwelling code and name come from the page header."""
    dwelling = parse_dwelling(HEADER)
    assert dwelling.code == "12345678TEST9"
    assert dwelling.name == "Edificio Test / P1 / 2 / A"


def test_parse_meters() -> None:
    """Only the options of the meters select count."""
    meters = parse_meters(SELECT)
    assert [(m.meter_id, m.name) for m in meters] == [
        ("12345", "P1 / 2 / A / Energia/Consumo Calef")
    ]
    assert parse_meters("<html></html>") == []


def test_parse_readings() -> None:
    """JavaScript months are converted and the order is kept."""
    tz = dt_util.get_time_zone("Europe/Madrid")
    readings = build_readings(3)
    parsed = parse_readings(payload("1", readings), "1", tz)
    assert [r.value for r in parsed] == [1000.0, 1010.0, 1020.0]
    assert parsed[0].datetime.month == 10
    assert parse_readings({"1": {"hayDatos": [False]}, "error": "x"}, "1", tz) == []
