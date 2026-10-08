"""Date logic for birthdays. Pure Python, no Home Assistant imports."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from .vcard import Birthday


def _is_leap(year: int) -> bool:
    # Not stdlib "calendar": this package has its own calendar.py platform module.
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def occurrence_in_year(birthday: Birthday, year: int) -> date:
    """Date of the birthday in the given year (29 Feb falls on 1 Mar in non-leap years)."""
    if birthday.month == 2 and birthday.day == 29 and not _is_leap(year):
        return date(year, 3, 1)
    return date(year, birthday.month, birthday.day)


def next_occurrence(birthday: Birthday, today: date) -> date | None:
    """First birthday on or after ``today`` (the birth date itself does not count)."""
    last_year = max(today.year, birthday.year or 0) + 1
    for year in range(today.year, last_year + 1):
        occurrence = occurrence_in_year(birthday, year)
        if birthday.year is not None and occurrence.year <= birthday.year:
            continue
        if occurrence >= today:
            return occurrence
    return None


def occurrences_between(birthday: Birthday, start: date, end: date) -> Iterator[date]:
    """Birthdays with ``start <= date < end``."""
    for year in range(start.year, end.year + 1):
        occurrence = occurrence_in_year(birthday, year)
        if birthday.year is not None and occurrence.year <= birthday.year:
            continue
        if start <= occurrence < end:
            yield occurrence


def event_title(birthday: Birthday, occurrence: date) -> str:
    """Calendar title, e.g. "🎂 Erika Mustermann (35)"; no age without a birth year."""
    if birthday.year is None:
        return f"🎂 {birthday.name}"
    return f"🎂 {birthday.name} ({occurrence.year - birthday.year})"
