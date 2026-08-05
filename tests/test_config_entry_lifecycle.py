"""Tests for Home Assistant config-entry setup, unload, and options flow."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest

from tests.module_loader import INTEGRATION_PATH


class StubConfigFlow:
    """Minimal ConfigFlow base accepted by the integration class."""

    def __init_subclass__(cls, **_kwargs: object) -> None:
        super().__init_subclass__()


class StubOptionsFlow:
    """Minimal OptionsFlow result helpers."""

    def async_create_entry(self, *, title: str, data: dict) -> dict:
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(self, **kwargs: object) -> dict:
        return {"type": "form", **kwargs}


class FakeConfigEntry:
    """Provide the config-entry surface used by the integration."""

    def __init__(self) -> None:
        self.entry_id = "entry-1"
        self.data = {
            "name": "Battery",
            "serial_port": "/dev/ttyUSB0",
            "baudrate": 19200,
        }
        self.options: dict = {}
        self.unload_callbacks: list = []

    def as_dict(self) -> dict:
        return {"entry_id": self.entry_id, "data": self.data}

    def add_update_listener(self, listener):
        return listener

    def async_on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)


class FakeConfigEntries:
    """Record platform and options lifecycle calls."""

    def __init__(self, *, unload_ok: bool = True) -> None:
        self.unload_ok = unload_ok
        self.setup_calls: list[tuple[FakeConfigEntry, list[str]]] = []
        self.unload_calls: list[tuple[FakeConfigEntry, list[str]]] = []
        self.update_calls: list[tuple[FakeConfigEntry, dict]] = []
        self.reload_calls: list[str] = []

    async def async_forward_entry_setups(
        self, entry: FakeConfigEntry, platforms: list[str]
    ) -> None:
        self.setup_calls.append((entry, platforms))

    async def async_unload_platforms(
        self, entry: FakeConfigEntry, platforms: list[str]
    ) -> bool:
        self.unload_calls.append((entry, platforms))
        return self.unload_ok

    def async_update_entry(self, entry: FakeConfigEntry, **changes: dict) -> None:
        self.update_calls.append((entry, changes))

    async def async_reload(self, entry_id: str) -> None:
        self.reload_calls.append(entry_id)


class FakeHass:
    def __init__(self, config_entries: FakeConfigEntries) -> None:
        self.data: dict = {}
        self.config_entries = config_entries


def install_homeassistant_stubs() -> None:
    """Install only the framework names needed to import lifecycle modules."""
    homeassistant = ModuleType("homeassistant")
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = FakeConfigEntry
    config_entries.ConfigFlow = StubConfigFlow
    config_entries.OptionsFlow = StubOptionsFlow
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = FakeHass
    core.callback = lambda function: function
    homeassistant.config_entries = config_entries
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core

    voluptuous = ModuleType("voluptuous")
    voluptuous.Schema = lambda value: value
    voluptuous.Required = lambda key, **_kwargs: key
    sys.modules["voluptuous"] = voluptuous


def load_file(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ConfigEntryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        install_homeassistant_stubs()
        self.integration = load_file(
            "victronusb_init_under_test", INTEGRATION_PATH / "__init__.py"
        )

    async def test_setup_awaits_plural_platform_forwarding(self) -> None:
        """Using deprecated singular forwarding breaks this test."""
        config_entries = FakeConfigEntries()
        hass = FakeHass(config_entries)
        entry = FakeConfigEntry()

        result = await self.integration.async_setup_entry(hass, entry)

        self.assertTrue(result)
        self.assertEqual([(entry, ["sensor"])], config_entries.setup_calls)
        self.assertIsInstance(hass.data["victronusb"][entry.entry_id], dict)

    async def test_successful_unload_removes_runtime_after_platform(self) -> None:
        """Removing runtime before entities finish unloading breaks this test."""
        config_entries = FakeConfigEntries(unload_ok=True)
        hass = FakeHass(config_entries)
        entry = FakeConfigEntry()
        hass.data = {"victronusb": {entry.entry_id: {"sensor": object()}}}

        result = await self.integration.async_unload_entry(hass, entry)

        self.assertTrue(result)
        self.assertEqual([(entry, ["sensor"])], config_entries.unload_calls)
        self.assertNotIn(entry.entry_id, hass.data["victronusb"])

    async def test_failed_platform_unload_retains_runtime(self) -> None:
        """Claiming successful unload after platform failure breaks this test."""
        config_entries = FakeConfigEntries(unload_ok=False)
        hass = FakeHass(config_entries)
        entry = FakeConfigEntry()
        runtime = {"sensor": object()}
        hass.data = {"victronusb": {entry.entry_id: runtime}}

        result = await self.integration.async_unload_entry(hass, entry)

        self.assertFalse(result)
        self.assertIs(runtime, hass.data["victronusb"][entry.entry_id])

    async def test_options_flow_relies_on_one_listener_reload(self) -> None:
        """Updating entry data and explicitly reloading causes reload races."""
        config_flow = load_file(
            "victronusb_config_flow_under_test",
            INTEGRATION_PATH / "config_flow.py",
        )
        entry = FakeConfigEntry()
        flow = config_flow.OptionsFlowHandler(entry)
        config_entries = FakeConfigEntries()
        flow.hass = FakeHass(config_entries)
        new_options = {
            "serial_port": "/dev/serial/by-id/usb-test",
            "baudrate": 19200,
        }

        result = await flow.async_step_init(new_options)

        self.assertEqual(new_options, result["data"])
        self.assertEqual([], config_entries.update_calls)
        self.assertEqual([], config_entries.reload_calls)


if __name__ == "__main__":
    unittest.main()
