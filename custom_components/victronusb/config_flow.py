"""Config flow for the Victron USB integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

_LOGGER = logging.getLogger(__name__)


class Smart0183SERIALConfigFlow(config_entries.ConfigFlow, domain="victronusb"):
    """Configure a Victron VE.Direct serial connection."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle initial configuration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            existing_names = {
                entry.data.get("name") for entry in self._async_current_entries()
            }
            if user_input["name"] in existing_names:
                errors["name"] = "name_exists"
            else:
                return self.async_create_entry(
                    title=user_input["name"], data=user_input
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("serial_port", default="/dev/serial/by-id/"): str,
                    vol.Required("baudrate", default=19200): int,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Update the serial port and baud rate."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Retain compatibility without assigning deprecated config_entry."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manage serial options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {
            **self._config_entry.data,
            **self._config_entry.options,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "serial_port", default=current.get("serial_port")
                    ): str,
                    vol.Required(
                        "baudrate", default=current.get("baudrate", 19200)
                    ): int,
                }
            ),
        )
