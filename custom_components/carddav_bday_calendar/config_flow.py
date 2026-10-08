"""Config flow for the CardDAV Birthday Calendar integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .carddav import (
    AddressBook,
    CardDavAuthError,
    CardDavClient,
    CardDavError,
)
from .const import CONF_ADDRESSBOOKS, DEFAULT_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default=DEFAULT_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)


class CardDavBirthdayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CardDAV Birthday Calendar."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._addressbooks: list[AddressBook] = []

    async def _discover(self, data: Mapping[str, Any]) -> list[AddressBook]:
        client = CardDavClient(
            async_get_clientsession(self.hass, verify_ssl=data[CONF_VERIFY_SSL]),
            data[CONF_URL],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
        )
        return await client.discover_addressbooks()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for server, user and password, then discover addressbooks."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                books = await self._discover(user_input)
            except CardDavAuthError:
                errors["base"] = "invalid_auth"
            except CardDavError as err:
                _LOGGER.debug("CardDAV discovery failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during CardDAV discovery")
                errors["base"] = "unknown"
            else:
                if not books:
                    errors["base"] = "no_addressbooks"
                else:
                    host = urlsplit(user_input[CONF_URL]).netloc
                    await self.async_set_unique_id(
                        f"{user_input[CONF_USERNAME].lower()}@{host}"
                    )
                    self._abort_if_unique_id_configured()
                    self._data = user_input
                    self._addressbooks = books
                    if len(books) == 1:
                        return self._create_entry([books[0].url])
                    return await self.async_step_addressbooks()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            description_placeholders={"icloud_url": DEFAULT_URL},
            errors=errors,
        )

    async def async_step_addressbooks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose which addressbooks to read (only if there are several)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_ADDRESSBOOKS]:
                return self._create_entry(user_input[CONF_ADDRESSBOOKS])
            errors["base"] = "no_addressbook_selected"

        options = [SelectOptionDict(value=b.url, label=b.name) for b in self._addressbooks]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDRESSBOOKS, default=[o["value"] for o in options]
                ): SelectSelector(SelectSelectorConfig(options=options, multiple=True))
            }
        )
        return self.async_show_form(
            step_id="addressbooks", data_schema=schema, errors=errors
        )

    def _create_entry(self, addressbook_urls: list[str]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._data[CONF_USERNAME],
            data={**self._data, CONF_ADDRESSBOOKS: addressbook_urls},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication, e.g. after an app-specific password was revoked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._discover({**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]})
            except CardDavAuthError:
                errors["base"] = "invalid_auth"
            except CardDavError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
            errors=errors,
        )
