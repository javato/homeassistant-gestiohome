"""Tests for the GestioHome web parsing."""

from __future__ import annotations

from homeassistant.util import dt as dt_util
import pytest

from custom_components.gestiohome.api import (
    GestioHomeClient,
    InvalidAuth,
    normalize_url,
    parse_dwelling,
    parse_meters,
    parse_readings,
    parse_tabs,
    parse_unit,
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


MENU = (
    '<ul class="nav"><li class="active"><a href="iuCalor.php">Calor</a></li>'
    '<li><a href="iuFrio.php">Fr&iacute;o</a></li>'
    '<li><a href="iuACS.php"><span>ACS</span></a></li>'
    '<li><a href="iuCalor.php">Calor</a></li></ul>'
    '<a href="cambiaPassword.php">Gestionar</a><a href="closeSession.php">Salir</a>'
)


def test_parse_tabs() -> None:
    """Tabs are the menu's iu*.php links, in order and once each."""
    assert [(t.page, t.label) for t in parse_tabs(MENU)] == [
        ("iuCalor.php", "Calor"),
        ("iuFrio.php", "Frío"),
        ("iuACS.php", "ACS"),
    ]


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
    meters = parse_meters(SELECT, "Calor")
    assert [(m.meter_id, m.name, m.tab) for m in meters] == [
        ("12345", "P1 / 2 / A / Energia/Consumo Calef", "Calor")
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
    assert parse_unit(payload("1", readings), "1") == "kWh"
    assert parse_unit({"1": {"hayDatos": [False]}}, "1") is None


class FakeSession:
    """Answers the web's pages from a dict, like a logged-in browser."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, str]] = []

    def request(self, method, url, data=None, timeout=None):
        page = url.split("/GestioHome/", 1)[1]
        self.requests.append((method, page))
        text = self.pages.get(page, "")

        class _Resp:
            status = 200

            async def text(self, errors=None):
                return text

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return _Resp()


async def test_discover_follows_the_menu() -> None:
    """Meters come from every tab of the menu that lists any."""
    calor = HEADER + MENU + SELECT
    session = FakeSession(
        {
            "": "<script>document.location='iuCalor.php'</script>",
            "iuCalor.php": calor,
            "iuFrio.php": MENU,
            "iuACS.php": MENU
            + '<select id="measuresSel"><option value="777">ACS 1</option>'
            + '<option value="12345">dup</option></select>',
        }
    )
    client = GestioHomeClient(session, "x.gestiohome.com", "user", "pw")
    dwelling, meters = await client.async_discover()
    assert dwelling.code == "12345678TEST9"
    assert [(m.meter_id, m.tab) for m in meters] == [
        ("12345", "Calor"),
        ("777", "ACS"),
    ]
    assert ("POST", "login.php") in session.requests
    assert all(page != "closeSession.php" for _, page in session.requests)


async def test_login_rejected() -> None:
    """The web sending back to the login page means wrong credentials."""
    session = FakeSession({"": "<form><input name='userpass'></form>"})
    client = GestioHomeClient(session, "x.gestiohome.com", "user", "bad")
    with pytest.raises(InvalidAuth):
        await client.async_login()
