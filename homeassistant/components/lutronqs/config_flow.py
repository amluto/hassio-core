"""Config flow for Lutron QS integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import DEFAULT_PASSWORD, DEFAULT_USERNAME, DOMAIN

# Note: There is no known method for automatic discovery of Lutron QSE-CI-NWK-E devices.
# These devices do not advertise themselves via mDNS/Zeroconf or any other standard
# discovery protocol. Manual configuration is required.


class LutronQSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lutron QS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check for duplicate entries based on host
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            # For now, we're not actually connecting to the device
            # TODO: Add connection validation when device connection is implemented
            # try:
            #     await validate_connection(user_input[CONF_HOST], ...)
            # except CannotConnect:
            #     errors["base"] = "cannot_connect"
            # except InvalidAuth:
            #     errors["base"] = "invalid_auth"
            # except Exception:
            #     errors["base"] = "unknown"

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
