"""Calendar platform: all contact birthdays as yearly all-day events."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import CardDavConfigEntry
from .birthdays import event_title, next_occurrence, occurrences_between
from .coordinator import BirthdayCoordinator
from .vcard import Birthday


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CardDavConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the birthday calendar."""
    async_add_entities([BirthdayCalendar(entry.runtime_data, entry.entry_id)])


def _all_day_event(birthday: Birthday, occurrence: date) -> CalendarEvent:
    return CalendarEvent(
        start=occurrence,
        end=occurrence + timedelta(days=1),  # all-day events end exclusively
        summary=event_title(birthday, occurrence),
    )


class BirthdayCalendar(CoordinatorEntity[BirthdayCoordinator], CalendarEntity):
    """One calendar holding every birthday found in the CardDAV addressbook(s)."""

    _attr_has_entity_name = True
    _attr_translation_key = "birthdays"
    _attr_icon = "mdi:cake-variant"

    def __init__(self, coordinator: BirthdayCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entry_id

    @property
    def event(self) -> CalendarEvent | None:
        """The next birthday (today's counts), used for the entity state."""
        today = dt_util.now().date()
        soonest: tuple[date, Birthday] | None = None
        for birthday in self.coordinator.data:
            occurrence = next_occurrence(birthday, today)
            if occurrence is not None and (soonest is None or occurrence < soonest[0]):
                soonest = (occurrence, birthday)
        return _all_day_event(soonest[1], soonest[0]) if soonest else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return birthdays overlapping [start_date, end_date)."""
        start = dt_util.as_local(start_date).date()
        end_local = dt_util.as_local(end_date)
        # An all-day event on the end date overlaps unless the range ends at its midnight.
        end = end_local.date()
        if end_local.time() != time(0):
            end += timedelta(days=1)

        events = [
            _all_day_event(birthday, occurrence)
            for birthday in self.coordinator.data
            for occurrence in occurrences_between(birthday, start, end)
        ]
        events.sort(key=lambda e: (e.start, e.summary))
        return events
