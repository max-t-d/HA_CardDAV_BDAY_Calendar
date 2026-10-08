"""Date logic."""

from datetime import date

from custom_components.carddav_bday_calendar.birthdays import (
    event_title,
    next_occurrence,
    occurrence_in_year,
    occurrences_between,
)
from custom_components.carddav_bday_calendar.vcard import Birthday


def _b(month, day, year=None, name="Max"):
    return Birthday(uid=name, name=name, month=month, day=day, year=year)


def test_next_occurrence_today_counts():
    assert next_occurrence(_b(10, 8, 1990), date(2026, 10, 8)) == date(2026, 10, 8)


def test_next_occurrence_rolls_into_next_year():
    assert next_occurrence(_b(10, 7, 1990), date(2026, 10, 8)) == date(2027, 10, 7)


def test_birth_date_itself_is_not_a_birthday():
    # Born today: the first birthday is next year.
    assert next_occurrence(_b(10, 8, 2026), date(2026, 10, 8)) == date(2027, 10, 8)


def test_leap_day():
    leap_baby = _b(2, 29, 1992)
    assert occurrence_in_year(leap_baby, 2027) == date(2027, 3, 1)
    assert occurrence_in_year(leap_baby, 2028) == date(2028, 2, 29)
    assert next_occurrence(leap_baby, date(2027, 1, 1)) == date(2027, 3, 1)
    assert occurrence_in_year(leap_baby, 2100) == date(2100, 3, 1)  # not a leap year


def test_occurrences_between_spans_years_and_end_is_exclusive():
    b = _b(1, 1, 1990)
    assert list(occurrences_between(b, date(2026, 12, 1), date(2028, 1, 1))) == [
        date(2027, 1, 1)
    ]
    assert list(occurrences_between(b, date(2026, 12, 1), date(2028, 1, 2))) == [
        date(2027, 1, 1),
        date(2028, 1, 1),
    ]


def test_occurrences_skip_years_up_to_birth_year():
    assert list(occurrences_between(_b(6, 1, 2020), date(2019, 1, 1), date(2022, 1, 1))) == [
        date(2021, 6, 1)
    ]


def test_title():
    assert event_title(_b(10, 9, 1990, "Erika"), date(2026, 10, 9)) == "🎂 Erika (36)"
    assert event_title(_b(10, 9, None, "Erika"), date(2026, 10, 9)) == "🎂 Erika"
