"""Minimal vCard parsing: just enough to get a display name and a birthday."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Apple stores "birthday without year" as a placeholder year (1604) and
# marks it with the X-APPLE-OMIT-YEAR parameter.
APPLE_PLACEHOLDER_YEAR = 1604

_UNFOLD_RE = re.compile(r"\r?\n[ \t]")
_FULL_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_NO_YEAR_RE = re.compile(r"^--(\d{2})-?(\d{2})$")
_ESCAPE_RE = re.compile(r"\\(.)")


@dataclass(frozen=True, slots=True)
class Birthday:
    """A contact's birthday. ``year`` is None when the contact has no birth year."""

    uid: str
    name: str
    month: int
    day: int
    year: int | None = None


def _unescape(value: str) -> str:
    return _ESCAPE_RE.sub(lambda m: " " if m[1] in "nN" else m[1], value).strip()


def parse_bday(
    value: str, params: list[str] | None = None
) -> tuple[int | None, int, int] | None:
    """Parse a BDAY value into (year | None, month, day).

    Accepts YYYY-MM-DD, YYYYMMDD, --MM-DD, --MMDD and date-times (the time
    part is dropped). Returns None for anything else (e.g. free text).
    """
    value = value.strip().split("T", 1)[0]
    omit_year = any(p.upper().startswith("X-APPLE-OMIT-YEAR") for p in params or [])

    year: int | None
    if match := _FULL_RE.match(value):
        year, month, day = int(match[1]), int(match[2]), int(match[3])
        if omit_year or year in (0, APPLE_PLACEHOLDER_YEAR):
            year = None
    elif match := _NO_YEAR_RE.match(value):
        year, month, day = None, int(match[1]), int(match[2])
    else:
        return None

    try:
        date(2000, month, day)  # 2000 is a leap year, so 29 Feb stays valid
    except ValueError:
        return None
    return year, month, day


def parse_vcard(text: str) -> Birthday | None:
    """Return the Birthday for a single vCard, or None if it has no usable BDAY."""
    uid = fn = n_name = org = None
    bday: tuple[int | None, int, int] | None = None

    for line in _UNFOLD_RE.sub("", text).splitlines():
        head, sep, value = line.partition(":")
        if not sep:
            continue
        name, *params = head.split(";")
        name = name.rsplit(".", 1)[-1].upper()  # drop Apple's "item1." group prefix

        if name == "END":
            break
        if name == "UID" and uid is None:
            uid = value.strip()
        elif name == "FN" and fn is None:
            fn = _unescape(value)
        elif name == "N" and n_name is None:
            parts = value.split(";")
            given = _unescape(parts[1]) if len(parts) > 1 else ""
            family = _unescape(parts[0])
            n_name = " ".join(p for p in (given, family) if p)
        elif name == "ORG" and org is None:
            org = _unescape(value.split(";")[0])
        elif name == "BDAY" and bday is None:
            bday = parse_bday(value, params)

    if bday is None:
        return None

    year, month, day = bday
    display_name = fn or n_name or org or "?"
    return Birthday(
        uid=uid or f"{display_name}:{month:02d}{day:02d}",
        name=display_name,
        month=month,
        day=day,
        year=year,
    )
