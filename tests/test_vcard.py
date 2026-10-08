"""vCard BDAY parsing."""

import pytest

from custom_components.carddav_bday_calendar.vcard import Birthday, parse_bday, parse_vcard


def _card(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VCARD", "VERSION:3.0", *lines, "END:VCARD"])


def test_plain_birthday():
    card = _card("UID:abc", "FN:Erika Mustermann", "BDAY:1990-05-17")
    assert parse_vcard(card) == Birthday("abc", "Erika Mustermann", 5, 17, 1990)


@pytest.mark.parametrize("value", ["--0517", "--05-17"])
def test_birthday_without_year(value):
    card = _card("UID:abc", "FN:Max", f"BDAY:{value}")
    assert parse_vcard(card) == Birthday("abc", "Max", 5, 17, None)


def test_compact_date_and_datetime():
    assert parse_bday("19900517") == (1990, 5, 17)
    assert parse_bday("1990-05-17T00:00:00Z") == (1990, 5, 17)


def test_apple_omit_year_param_drops_placeholder_year():
    card = _card("UID:abc", "FN:Max", "BDAY;X-APPLE-OMIT-YEAR=1604:1604-05-17")
    assert parse_vcard(card).year is None


def test_apple_placeholder_year_without_param():
    assert parse_bday("1604-05-17") == (None, 5, 17)


def test_group_prefix_and_folded_lines():
    card = _card(
        "UID:abc",
        "FN:Erika Muster",
        " mann",  # folded continuation of the FN value
        "item1.BDAY:1990-05-17",
    )
    parsed = parse_vcard(card)
    assert parsed.name == "Erika Mustermann"
    assert (parsed.month, parsed.day) == (5, 17)


def test_name_fallbacks_and_escapes():
    assert parse_vcard(_card("UID:1", "N:Mustermann;Erika;;;", "BDAY:--0101")).name == "Erika Mustermann"
    assert parse_vcard(_card("UID:2", "ORG:ACME\\, Inc.;Sales", "BDAY:--0101")).name == "ACME, Inc."
    assert parse_vcard(_card("UID:3", "FN:Meier\\, Hans", "BDAY:--0101")).name == "Meier, Hans"


def test_missing_uid_gets_stable_fallback():
    assert parse_vcard(_card("FN:Max", "BDAY:--0517")).uid == "Max:0517"


@pytest.mark.parametrize("value", ["", "circa 1800", "1990-02-30", "1990-13-01", "--0230", "1990-05"])
def test_unusable_birthdays(value):
    assert parse_vcard(_card("UID:abc", "FN:Max", f"BDAY:{value}")) is None


def test_leap_day_is_valid_without_year():
    assert parse_bday("--0229") == (None, 2, 29)


def test_card_without_bday():
    assert parse_vcard(_card("UID:abc", "FN:Max")) is None
