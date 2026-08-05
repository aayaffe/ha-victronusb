"""Sensor platform for Victron VE.Direct USB devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import json
import logging
import math
from pathlib import Path
from typing import Any

import serial_asyncio
from serial import EIGHTBITS, PARITY_NONE, STOPBITS_ONE

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .serial_connection import SerialConnectionManager

CONF_BAUDRATE = "baudrate"
CONF_SERIAL_PORT = "serial_port"

DEFAULT_BAUDRATE = 19200
FRAME_SILENCE_TIMEOUT = 30.0
MIN_UPDATE_INTERVAL = 5.0

_LOGGER = logging.getLogger(__name__)

SensorMetadata = dict[str, dict[str, str | None]]


def load_smart_data(json_path: Path) -> SensorMetadata:
    """Load and flatten the bundled VE.Direct field metadata."""
    with json_path.open(encoding="utf-8") as file:
        smart_data = json.load(file)

    result: SensorMetadata = {}
    for sentence in smart_data:
        group = sentence["group"]
        for field in sentence["fields"]:
            result[field["unique_id"]] = {
                "full_description": field["full_description"],
                "group": group,
                "unit_of_measurement": field.get("unit_of_measurement"),
            }
    return result


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: Callable[..., None],
) -> None:
    """Set up one entry-scoped serial hub entity."""
    config = {**entry.data, **entry.options}
    name = config[CONF_NAME]
    serial_port = config[CONF_SERIAL_PORT]
    baudrate = config.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)

    metadata_path = Path(__file__).with_name("Victronusb.json")
    try:
        metadata = await hass.async_add_executor_job(load_smart_data, metadata_path)
    except (OSError, ValueError, KeyError, TypeError):
        _LOGGER.exception("Unable to load VE.Direct sensor metadata")
        raise

    sensor = SerialSensor(
        name=name,
        port=serial_port,
        baudrate=baudrate,
        metadata=metadata,
        async_add_entities=async_add_entities,
    )
    hass.data[DOMAIN][entry.entry_id]["sensor"] = sensor
    _LOGGER.info("Configuring %s on %s at %s baud", name, serial_port, baudrate)
    async_add_entities([sensor], True)


class SmartSensor(SensorEntity):
    """A dynamic sensor backed by a validated VE.Direct field."""

    _attr_should_poll = False

    def __init__(
        self,
        name: str,
        friendly_name: str,
        initial_state: str,
        group: str | None = None,
        unit_of_measurement: str | None = None,
        device_name: str | None = None,
        sentence_type: str | None = None,
    ) -> None:
        """Initialize a dynamic sensor without changing legacy identifiers."""
        self._unique_id = name.lower().replace(" ", "_")
        self.entity_id = f"sensor.{self._unique_id}"
        self._name = friendly_name or self._unique_id
        self._state = initial_state
        self._group = group or "Other"
        self._device_name = device_name
        self._sentence_type = sentence_type
        self._unit_of_measurement = unit_of_measurement
        self._last_updated = datetime.now()
        self._available = bool(initial_state)

    @property
    def name(self) -> str:
        """Return the friendly name."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the legacy unique ID."""
        return self._unique_id

    @property
    def native_value(self) -> str:
        """Return the latest field value."""
        return self._state

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the configured native unit."""
        return self._unit_of_measurement

    @property
    def unit_of_measurement(self) -> str | None:
        """Return the unit for compatibility with older Home Assistant."""
        return self._unit_of_measurement

    @property
    def device_info(self) -> dict[str, Any]:
        """Return the existing device grouping."""
        return {
            "identifiers": {(DOMAIN, self._device_name)},
            "name": self._device_name,
            "manufacturer": self._group,
            "model": self._sentence_type,
        }

    @property
    def state_class(self) -> SensorStateClass | None:
        """Return a state class only when Home Assistant can store a number."""
        try:
            return (
                SensorStateClass.MEASUREMENT
                if math.isfinite(float(self._state))
                else None
            )
        except (TypeError, ValueError):
            return None

    @property
    def last_updated(self) -> datetime:
        """Return the most recent field update time."""
        return self._last_updated

    @property
    def available(self) -> bool:
        """Return whether validated frames are arriving."""
        return self._available

    def set_state(self, new_state: str) -> None:
        """Store a field value and restore entity availability."""
        self._state = new_state
        self._last_updated = datetime.now()
        self._available = bool(new_state)
        self._write_state()

    def set_available(self, available: bool) -> None:
        """Apply connection availability without discarding the last value."""
        if self._available == available:
            return
        self._available = available
        self._write_state()

    def _write_state(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class SerialSensor(SensorEntity):
    """Hub entity adapting validated serial frames to dynamic sensors."""

    _attr_should_poll = False

    def __init__(
        self,
        *,
        name: str,
        port: str,
        baudrate: int,
        metadata: SensorMetadata,
        async_add_entities: Callable[..., None],
    ) -> None:
        """Initialize the serial entity and its owned manager."""
        self._name = name
        self._attributes: dict[str, Any] | None = None
        self._available = False
        self._metadata = metadata
        self._async_add_entities = async_add_entities
        self._created_sensors: dict[str, SmartSensor] = {}
        self._last_processed: dict[str, float] = {}
        self._manager = SerialConnectionManager(
            device=port,
            connector=serial_asyncio.open_serial_connection,
            connection_kwargs={
                "url": port,
                "baudrate": baudrate,
                "bytesize": EIGHTBITS,
                "parity": PARITY_NONE,
                "stopbits": STOPBITS_ONE,
                "xonxoff": False,
                "rtscts": False,
                "dsrdtr": False,
            },
            on_frame=self._handle_frame,
            on_availability=self._set_connection_availability,
            silence_timeout=FRAME_SILENCE_TIMEOUT,
        )

    async def async_added_to_hass(self) -> None:
        """Start owned background tasks after Home Assistant adds the entity."""
        await super().async_added_to_hass()
        await self._manager.async_start()

    async def async_will_remove_from_hass(self) -> None:
        """Await task cancellation and transport closure during unload."""
        await self._manager.stop()
        await super().async_will_remove_from_hass()

    async def _handle_frame(self, records: tuple[tuple[str, str], ...]) -> None:
        """Create or update sensors from one validated frame."""
        now = asyncio.get_running_loop().time()
        for field_label, field_data in records:
            last_processed = self._last_processed.get(field_label)
            if (
                last_processed is not None
                and now - last_processed < MIN_UPDATE_INTERVAL
            ):
                continue
            self._process_record(field_label, field_data)
            self._last_processed[field_label] = now

    def _process_record(self, field_label: str, field_data: str) -> None:
        sensor_info = self._metadata.get(field_label)
        if sensor_info is None:
            _LOGGER.debug("Ignoring undefined VE.Direct field %s", field_label)
            return

        sensor = self._created_sensors.get(field_label)
        if sensor is not None:
            sensor.set_state(field_data)
            return

        full_description = sensor_info.get("full_description") or field_label
        group = sensor_info.get("group")
        unit = sensor_info.get("unit_of_measurement")
        sensor = SmartSensor(
            field_label,
            full_description,
            field_data,
            group,
            unit,
            full_description,
            field_label,
        )
        self._created_sensors[field_label] = sensor
        self._async_add_entities([sensor])

    def _set_connection_availability(self, available: bool) -> None:
        self._available = available
        for sensor in self._created_sensors.values():
            sensor.set_available(available)
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def name(self) -> str:
        """Return the configured hub name."""
        return self._name

    @property
    def available(self) -> bool:
        """Return whether validated frames are arriving."""
        return self._available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the legacy attributes value."""
        return self._attributes

    @property
    def native_value(self) -> None:
        """The hub has no measurement value."""
        return None
