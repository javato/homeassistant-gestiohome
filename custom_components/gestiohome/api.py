"""Client for the GestioHome web (Usanca Metering Solutions)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
import html
import json
import logging
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)

APP_PATH = "/GestioHome/"
TIMEOUT = aiohttp.ClientTimeout(total=60)

# what the web answers instead of a page or data once the session is gone
_LOGIN_REDIRECT = re.compile(r"document\.location\s*=\s*[\"']login\.php")
_TAG = re.compile(r"<[^>]+>")
_APP_CODE = re.compile(r"C[óo]digo APP M[óo]vil\s*:\s*([A-Za-z0-9]+)")
_DWELLING = re.compile(r"Vivienda\s*:\s*(.+?)\s+C[óo]digo APP")
_MEASURES_SELECT = re.compile(
    r"<select[^>]*id=[\"']measuresSel[\"'][^>]*>(.*?)</select>", re.S | re.I
)
_OPTION = re.compile(r"<option[^>]*value=[\"'](\d+)[\"'][^>]*>([^<]*)</option>", re.I)
# the tabs of the web's menu (Calor, Frío, ACS, ...): links to iu*.php pages
_TAB = re.compile(r"<a[^>]*href=[\"'](iu\w+\.php)[\"'][^>]*>(.*?)</a>", re.S | re.I)
# a page that sends the browser elsewhere with JavaScript
_JS_REDIRECT = re.compile(r"document\.location\s*=\s*[\"']([\w./-]+\.php)[\"']")


class GestioHomeError(Exception):
    """Base error."""


class CannotConnect(GestioHomeError):
    """The web did not answer or answered something unexpected."""


class InvalidAuth(GestioHomeError):
    """The web rejected the username or the password."""


@dataclass(frozen=True, slots=True)
class Tab:
    """A tab of the web's menu."""

    page: str
    label: str


@dataclass(frozen=True, slots=True)
class Meter:
    """A meter of the dwelling, as a tab of the web lists it."""

    meter_id: str
    name: str
    tab: str = ""


@dataclass(frozen=True, slots=True)
class Reading:
    """An absolute meter reading at a moment."""

    datetime: datetime
    value: float


@dataclass(frozen=True, slots=True)
class Readings:
    """The readings of a meter, oldest first, and their unit."""

    readings: list[Reading]
    unit: str | None


@dataclass(frozen=True, slots=True)
class Dwelling:
    """What the web shows about the dwelling once logged in."""

    code: str | None
    name: str | None


def normalize_url(url: str) -> str:
    """Return the base URL of a building's GestioHome (scheme and host)."""

    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    # accept a pasted page URL such as https://x.gestiohome.com/GestioHome/login.php
    if (idx := url.lower().find("/gestiohome")) != -1:
        url = url[:idx]
    return url


def parse_dwelling(page: str) -> Dwelling:
    """Read the dwelling code and name from any logged-in page."""

    text = " ".join(html.unescape(_TAG.sub(" ", page)).split())
    code = m.group(1) if (m := _APP_CODE.search(text)) else None
    name = m.group(1) if (m := _DWELLING.search(text)) else None
    return Dwelling(code=code, name=name)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def parse_tabs(page: str) -> list[Tab]:
    """Read the tabs of the web's menu, in order and without repeats."""

    tabs: dict[str, Tab] = {}
    for href, label in _TAB.findall(page):
        if href not in tabs:
            tabs[href] = Tab(href, _text(label) or href)
    return list(tabs.values())


def parse_meters(page: str, tab: str = "") -> list[Meter]:
    """Read the meters a tab lists (the select the web's chart is drawn from)."""

    if not (select := _MEASURES_SELECT.search(page)):
        return []
    return [
        Meter(meter_id, _text(name), tab)
        for meter_id, name in _OPTION.findall(select.group(1))
    ]


def parse_unit(payload: dict, meter_id: str) -> str | None:
    """Return the unit a getStatisticData.php answer gives for a meter."""

    series = payload.get(meter_id)
    if isinstance(series, dict) and isinstance(unit := series.get("seriesUnit"), str):
        return unit.strip() or None
    return None


def parse_readings(payload: dict, meter_id: str, tz: tzinfo) -> list[Reading]:
    """Turn a getStatisticData.php answer into readings, oldest first.

    Times come split in fields, in the building's local time, with JavaScript
    months (0 = January). Repeated times keep the last value.
    """

    series = payload.get(meter_id)
    if not isinstance(series, dict) or not series.get("yvalues"):
        return []
    found: dict[datetime, float] = {}
    for year, month, day, hour, minute, second, value in zip(
        series["xYear"],
        series["xMonth"],
        series["xDay"],
        series["xHour"],
        series["xMinute"],
        series.get("xSecond") or ["0"] * len(series["yvalues"]),
        series["yvalues"],
        strict=False,
    ):
        if value is None:
            continue
        try:
            moment = datetime(
                int(year),
                int(month) + 1,
                int(day),
                int(hour),
                int(minute),
                int(second),
                tzinfo=tz,
            )
            found[moment] = float(value)
        except (TypeError, ValueError):
            _LOGGER.debug("Skipping malformed reading %s", (year, month, day, value))
    return [Reading(dt, found[dt]) for dt in sorted(found)]


class GestioHomeClient:
    """Log in to GestioHome and read meters through the web's own endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the client; the session must keep its own cookies."""

        self._session = session
        self._base = normalize_url(url) + APP_PATH
        self._username = username
        self._password = password
        self._logged_in = False
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the URL of the web's pages."""
        return self._base

    async def _request(self, method: str, page: str, data: dict | None = None) -> str:
        try:
            async with self._session.request(
                method, self._base + page, data=data, timeout=TIMEOUT
            ) as resp:
                if resp.status >= 400:
                    raise CannotConnect(f"{page}: HTTP {resp.status}")
                return await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"{page}: {err}") from err

    async def async_login(self) -> Dwelling:
        """Log in and return the dwelling; raise InvalidAuth if rejected."""

        async with self._lock:
            return parse_dwelling(await self._login())

    async def _login(self) -> str:
        """Log in and return the page the web opens; raise InvalidAuth if rejected.

        That page is what the login answers (redirects followed), or the page
        it sends the browser to with JavaScript.
        """

        self._logged_in = False
        await self._request("GET", "login.php")  # start the PHP session
        page = await self._request(
            "POST",
            "login.php",
            {
                "isAPP": "",
                "tokenAPP": "",
                "username": self._username,
                "userpass": self._password,
            },
        )
        if (m := _JS_REDIRECT.search(page)) and not _LOGIN_REDIRECT.search(page):
            page = await self._request("GET", m.group(1))
        if _LOGIN_REDIRECT.search(page) or "userpass" in page:
            raise InvalidAuth("GestioHome rejected the credentials")
        self._logged_in = True
        return page

    async def _get_logged_in(self, method: str, page: str, data=None) -> str:
        """Request a page, logging in again once if the session expired."""

        async with self._lock:
            if not self._logged_in:
                await self._login()
            text = await self._request(method, page, data)
            if _LOGIN_REDIRECT.search(text):
                _LOGGER.debug("GestioHome session expired, logging in again")
                await self._login()
                text = await self._request(method, page, data)
                if _LOGIN_REDIRECT.search(text):
                    raise CannotConnect(f"{page}: still asks to log in")
            return text

    async def async_discover(self) -> tuple[Dwelling, list[Meter]]:
        """Return the dwelling and every meter the tabs of the web list.

        Nothing about the supplies is assumed: the tabs come from the web's
        menu, and a tab counts if it lists meters (the others, such as
        receipts or alarms, list none).
        """

        # a fresh login: the page it opens has the menu
        async with self._lock:
            landing = await self._login()
        meters: dict[str, Meter] = {}
        for tab in parse_tabs(landing):
            for meter in parse_meters(
                await self._get_logged_in("GET", tab.page), tab.label
            ):
                meters.setdefault(meter.meter_id, meter)
        return parse_dwelling(landing), list(meters.values())

    async def async_get_readings(
        self, meter_id: str, start: date, end: date, tz: tzinfo
    ) -> Readings:
        """Return the absolute readings of a meter between two dates (inclusive)."""

        text = await self._get_logged_in(
            "POST",
            "getStatisticData.php",
            {"meter": meter_id, "daterange": f"{start.isoformat()} {end.isoformat()}"},
        )
        try:
            payload = json.loads(text)
        except ValueError as err:
            raise CannotConnect(
                f"getStatisticData.php: unexpected answer {text[:100]!r}"
            ) from err
        if not isinstance(payload, dict):
            raise CannotConnect("getStatisticData.php: unexpected answer")
        return Readings(
            parse_readings(payload, meter_id, tz), parse_unit(payload, meter_id)
        )
