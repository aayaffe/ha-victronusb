"""Owned asynchronous lifecycle for a VE.Direct serial connection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any, Protocol

from .vedirect import VEDirectParser

_LOGGER = logging.getLogger(__name__)


class _Reader(Protocol):
    async def read(self, size: int) -> bytes:
        """Read up to size bytes."""


class _Writer(Protocol):
    def close(self) -> None:
        """Close the underlying transport."""


class _ConnectionLost(Exception):
    """Signal a recoverable EOF or valid-frame timeout."""


class SerialConnectionManager:
    """Own serial tasks, reconnect policy, parser, and transport cleanup."""

    def __init__(
        self,
        *,
        device: str,
        connector: Callable[..., Awaitable[tuple[_Reader, _Writer]]],
        connection_kwargs: Mapping[str, Any],
        on_frame: Callable[[tuple[tuple[str, str], ...]], Awaitable[None]],
        on_availability: Callable[[bool], None],
        reconnect_initial: float = 1.0,
        reconnect_max: float = 30.0,
        silence_timeout: float = 30.0,
        availability_check_interval: float = 5.0,
    ) -> None:
        """Initialize a stopped manager."""
        self._device = device
        self._connector = connector
        self._connection_kwargs = dict(connection_kwargs)
        self._on_frame = on_frame
        self._on_availability = on_availability
        self._reconnect_initial = reconnect_initial
        self._reconnect_max = reconnect_max
        self._silence_timeout = silence_timeout
        self._availability_check_interval = availability_check_interval

        self._parser = VEDirectParser()
        self._reader_task: asyncio.Task[None] | None = None
        self._availability_task: asyncio.Task[None] | None = None
        self._writer: _Writer | None = None
        self._last_valid_frame: float | None = None
        self._available: bool | None = None
        self._failure_logged = False
        self._checksum_error_logged = False
        self._had_disconnect = False

    @property
    def reader_task(self) -> asyncio.Task[None] | None:
        """Return the owned reader task for lifecycle inspection."""
        return self._reader_task

    @property
    def availability_task(self) -> asyncio.Task[None] | None:
        """Return the owned availability task for lifecycle inspection."""
        return self._availability_task

    @property
    def writer(self) -> _Writer | None:
        """Return the current serial writer/transport."""
        return self._writer

    def start(self) -> None:
        """Start exactly one reader and one availability task."""
        if self._reader_task is not None and not self._reader_task.done():
            return
        if self._availability_task is not None and not self._availability_task.done():
            self._availability_task.cancel()
        self._set_available(False)
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name=f"victronusb-reader-{self._device}"
        )
        self._availability_task = asyncio.create_task(
            self._availability_loop(),
            name=f"victronusb-availability-{self._device}",
        )

    async def stop(self) -> None:
        """Cancel and await every owned task, then close the transport."""
        tasks = [
            task
            for task in (self._reader_task, self._availability_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_writer(self._writer)
        self._reader_task = None
        self._availability_task = None
        self._last_valid_frame = None
        self._set_available(False)

    async def _reader_loop(self) -> None:
        backoff = self._reconnect_initial
        try:
            while True:
                writer: _Writer | None = None
                try:
                    reader, writer = await self._connector(**self._connection_kwargs)
                    self._writer = writer
                    self._parser.reset()
                    backoff = self._reconnect_initial
                    if self._had_disconnect:
                        _LOGGER.info("Serial device %s reconnected", self._device)
                    else:
                        _LOGGER.info("Serial device %s connected", self._device)
                    await self._read_connection(reader)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # transport implementations vary
                    self._set_available(False)
                    self._log_disconnect(exc, backoff)
                    self._had_disconnect = True
                finally:
                    await self._close_writer(writer)

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max)
        finally:
            await self._close_writer(self._writer)
            _LOGGER.info("Serial reader task terminated for %s", self._device)

    async def _read_connection(self, reader: _Reader) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._silence_timeout

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _ConnectionLost(
                    f"no valid VE.Direct frame for {self._silence_timeout:g} seconds"
                )
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise _ConnectionLost(
                    f"no valid VE.Direct frame for {self._silence_timeout:g} seconds"
                ) from exc

            if not data:
                raise _ConnectionLost("serial device reached EOF")

            checksum_failures = self._parser.checksum_failures
            frames = self._parser.feed(data)
            if self._parser.checksum_failures != checksum_failures:
                self._log_checksum_failure()

            for frame in frames:
                await self._on_frame(frame.records)
                now = loop.time()
                self._last_valid_frame = now
                deadline = now + self._silence_timeout
                if self._had_disconnect:
                    self._failure_logged = False
                    self._checksum_error_logged = False
                    self._had_disconnect = False
                self._set_available(True)

    async def _availability_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._availability_check_interval)
                last_frame = self._last_valid_frame
                available = (
                    last_frame is not None
                    and asyncio.get_running_loop().time() - last_frame
                    < self._silence_timeout
                )
                self._set_available(available)
        finally:
            _LOGGER.debug("Availability task terminated for %s", self._device)

    def _set_available(self, available: bool) -> None:
        if available == self._available:
            return
        self._available = available
        self._on_availability(available)

    def _log_disconnect(self, exc: Exception, backoff: float) -> None:
        if self._failure_logged:
            _LOGGER.debug(
                "Serial device %s remains disconnected: %s; retrying in %.1fs",
                self._device,
                exc,
                backoff,
            )
            return
        _LOGGER.warning(
            "Serial device %s disconnected: %s; retrying in %.1fs",
            self._device,
            exc,
            backoff,
        )
        self._failure_logged = True

    def _log_checksum_failure(self) -> None:
        if self._checksum_error_logged:
            _LOGGER.debug(
                "Discarded another invalid VE.Direct checksum from %s",
                self._device,
            )
            return
        _LOGGER.warning(
            "Discarded a VE.Direct frame with an invalid checksum from %s",
            self._device,
        )
        self._checksum_error_logged = True

    async def _close_writer(self, writer: _Writer | None) -> None:
        if writer is None:
            return
        if self._writer is writer:
            self._writer = None
        try:
            writer.close()
            wait_closed = getattr(writer, "wait_closed", None)
            if wait_closed is not None:
                await asyncio.wait_for(wait_closed(), timeout=1.0)
        except Exception as exc:  # closure must not block reload cleanup
            _LOGGER.debug("Error while closing serial device %s: %s", self._device, exc)
