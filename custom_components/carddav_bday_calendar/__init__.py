"""CardDAV Birthday Calendar: contact birthdays as one Home Assistant calendar."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .carddav import CardDavClient
from .const import CONF_ADDRESSBOOKS
from .coordinator import BirthdayCoordinator

PLATFORMS: list[Platform] = [Platform.CALENDAR]

type CardDavConfigEntry = ConfigEntry[BirthdayCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CardDavConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_SSL])
    client = CardDavClient(
        session,
        entry.data[CONF_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = BirthdayCoordinator(
        hass, entry, client, entry.data[CONF_ADDRESSBOOKS]
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CardDavConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
