"""Data update coordinator for the CardDAV Birthday Calendar integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .carddav import CardDavAuthError, CardDavClient, CardDavError
from .const import DOMAIN, SCAN_INTERVAL
from .vcard import Birthday

_LOGGER = logging.getLogger(__name__)


class BirthdayCoordinator(DataUpdateCoordinator[list[Birthday]]):
    """Fetches all birthdays from the selected addressbooks."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CardDavClient,
        addressbooks: list[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self._client = client
        self._addressbooks = addressbooks

    async def _async_update_data(self) -> list[Birthday]:
        birthdays: dict[str, Birthday] = {}
        try:
            for url in self._addressbooks:
                for birthday in await self._client.fetch_birthdays(url):
                    birthdays.setdefault(birthday.uid, birthday)
        except CardDavAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CardDavError as err:
            raise UpdateFailed(f"Error talking to CardDAV server: {err}") from err

        return sorted(
            birthdays.values(), key=lambda b: (b.month, b.day, b.name.casefold())
        )
