"""Small CardDAV client: addressbook discovery and birthday retrieval.

Deliberately free of Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

import aiohttp

from .vcard import Birthday, parse_vcard

_LOGGER = logging.getLogger(__name__)

DAV = "{DAV:}"
CARDDAV = "{urn:ietf:params:xml:ns:carddav}"

_TIMEOUT = aiohttp.ClientTimeout(total=60)
_MAX_REDIRECTS = 5

_PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:resourcetype/>
    <d:displayname/>
    <d:current-user-principal/>
    <c:addressbook-home-set/>
  </d:prop>
</d:propfind>"""

# Preferred: let the server filter on BDAY and only send the properties we need
# (iCloud vCards otherwise carry base64 photos).
_QUERY_BIRTHDAYS = """<?xml version="1.0" encoding="utf-8"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <c:address-data>
      <c:prop name="UID"/>
      <c:prop name="FN"/>
      <c:prop name="N"/>
      <c:prop name="ORG"/>
      <c:prop name="BDAY"/>
    </c:address-data>
  </d:prop>
  <c:filter>
    <c:prop-filter name="BDAY"/>
  </c:filter>
</c:addressbook-query>"""

# Fallback for servers that reject filters or partial address-data.
_QUERY_ALL = """<?xml version="1.0" encoding="utf-8"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <c:address-data/>
  </d:prop>
  <c:filter/>
</c:addressbook-query>"""


class CardDavError(Exception):
    """Base error."""


class CardDavConnectionError(CardDavError):
    """The server could not be reached."""


class CardDavAuthError(CardDavError):
    """The server rejected the credentials."""


class CardDavHttpError(CardDavError):
    """The server answered with an unexpected HTTP status."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} from {url}")
        self.status = status


@dataclass(frozen=True, slots=True)
class AddressBook:
    url: str
    name: str


@dataclass(slots=True)
class Resource:
    """One <response> of a PROPFIND multistatus."""

    href: str
    displayname: str | None = None
    is_addressbook: bool = False
    principal: str | None = None
    home_set: str | None = None


def _inner_href(prop: ET.Element, tag: str, base_url: str) -> str | None:
    element = prop.find(tag)
    if element is None:
        return None
    href = element.findtext(f"{DAV}href")
    return urljoin(base_url, href.strip()) if href and href.strip() else None


def parse_multistatus(data: bytes, base_url: str) -> list[Resource]:
    """Parse a PROPFIND multistatus body; hrefs are resolved against ``base_url``."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise CardDavError(f"Invalid XML from server: {err}") from err

    resources: list[Resource] = []
    for response in root.iter(f"{DAV}response"):
        href = response.findtext(f"{DAV}href")
        if not href:
            continue
        resource = Resource(href=urljoin(base_url, href.strip()))
        for propstat in response.findall(f"{DAV}propstat"):
            if " 200" not in (propstat.findtext(f"{DAV}status") or ""):
                continue
            prop = propstat.find(f"{DAV}prop")
            if prop is None:
                continue
            resource_type = prop.find(f"{DAV}resourcetype")
            if resource_type is not None and resource_type.find(f"{CARDDAV}addressbook") is not None:
                resource.is_addressbook = True
            if name := prop.findtext(f"{DAV}displayname"):
                resource.displayname = name.strip()
            resource.principal = resource.principal or _inner_href(
                prop, f"{DAV}current-user-principal", base_url
            )
            resource.home_set = resource.home_set or _inner_href(
                prop, f"{CARDDAV}addressbook-home-set", base_url
            )
        resources.append(resource)
    return resources


_BDAY_LINE_RE = re.compile(r"^(?:[\w-]+\.)?BDAY([^:\r\n]*):([^\r\n]*)", re.IGNORECASE | re.MULTILINE)


@dataclass(slots=True)
class ReportStats:
    """Counters for debugging a REPORT response; contains no personal data."""

    cards: int = 0  # vCards in the response
    without_bday: int = 0  # vCards that have no BDAY line at all
    duplicates: int = 0  # vCards dropped because their UID was already seen
    unparsed: Counter[str] = field(default_factory=Counter)  # masked BDAY formats we could not read


def _masked_bday(card: str) -> str | None:
    """Return the BDAY line with every digit replaced by 9 (shows the format only)."""
    match = _BDAY_LINE_RE.search(card)
    if match is None:
        return None
    return re.sub(r"\d", "9", f"BDAY{match[1]}:{match[2]}")[:80]


def parse_report(data: bytes) -> tuple[list[Birthday], ReportStats]:
    """Extract birthdays from an addressbook-query REPORT response.

    Returns (birthdays deduplicated by UID, statistics about the response).
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise CardDavError(f"Invalid XML from server: {err}") from err

    birthdays: dict[str, Birthday] = {}
    stats = ReportStats()
    for element in root.iter(f"{CARDDAV}address-data"):
        if not element.text:
            continue
        stats.cards += 1
        if birthday := parse_vcard(element.text):
            if birthday.uid in birthdays:
                stats.duplicates += 1
            else:
                birthdays[birthday.uid] = birthday
        elif (masked := _masked_bday(element.text)) is not None:
            stats.unparsed[masked] += 1
        else:
            stats.without_bday += 1
    return list(birthdays.values()), stats


def _name_from_href(href: str) -> str:
    return unquote(urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1]) or href


class CardDavClient:
    """CardDAV client using an aiohttp session (HA's shared session in practice)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._url = url
        self._auth = aiohttp.BasicAuth(username, password)

    async def _request(
        self, method: str, url: str, *, depth: str, body: str
    ) -> tuple[int, bytes, str]:
        """Send a request, following redirects manually so credentials survive them.

        Returns (status, body, final_url). Raises on 401, network errors and
        https -> http downgrades.
        """
        headers = {"Content-Type": "application/xml; charset=utf-8", "Depth": depth}
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                async with self._session.request(
                    method,
                    url,
                    auth=self._auth,
                    data=body.encode(),
                    headers=headers,
                    allow_redirects=False,
                    timeout=_TIMEOUT,
                ) as response:
                    location = response.headers.get("Location")
                    if response.status in (301, 302, 307, 308) and location:
                        target = urljoin(url, location)
                        if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                            raise CardDavError("Refusing redirect from https to http")
                        url = target
                        continue
                    if response.status == 401:
                        raise CardDavAuthError("Invalid credentials")
                    return response.status, await response.read(), url
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise CardDavConnectionError(str(err) or type(err).__name__) from err
        raise CardDavError("Too many redirects")

    async def _propfind(self, url: str, depth: str) -> list[Resource]:
        status, data, final_url = await self._request(
            "PROPFIND", url, depth=depth, body=_PROPFIND_BODY
        )
        if status != 207:
            raise CardDavHttpError(status, final_url)
        return parse_multistatus(data, final_url)

    async def _discover_from(self, start_url: str) -> list[AddressBook]:
        resources = await self._propfind(start_url, "0")

        # The URL may already be an addressbook (e.g. a Radicale collection URL).
        for resource in resources:
            if resource.is_addressbook:
                return [
                    AddressBook(
                        resource.href,
                        resource.displayname or _name_from_href(resource.href),
                    )
                ]

        home = next((r.home_set for r in resources if r.home_set), None)
        if home is None:
            principal = next((r.principal for r in resources if r.principal), None)
            if principal:
                home_resources = await self._propfind(principal, "0")
                home = next((r.home_set for r in home_resources if r.home_set), None)
        if home is None:
            return []

        return [
            AddressBook(r.href, r.displayname or _name_from_href(r.href))
            for r in await self._propfind(home, "1")
            if r.is_addressbook
        ]

    async def discover_addressbooks(self) -> list[AddressBook]:
        """Find addressbook collections starting from the configured URL.

        Works with the bare server URL (https://contacts.icloud.com), a
        .well-known-capable host, or a direct addressbook URL.
        """
        first_error: CardDavHttpError | None = None
        for start_url in (self._url, urljoin(self._url, "/.well-known/carddav")):
            try:
                books = await self._discover_from(start_url)
            except CardDavHttpError as err:
                # Keep the first one: it concerns the URL the user actually entered.
                first_error = first_error or err
                continue
            if books:
                return books
        if first_error is not None:
            raise first_error
        return []

    async def fetch_birthdays(self, addressbook_url: str) -> list[Birthday]:
        """Return all contacts with a usable BDAY from one addressbook."""
        status = 0
        answered = False
        stats = ReportStats()
        for label, body in (("filtered", _QUERY_BIRTHDAYS), ("unfiltered", _QUERY_ALL)):
            status, data, _ = await self._request(
                "REPORT", addressbook_url, depth="1", body=body
            )
            if status != 207:
                _LOGGER.debug(
                    "REPORT (%s) on %s returned HTTP %s, trying fallback",
                    label,
                    addressbook_url,
                    status,
                )
                continue
            answered = True
            # Parsing can be a few MB of XML if the server ignored partial retrieval.
            birthdays, stats = await asyncio.get_running_loop().run_in_executor(
                None, parse_report, data
            )
            _LOGGER.debug(
                "REPORT (%s) on %s: %d vCards, %d birthdays, %d without BDAY, "
                "%d duplicate UIDs, unreadable BDAY formats: %s",
                label,
                addressbook_url,
                stats.cards,
                len(birthdays),
                stats.without_bday,
                stats.duplicates,
                dict(stats.unparsed) or "none",
            )
            if stats.unparsed:
                _LOGGER.warning(
                    "%d contacts have a BDAY value that could not be read "
                    "(digits shown as 9): %s",
                    sum(stats.unparsed.values()),
                    dict(stats.unparsed),
                )
            if birthdays:
                return birthdays
            # A 207 without any birthday can also mean the server ignored or
            # mishandled the filter / partial retrieval, so try the plain query too.

        if not answered:
            raise CardDavHttpError(status, addressbook_url)
        _LOGGER.warning(
            "No birthdays found in addressbook %s (%d vCards seen in the last response)",
            addressbook_url,
            stats.cards,
        )
        return []
