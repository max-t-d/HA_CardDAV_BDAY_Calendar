"""CardDAV client against a scripted fake session (iCloud-like flow)."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.carddav_bday_calendar.carddav import (
    CardDavAuthError,
    CardDavClient,
    CardDavConnectionError,
    CardDavHttpError,
    parse_report,
)

ROOT = "https://contacts.icloud.com"
PRINCIPAL = "https://contacts.icloud.com:443/1234/principal/"
HOME = "https://p56-contacts.icloud.com:443/1234/carddavhome/"
BOOK = "https://p56-contacts.icloud.com:443/1234/carddavhome/card/"


def _multistatus(*responses: str) -> bytes:
    body = "".join(responses)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
        f"{body}</d:multistatus>"
    ).encode()


def _response(href: str, props: str, status: str = "HTTP/1.1 200 OK") -> str:
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat>"
        f"<d:prop>{props}</d:prop><d:status>{status}</d:status>"
        "</d:propstat></d:response>"
    )


# iCloud answers with absolute hrefs that carry an explicit port.
PRINCIPAL_RESPONSE = _multistatus(
    _response(
        "/",
        f"<d:current-user-principal><d:href>{PRINCIPAL}</d:href></d:current-user-principal>",
    )
)
HOME_SET_RESPONSE = _multistatus(
    _response(
        "/1234/principal/",
        f"<c:addressbook-home-set><d:href>{HOME}</d:href></c:addressbook-home-set>",
    )
)
HOME_LISTING = _multistatus(
    _response(HOME, "<d:resourcetype><d:collection/></d:resourcetype>"),
    _response(
        "/1234/carddavhome/card/",
        "<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>"
        "<d:displayname>Kontakte</d:displayname>",
    ),
)


def _vcard(uid: str, name: str, bday: str | None) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}", f"FN:{name}"]
    if bday:
        lines.append(f"BDAY:{bday}")
    lines.append("END:VCARD")
    return "\n".join(lines)


def _report(*cards: str) -> bytes:
    return _multistatus(
        *(
            f"<d:response><d:href>/c{i}.vcf</d:href><d:propstat><d:prop>"
            f"<c:address-data><![CDATA[{card}]]></c:address-data>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            for i, card in enumerate(cards)
        )
    )


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Maps (method, url) to a queue of responses; records every request."""

    def __init__(self, script: dict[tuple[str, str], list]):
        self._script = {key: list(values) for key, values in script.items()}
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        queue = self._script.get((method, url))
        if not queue:
            return FakeResponse(404)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


def _client(session: FakeSession, url: str = ROOT) -> CardDavClient:
    return CardDavClient(session, url, "user@example.com", "app-password")


async def test_icloud_style_discovery():
    session = FakeSession(
        {
            ("PROPFIND", ROOT): [FakeResponse(207, PRINCIPAL_RESPONSE)],
            ("PROPFIND", PRINCIPAL): [FakeResponse(207, HOME_SET_RESPONSE)],
            ("PROPFIND", HOME): [FakeResponse(207, HOME_LISTING)],
        }
    )
    books = await _client(session).discover_addressbooks()
    assert [(b.url, b.name) for b in books] == [(BOOK, "Kontakte")]
    # Auth is sent explicitly on every hop, including the other host.
    assert all(kwargs["auth"].login == "user@example.com" for _, _, kwargs in session.calls)


async def test_direct_addressbook_url():
    listing = _multistatus(
        _response(
            "/dav/book/",
            "<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>",
        )
    )
    url = "https://radicale.example/dav/book/"
    session = FakeSession({("PROPFIND", url): [FakeResponse(207, listing)]})
    books = await _client(session, url).discover_addressbooks()
    assert [(b.url, b.name) for b in books] == [(url, "book")]


async def test_well_known_fallback():
    wk = "https://example.org/.well-known/carddav"
    session = FakeSession(
        {
            ("PROPFIND", "https://example.org"): [FakeResponse(405)],
            ("PROPFIND", wk): [FakeResponse(207, HOME_LISTING.replace(b"carddavhome/card/", b"x/"))],
        }
    )
    # The well-known response lists an addressbook directly.
    books = await _client(session, "https://example.org").discover_addressbooks()
    assert len(books) == 1


async def test_redirect_is_followed_with_credentials():
    session = FakeSession(
        {
            ("PROPFIND", ROOT): [FakeResponse(301, headers={"Location": "https://other.example/dav/"})],
            ("PROPFIND", "https://other.example/dav/"): [FakeResponse(207, HOME_LISTING)],
        }
    )
    books = await _client(session).discover_addressbooks()
    assert len(books) == 1
    assert session.calls[1][2]["auth"].password == "app-password"


async def test_https_to_http_redirect_is_refused():
    session = FakeSession(
        {("PROPFIND", ROOT): [FakeResponse(302, headers={"Location": "http://evil.example/"})]}
    )
    with pytest.raises(Exception, match="Refusing redirect"):
        await _client(session).discover_addressbooks()
    assert len(session.calls) == 1


async def test_401_is_auth_error():
    session = FakeSession({("PROPFIND", ROOT): [FakeResponse(401)]})
    with pytest.raises(CardDavAuthError):
        await _client(session).discover_addressbooks()


async def test_network_error_is_connection_error():
    session = FakeSession({("PROPFIND", ROOT): [aiohttp.ClientConnectionError("boom")]})
    with pytest.raises(CardDavConnectionError):
        await _client(session).discover_addressbooks()


async def test_unreachable_dav_raises_http_error():
    session = FakeSession({})  # everything 404
    with pytest.raises(CardDavHttpError):
        await _client(session).discover_addressbooks()


async def test_fetch_birthdays_falls_back_to_unfiltered_query():
    report = _report(
        _vcard("1", "Erika", "1990-05-17"),
        _vcard("2", "No Birthday", None),
        _vcard("3", "Max", "--0101"),
        _vcard("1", "Erika (duplicate)", "1990-05-17"),
    )
    session = FakeSession({("REPORT", BOOK): [FakeResponse(400), FakeResponse(207, report)]})
    birthdays = await _client(session).fetch_birthdays(BOOK)

    assert [(b.uid, b.name) for b in birthdays] == [("1", "Erika"), ("3", "Max")]
    assert len(session.calls) == 2
    assert b"prop-filter" in session.calls[0][2]["data"]
    assert b"prop-filter" not in session.calls[1][2]["data"]


async def test_fetch_birthdays_raises_when_both_queries_fail():
    session = FakeSession({("REPORT", BOOK): [FakeResponse(501)]})
    with pytest.raises(CardDavHttpError) as err:
        await _client(session).fetch_birthdays(BOOK)
    assert err.value.status == 501


def test_parse_report_rejects_garbage():
    with pytest.raises(Exception, match="Invalid XML"):
        parse_report(b"<not xml")
