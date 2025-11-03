"""Config flow for Lutron QS integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pylutron_integration import connection as lutron_connection
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import DEFAULT_PASSWORD, DEFAULT_USERNAME, DOMAIN

# Note: There is no known method for automatic discovery of Lutron QSE-CI-NWK-E devices.
# These devices do not advertise themselves via mDNS/Zeroconf or any other standard
# discovery protocol. Manual configuration is required.

_LOGGER = logging.getLogger(__name__)


async def validate_connection(host: str, username: str, password: str) -> None:
    """Validate the connection to the Lutron QS access point.

    Raises:
        CannotConnect: If connection fails.
        InvalidAuth: If authentication fails.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 23), timeout=10.0
        )
    except (OSError, TimeoutError) as err:
        raise CannotConnect from err

    try:
        try:
            conn = await asyncio.wait_for(
                lutron_connection.login(
                    reader,
                    writer,
                    username.encode("utf-8"),
                    password.encode("utf-8") if password else None,
                ),
                timeout=10.0,
            )
            # Successfully connected and authenticated, disconnect cleanly
            await conn.disconnect()
        except lutron_connection.LoginError as err:
            raise InvalidAuth from err
        except (OSError, TimeoutError) as err:
            raise CannotConnect from err
    finally:
        # Always close the writer to clean up the connection
        writer.close()
        await writer.wait_closed()


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate authentication failure."""


class LutronQSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lutron QS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Use IP address as unique ID. Note: This is not strictly correct because
            # it's possible to connect multiple hubs to the same QS link, and it's not
            # entirely clear how to tell which hub is which. Using the hub's serial
            # number would be more correct, but we'd need to connect and enumerate
            # devices before we could determine it. Using the IP address provides a
            # reasonable unique identifier for the common single-hub case.
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            # Validate the connection before creating the entry (test-before-configure)
            try:
                await validate_connection(
                    user_input[CONF_HOST],
                    user_input[CONF_USERNAME],
                    user_input.get(CONF_PASSWORD, ""),
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during connection validation")
                errors["base"] = "unknown"

            if not errors:
                return self.async_create_entry(
                    title=f"Lutron QS ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        # Show the configuration form
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration.

        Note: We don't validate credentials here because:
        1. The device doesn't support multiple simultaneous connections
        2. The integration is already connected when reconfiguring
        3. Validation will happen during reload in async_setup_entry
        4. If credentials are invalid, setup will fail with ConfigEntryAuthFailed,
           which automatically triggers the reauth flow
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry

        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                title=f"Lutron QS ({user_input[CONF_HOST]})",
                data=user_input,
            )

        # Show the configuration form with current values
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
                ): str,
                vol.Optional(
                    CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
                ): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth flow when credentials are invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation step."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry

        if user_input is not None:
            # Validate the new credentials (test-before-configure)
            try:
                await validate_connection(
                    entry.data[CONF_HOST],
                    user_input[CONF_USERNAME],
                    user_input.get(CONF_PASSWORD, ""),
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during connection validation")
                errors["base"] = "unknown"

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        CONF_HOST: entry.data[CONF_HOST],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                    },
                )

        # Show the reauth form with current username
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
                ): str,
                vol.Optional(
                    CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
                ): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )
