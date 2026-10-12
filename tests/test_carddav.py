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
    reports = [c for c in session.calls if c[0] == "REPORT"]
    assert len(reports) == 2
    assert b"prop-filter" in reports[0][2]["data"]
    assert b"prop-filter" not in reports[1][2]["data"]


async def test_fetch_birthdays_raises_when_both_queries_fail():
    session = FakeSession({("REPORT", BOOK): [FakeResponse(501)]})
    with pytest.raises(CardDavHttpError) as err:
        await _client(session).fetch_birthdays(BOOK)
    assert err.value.status == 501


async def test_fetch_birthdays_falls_back_when_filtered_query_returns_nothing():
    # Server answers 207 but ignores partial retrieval: no address-data at all.
    empty = _multistatus(
        "<d:response><d:href>/c0.vcf</d:href><d:propstat><d:prop/>"
        "<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>"
    )
    full = _report(_vcard("1", "Erika", "1990-05-17"))
    session = FakeSession({("REPORT", BOOK): [FakeResponse(207, empty), FakeResponse(207, full)]})
    birthdays = await _client(session).fetch_birthdays(BOOK)
    assert [b.name for b in birthdays] == ["Erika"]
    assert len([c for c in session.calls if c[0] == "REPORT"]) == 2


async def test_fetch_birthdays_without_any_birthday_is_empty_not_an_error(caplog):
    report = _report(_vcard("1", "No Birthday", None))
    session = FakeSession({("REPORT", BOOK): [FakeResponse(207, report)]})
    assert await _client(session).fetch_birthdays(BOOK) == []
    assert "No birthdays found" in caplog.text
    assert "1 vCards seen" in caplog.text


def _listing(*names: str) -> bytes:
    """PROPFIND Depth 1 on an addressbook: the collection itself plus its vCards."""
    return _multistatus(
        _response(
            "/1234/carddavhome/card/",
            "<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>",
        ),
        *(
            _response(f"/1234/carddavhome/card/{name}.vcf", "<d:resourcetype/><d:getetag>\"x\"</d:getetag>")
            for name in names
        ),
    )


def _reports(session: FakeSession) -> list[bytes]:
    return [kwargs["data"] for method, _, kwargs in session.calls if method == "REPORT"]


async def test_multiget_gets_every_contact_even_if_filtered_query_would_not():
    three = _report(
        _vcard("1", "Erika", "1990-05-17"),
        _vcard("2", "Max", "--0101"),
        _vcard("3", "No Birthday", None),
    )
    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(207, _listing("a", "b", "c"))],
            ("REPORT", BOOK): [FakeResponse(207, three)],
        }
    )
    birthdays = await _client(session).fetch_birthdays(BOOK)

    assert [b.name for b in birthdays] == ["Erika", "Max"]
    reports = _reports(session)
    assert len(reports) == 1  # no addressbook-query involved
    assert b"addressbook-multiget" in reports[0]
    # Hrefs are sent as paths, one per listed vCard; the collection itself is not requested.
    assert reports[0].count(b"<d:href>") == 3
    assert b"<d:href>/1234/carddavhome/card/a.vcf</d:href>" in reports[0]
    assert b"<d:href>/1234/carddavhome/card/</d:href>" not in reports[0]


async def test_multiget_retries_in_full_when_partial_retrieval_is_ignored():
    one = _report(_vcard("1", "Erika", "1990-05-17"))
    three = _report(
        _vcard("1", "Erika", "1990-05-17"),
        _vcard("2", "Max", "--0101"),
        _vcard("3", "Anna", "1985-12-24"),
    )
    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(207, _listing("a", "b", "c"))],
            ("REPORT", BOOK): [FakeResponse(207, one), FakeResponse(207, three)],
        }
    )
    birthdays = await _client(session).fetch_birthdays(BOOK)

    assert [b.name for b in birthdays] == ["Erika", "Max", "Anna"]
    reports = _reports(session)
    assert len(reports) == 2
    assert b'name="BDAY"' in reports[0]
    assert b'name="BDAY"' not in reports[1]


async def test_multiget_rejecting_partial_retrieval_falls_back_to_full_vcards():
    three = _report(_vcard("1", "Erika", "1990-05-17"))
    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(207, _listing("a"))],
            ("REPORT", BOOK): [FakeResponse(400), FakeResponse(207, three)],
        }
    )
    assert [b.name for b in await _client(session).fetch_birthdays(BOOK)] == ["Erika"]


async def test_multiget_is_batched():
    names = [f"c{i}" for i in range(250)]

    def chunk(part: list[str]) -> FakeResponse:
        return FakeResponse(207, _report(*(_vcard(n, n, "--0101") for n in part)))

    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(207, _listing(*names))],
            ("REPORT", BOOK): [chunk(names[:100]), chunk(names[100:200]), chunk(names[200:])],
        }
    )
    assert len(await _client(session).fetch_birthdays(BOOK)) == 250
    assert len(_reports(session)) == 3


async def test_listing_not_supported_falls_back_to_addressbook_query():
    report = _report(_vcard("1", "Erika", "1990-05-17"))
    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(405)],
            ("REPORT", BOOK): [FakeResponse(207, report)],
        }
    )
    assert [b.name for b in await _client(session).fetch_birthdays(BOOK)] == ["Erika"]
    assert b"prop-filter" in _reports(session)[0]


async def test_multiget_returning_nothing_falls_back_to_addressbook_query():
    empty = _multistatus()
    report = _report(_vcard("1", "Erika", "1990-05-17"))
    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(207, _listing("a"))],
            ("REPORT", BOOK): [FakeResponse(207, empty), FakeResponse(207, empty), FakeResponse(207, report)],
        }
    )
    assert [b.name for b in await _client(session).fetch_birthdays(BOOK)] == ["Erika"]


async def test_warns_when_server_returns_fewer_contacts_than_it_lists(caplog):
    one = _report(_vcard("1", "Erika", "1990-05-17"))
    session = FakeSession(
        {
            ("PROPFIND", BOOK): [FakeResponse(207, _listing("a", "b", "c"))],
            ("REPORT", BOOK): [FakeResponse(207, one)],
        }
    )
    assert len(await _client(session).fetch_birthdays(BOOK)) == 1
    assert "listed 3 contacts but returned only 1" in caplog.text


async def test_multiget_auth_error_is_not_swallowed():
    session = FakeSession({("PROPFIND", BOOK): [FakeResponse(401)]})
    with pytest.raises(CardDavAuthError):
        await _client(session).fetch_birthdays(BOOK)


def test_parse_report_stats_explain_where_contacts_went():
    report = _report(
        _vcard("1", "Erika", "1990-05-17"),
        _vcard("2", "Same UID", "1991-06-18").replace("UID:2", "UID:1"),
        _vcard("3", "No Birthday", None),
        _vcard("4", "Weird", "circa 1980-07-19"),
        _vcard("5", "Weird 2", "circa 1981-08-20"),
    )
    birthdays, stats = parse_report(report)
    assert [b.name for b in birthdays] == ["Erika"]
    assert stats.cards == 5
    assert stats.duplicates == 1
    assert stats.without_bday == 1
    # Digits are masked so that no real dates end up in logs.
    assert dict(stats.unparsed) == {"BDAY:circa 9999-99-99": 2}


async def test_unreadable_bday_formats_are_warned_about(caplog):
    report = _report(_vcard("1", "Erika", "1990-05-17"), _vcard("2", "Weird", "um 1980"))
    session = FakeSession({("REPORT", BOOK): [FakeResponse(207, report)]})
    assert len(await _client(session).fetch_birthdays(BOOK)) == 1
    assert "could not be read" in caplog.text
    assert "BDAY:um 9999" in caplog.text
    assert "Weird" not in caplog.text


def test_parse_report_rejects_garbage():
    with pytest.raises(Exception, match="Invalid XML"):
        parse_report(b"<not xml")
