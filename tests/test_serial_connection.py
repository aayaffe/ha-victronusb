"""Tests for serial reconnect, watchdog, and shutdown behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import sys
from types import ModuleType
import unittest

from tests.module_loader import load_integration_module
from tests.test_vedirect import make_frame


class FakeSerialError(Exception):
    """A deterministic serial transport failure."""


class FakeReader:
    """Return scripted reads, then block until cancelled."""

    def __init__(self, *results: bytes | BaseException) -> None:
        self._results = list(results)
        self.read_started = asyncio.Event()

    async def read(self, _size: int) -> bytes:
        self.read_started.set()
        if self._results:
            result = self._results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        await asyncio.Future()
        raise AssertionError("unreachable")


class FakeWriter:
    """Track whether the manager releases its transport."""

    def __init__(self, on_close: Callable[[], None]) -> None:
        self.closed = False
        self.waited_closed = False
        self._on_close = on_close

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._on_close()

    async def wait_closed(self) -> None:
        self.waited_closed = True


class FakeConnector:
    """Open scripted readers and track concurrent live transports."""

    def __init__(self, *readers: FakeReader) -> None:
        self._readers = list(readers)
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.writers: list[FakeWriter] = []

    async def __call__(self, **_kwargs: object) -> tuple[FakeReader, FakeWriter]:
        self.calls += 1
        if not self._readers:
            raise FakeSerialError("no scripted connection")
        reader = self._readers.pop(0)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        writer = FakeWriter(self._closed)
        self.writers.append(writer)
        return reader, writer

    def _closed(self) -> None:
        self.active -= 1


class SerialConnectionManagerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise lifecycle behavior without Home Assistant or real hardware."""

    def setUp(self) -> None:
        module = load_integration_module("serial_connection")
        self.manager_type = module.SerialConnectionManager

    def make_manager(
        self,
        connector: FakeConnector,
        on_frame: Callable[[tuple[tuple[str, str], ...]], Awaitable[None]]
        | None = None,
        availability: list[bool] | None = None,
        *,
        silence_timeout: float = 0.1,
    ):
        async def ignore_frame(_records: tuple[tuple[str, str], ...]) -> None:
            return None

        return self.manager_type(
            device="/dev/serial/by-id/fake",
            connector=connector,
            connection_kwargs={},
            on_frame=on_frame or ignore_frame,
            on_availability=(
                availability.append if availability is not None else lambda _value: None
            ),
            reconnect_initial=0.001,
            reconnect_max=0.002,
            silence_timeout=silence_timeout,
            availability_check_interval=0.005,
        )

    async def test_eof_reconnects_and_processes_next_valid_frame(self) -> None:
        """Treating b'' as an ordinary read breaks this test."""
        received = asyncio.Event()
        records_seen: list[tuple[tuple[str, str], ...]] = []

        async def on_frame(records: tuple[tuple[str, str], ...]) -> None:
            records_seen.append(records)
            received.set()

        connector = FakeConnector(
            FakeReader(b""),
            FakeReader(make_frame(((b"V", b"13044"),))),
        )
        manager = self.make_manager(connector, on_frame)

        await manager.async_start()
        await asyncio.wait_for(received.wait(), 1)
        await manager.stop()

        self.assertEqual(2, connector.calls)
        self.assertEqual([(("V", "13044"),)], records_seen)
        self.assertTrue(all(writer.closed for writer in connector.writers))

    async def test_serial_exception_reconnects(self) -> None:
        """Catching only failures from connect, not read, breaks this test."""
        received = asyncio.Event()

        async def on_frame(_records: tuple[tuple[str, str], ...]) -> None:
            received.set()

        connector = FakeConnector(
            FakeReader(FakeSerialError("USB disconnected")),
            FakeReader(make_frame()),
        )
        manager = self.make_manager(connector, on_frame)

        await manager.async_start()
        await asyncio.wait_for(received.wait(), 1)
        await manager.stop()

        self.assertEqual(2, connector.calls)

    async def test_silence_reconnects_and_restores_availability(self) -> None:
        """A connected but silent transport must not strand the reader."""
        received = asyncio.Event()
        availability: list[bool] = []

        async def on_frame(_records: tuple[tuple[str, str], ...]) -> None:
            received.set()

        connector = FakeConnector(
            FakeReader(),
            FakeReader(make_frame()),
        )
        manager = self.make_manager(
            connector,
            on_frame,
            availability,
            silence_timeout=0.02,
        )

        await manager.async_start()
        await asyncio.wait_for(received.wait(), 1)
        await manager.stop()

        self.assertEqual(2, connector.calls)
        self.assertIn(True, availability)

    async def test_stop_cancels_tasks_and_closes_transport(self) -> None:
        """Cancelling without awaiting or closing the writer breaks this test."""
        reader = FakeReader()
        connector = FakeConnector(reader)
        manager = self.make_manager(connector)

        await manager.async_start()
        await asyncio.wait_for(reader.read_started.wait(), 1)
        reader_task = manager.reader_task
        availability_task = manager.availability_task
        writer = manager.writer
        await manager.stop()

        self.assertIsNotNone(reader_task)
        self.assertIsNotNone(availability_task)
        self.assertTrue(reader_task.done())
        self.assertTrue(availability_task.done())
        self.assertTrue(writer.closed)
        self.assertTrue(writer.waited_closed)
        self.assertIsNone(manager.reader_task)
        self.assertIsNone(manager.availability_task)
        self.assertIsNone(manager.writer)

    async def test_reload_has_exactly_one_active_reader(self) -> None:
        """Starting a replacement before old cleanup completes breaks this test."""
        first_reader = FakeReader()
        second_reader = FakeReader()
        connector = FakeConnector(first_reader, second_reader)
        first = self.make_manager(connector)
        second = self.make_manager(connector)

        await first.async_start()
        await asyncio.wait_for(first_reader.read_started.wait(), 1)
        await first.stop()
        await second.async_start()
        await asyncio.wait_for(second_reader.read_started.wait(), 1)
        await second.stop()

        self.assertEqual(2, connector.calls)
        self.assertEqual(1, connector.max_active)
        self.assertEqual(0, connector.active)

    async def test_entity_unload_awaits_manager_and_closes_transport(self) -> None:
        """Removing the HA entity must invoke the manager's complete cleanup."""
        sensor_base = type(
            "SensorEntity",
            (),
            {
                "hass": None,
                "async_will_remove_from_hass": _async_noop,
                "async_added_to_hass": _async_noop,
                "async_write_ha_state": lambda self: None,
            },
        )
        sensor_module = ModuleType("homeassistant.components.sensor")
        sensor_module.SensorEntity = sensor_base
        sensor_module.SensorStateClass = type(
            "SensorStateClass", (), {"MEASUREMENT": "measurement"}
        )
        components = ModuleType("homeassistant.components")
        components.sensor = sensor_module
        const = ModuleType("homeassistant.const")
        const.CONF_NAME = "name"
        core = ModuleType("homeassistant.core")
        core.HomeAssistant = object
        sys.modules["homeassistant.components"] = components
        sys.modules["homeassistant.components.sensor"] = sensor_module
        sys.modules["homeassistant.const"] = const
        sys.modules["homeassistant.core"] = core

        serial_asyncio = ModuleType("serial_asyncio")
        serial_asyncio.open_serial_connection = lambda **_kwargs: None
        serial = ModuleType("serial")
        serial.EIGHTBITS = 8
        serial.PARITY_NONE = "N"
        serial.STOPBITS_ONE = 1
        sys.modules["serial_asyncio"] = serial_asyncio
        sys.modules["serial"] = serial
        sys.modules["custom_components.victronusb"].DOMAIN = "victronusb"

        sensor_integration = load_integration_module("sensor")
        entity = sensor_integration.SerialSensor(
            name="Battery",
            port="/dev/serial/by-id/fake",
            baudrate=19200,
            metadata={},
            async_add_entities=lambda _entities: None,
        )
        reader = FakeReader()
        connector = FakeConnector(reader)
        manager = self.make_manager(connector)
        entity._manager = manager

        await manager.async_start()
        await asyncio.wait_for(reader.read_started.wait(), 1)
        writer = manager.writer
        await entity.async_will_remove_from_hass()

        self.assertTrue(writer.closed)
        self.assertIsNone(manager.reader_task)
        self.assertIsNone(manager.availability_task)

    async def test_hub_availability_writes_state_after_entity_is_added(self) -> None:
        """Using the obsolete private hass attribute suppresses this write."""
        sensor_base = type(
            "SensorEntity",
            (),
            {
                "hass": None,
                "async_will_remove_from_hass": _async_noop,
                "async_added_to_hass": _async_noop,
                "async_write_ha_state": lambda self: None,
            },
        )
        homeassistant = ModuleType("homeassistant")
        sensor_module = ModuleType("homeassistant.components.sensor")
        sensor_module.SensorEntity = sensor_base
        sensor_module.SensorStateClass = type(
            "SensorStateClass", (), {"MEASUREMENT": "measurement"}
        )
        components = ModuleType("homeassistant.components")
        components.sensor = sensor_module
        const = ModuleType("homeassistant.const")
        const.CONF_NAME = "name"
        core = ModuleType("homeassistant.core")
        core.HomeAssistant = object
        homeassistant.components = components
        sys.modules["homeassistant"] = homeassistant
        sys.modules["homeassistant.components"] = components
        sys.modules["homeassistant.components.sensor"] = sensor_module
        sys.modules["homeassistant.const"] = const
        sys.modules["homeassistant.core"] = core

        serial_asyncio = ModuleType("serial_asyncio")
        serial_asyncio.open_serial_connection = lambda **_kwargs: None
        serial = ModuleType("serial")
        serial.EIGHTBITS = 8
        serial.PARITY_NONE = "N"
        serial.STOPBITS_ONE = 1
        sys.modules["serial_asyncio"] = serial_asyncio
        sys.modules["serial"] = serial
        sys.modules["custom_components.victronusb"].DOMAIN = "victronusb"

        sensor_integration = load_integration_module("sensor")
        entity = sensor_integration.SerialSensor(
            name="Battery",
            port="/dev/serial/by-id/fake",
            baudrate=19200,
            metadata={},
            async_add_entities=lambda _entities: None,
        )
        writes: list[None] = []
        entity.hass = object()
        entity.async_write_ha_state = lambda: writes.append(None)

        entity._set_connection_availability(True)

        self.assertEqual([None], writes)

    async def test_async_start_awaits_previous_watchdog_before_replacing_it(
        self,
    ) -> None:
        """Replacing a watchdog before cancellation completes breaks this test."""
        first_reader = FakeReader()
        second_reader = FakeReader()
        connector = FakeConnector(first_reader, second_reader)
        manager = self.make_manager(connector)

        await manager.async_start()
        await asyncio.wait_for(first_reader.read_started.wait(), 1)
        old_watchdog = manager.availability_task
        manager.reader_task.cancel()
        await asyncio.gather(manager.reader_task, return_exceptions=True)

        await manager.async_start()

        self.assertTrue(old_watchdog.done())
        self.assertIsNot(old_watchdog, manager.availability_task)
        await manager.stop()


async def _async_noop(_self) -> None:
    """Stand in for Home Assistant entity lifecycle hooks."""


if __name__ == "__main__":
    unittest.main()
