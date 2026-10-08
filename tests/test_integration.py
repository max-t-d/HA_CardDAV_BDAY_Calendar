"""Config flow, reauth and the calendar entity inside a real Home Assistant core."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.entity_component import async_update_entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carddav_bday_calendar.carddav import (
    AddressBook,
    CardDavAuthError,
    CardDavConnectionError,
)
from custom_components.carddav_bday_calendar.const import CONF_ADDRESSBOOKS, DOMAIN
from custom_components.carddav_bday_calendar.vcard import Birthday

CLIENT = "custom_components.carddav_bday_calendar.carddav.CardDavClient"
BOOK = AddressBook("https://c.example/book/", "Kontakte")
USER_INPUT = {
    CONF_URL: "https://contacts.icloud.com",
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "app-password",
    CONF_VERIFY_SSL: True,
}
BIRTHDAYS = [
    Birthday("1", "Max", 10, 8, None),
    Birthday("2", "Erika", 10, 9, 1990),
    Birthday("3", "Schaltjahr", 2, 29, 1992),
]


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="user@example.com@contacts.icloud.com",
        data={**USER_INPUT, CONF_ADDRESSBOOKS: [BOOK.url]},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.fetch_birthdays", return_value=BIRTHDAYS):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


# --- config flow ---------------------------------------------------------


async def test_flow_single_addressbook_creates_entry(hass: HomeAssistant) -> None:
    with patch(f"{CLIENT}.discover_addressbooks", return_value=[BOOK]), patch(
        f"{CLIENT}.fetch_birthdays", return_value=[]
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"][CONF_ADDRESSBOOKS] == [BOOK.url]


async def test_flow_multiple_addressbooks_asks_which(hass: HomeAssistant) -> None:
    other = AddressBook("https://c.example/work/", "Arbeit")
    with patch(f"{CLIENT}.discover_addressbooks", return_value=[BOOK, other]), patch(
        f"{CLIENT}.fetch_birthdays", return_value=[]
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["step_id"] == "addressbooks"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESSBOOKS: [other.url]}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESSBOOKS] == [other.url]


async def test_flow_errors_and_recovery(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    for side_effect, expected in (
        (CardDavAuthError("no"), "invalid_auth"),
        (CardDavConnectionError("down"), "cannot_connect"),
        (RuntimeError("bug"), "unknown"),
    ):
        with patch(f"{CLIENT}.discover_addressbooks", side_effect=side_effect):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], USER_INPUT
            )
        assert result["errors"] == {"base": expected}

    with patch(f"{CLIENT}.discover_addressbooks", return_value=[]):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "no_addressbooks"}

    with patch(f"{CLIENT}.discover_addressbooks", return_value=[BOOK]), patch(
        f"{CLIENT}.fetch_birthdays", return_value=[]
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_flow_aborts_for_same_account(hass: HomeAssistant) -> None:
    _entry().add_to_hass(hass)
    with patch(f"{CLIENT}.discover_addressbooks", return_value=[BOOK]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- reauth --------------------------------------------------------------


async def test_revoked_password_starts_reauth_and_new_password_is_stored(
    hass: HomeAssistant,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.fetch_birthdays", side_effect=CardDavAuthError("revoked")):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]

    with patch(f"{CLIENT}.discover_addressbooks", return_value=[BOOK]), patch(
        f"{CLIENT}.fetch_birthdays", return_value=BIRTHDAYS
    ):
        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.state is ConfigEntryState.LOADED


async def test_server_down_retries_setup(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.fetch_birthdays", side_effect=CardDavConnectionError("down")):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


# --- calendar entity -----------------------------------------------------


async def test_state_on_on_a_birthday_and_next_event_attributes(
    hass: HomeAssistant, freezer
) -> None:
    freezer.move_to("2026-10-08 12:00:00+02:00")
    await _setup(hass, _entry())

    state = hass.states.get("calendar.birthdays")
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["message"] == "🎂 Max"
    assert state.attributes["all_day"] is True


async def test_state_off_between_birthdays_and_shows_next_with_age(
    hass: HomeAssistant, freezer
) -> None:
    freezer.move_to("2026-10-08 12:00:00+02:00")
    # Keep the patch active: moving time forward also triggers the 6-hourly refresh.
    with patch(f"{CLIENT}.fetch_birthdays", return_value=BIRTHDAYS):
        await _setup(hass, _entry())
        # Two days later Max's and Erika's birthdays are over; the leap-day person is next.
        freezer.move_to("2026-10-10 12:00:00+02:00")
        await async_update_entity(hass, "calendar.birthdays")
    state = hass.states.get("calendar.birthdays")
    assert state.state == STATE_OFF
    assert state.attributes["message"] == "🎂 Schaltjahr (35)"  # 1 Mar 2027 is the next one


async def test_get_events_service(hass: HomeAssistant, freezer) -> None:
    freezer.move_to("2026-10-08 12:00:00+02:00")
    await _setup(hass, _entry())

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.birthdays",
            "start_date_time": "2026-10-01 00:00:00",
            "end_date_time": "2026-10-31 00:00:00",
        },
        blocking=True,
        return_response=True,
    )
    events = response["calendar.birthdays"]["events"]
    assert [(e["start"], e["end"], e["summary"]) for e in events] == [
        ("2026-10-08", "2026-10-09", "🎂 Max"),
        ("2026-10-09", "2026-10-10", "🎂 Erika (36)"),
    ]

    # A range ending at 09:00 on the 9th still includes that day's all-day event.
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.birthdays",
            "start_date_time": "2026-10-09 08:00:00",
            "end_date_time": "2026-10-09 09:00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert [e["summary"] for e in response["calendar.birthdays"]["events"]] == ["🎂 Erika (36)"]


async def test_leap_day_person_in_march_of_non_leap_year(hass: HomeAssistant, freezer) -> None:
    freezer.move_to("2026-10-08 12:00:00+02:00")
    await _setup(hass, _entry())
    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.birthdays",
            "start_date_time": "2027-02-01 00:00:00",
            "end_date_time": "2027-04-01 00:00:00",
        },
        blocking=True,
        return_response=True,
    )
    events = response["calendar.birthdays"]["events"]
    assert [(e["start"], e["summary"]) for e in events] == [("2027-03-01", "🎂 Schaltjahr (35)")]
