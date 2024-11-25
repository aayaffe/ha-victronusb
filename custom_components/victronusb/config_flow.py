import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import logging

_LOGGER = logging.getLogger(__name__)

class Smart0183SERIALConfigFlow(config_entries.ConfigFlow, domain="victronusb"):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        _LOGGER.debug("async_step_user called with user_input: %s", user_input)
        errors = {}
        
        if user_input is not None:
            # Check if name already exists
            existing_names = {entry.data.get("name") for entry in self._async_current_entries()}
            _LOGGER.debug("Existing names in the integration: %s", existing_names) 
            
            if user_input["name"] in existing_names:
                
                _LOGGER.debug("Name exists error") 

                errors["name"] = "name_exists"
            else:
                _LOGGER.debug("User input is not None, creating entry with name: %s", user_input.get('name'))
                return self.async_create_entry(title=user_input.get('name'), data=user_input)

        if not errors:
            _LOGGER.debug("No user input or errors, showing form")
            
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("name"): str,  
                vol.Required("serial_port", default="/dev/ttyUSB0"): str,
                vol.Required("baudrate", default=4800): int,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        _LOGGER.debug("Getting options flow handler")
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        _LOGGER.debug("Initializing OptionsFlowHandler with config_entry: %s", config_entry)
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        _LOGGER.debug("OptionsFlowHandler.async_step_init called with user_input: %s", user_input)
        if user_input is not None:
            _LOGGER.debug("User input is not None, updating options")
            # Update the config entry with new options
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input}
            )

            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data=user_input)

        # Use current values as defaults
        serial_port = self.config_entry.data.get("serial_port")
        baudrate = self.config_entry.data.get("baudrate")

        _LOGGER.debug("Showing options form with serial_port: %s and baudrate: %s", serial_port, baudrate)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("serial_port", default=serial_port): str,
                vol.Required("baudrate", default=baudrate): int,
            }),
        )
