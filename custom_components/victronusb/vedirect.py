"""Incremental parser for VE.Direct TEXT protocol frames."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

_CHECKSUM_LABEL = b"Checksum"
_MAX_LABEL_LENGTH = 32
_MAX_VALUE_LENGTH = 512
_MAX_FRAME_LENGTH = 8192


@dataclass(frozen=True, slots=True)
class VEDirectFrame:
    """A validated VE.Direct TEXT frame."""

    records: tuple[tuple[str, str], ...]


class _State(Enum):
    WAIT_FOR_FRAME = auto()
    LABEL = auto()
    VALUE = auto()
    AFTER_CARRIAGE_RETURN = auto()
    CHECKSUM = auto()
    HEX = auto()


class VEDirectParser:
    """Parse arbitrary byte chunks without decoding the raw checksum byte."""

    def __init__(self) -> None:
        """Initialize parser state and diagnostic counters."""
        self.checksum_failures = 0
        self.malformed_frames = 0
        self._state = _State.WAIT_FOR_FRAME
        self._sync_carriage_return = False
        self._frame = bytearray()
        self._label = bytearray()
        self._value = bytearray()
        self._records: list[tuple[str, str]] = []

    def reset(self) -> None:
        """Discard an incomplete frame while retaining diagnostic counters."""
        self._reset_state()

    def feed(self, data: bytes) -> list[VEDirectFrame]:
        """Consume bytes and return every complete, valid TEXT frame."""
        frames: list[VEDirectFrame] = []
        for byte in data:
            frame = self._feed_byte(byte)
            if frame is not None:
                frames.append(frame)
        return frames

    def _feed_byte(self, byte: int) -> VEDirectFrame | None:
        if self._state is _State.WAIT_FOR_FRAME:
            if self._sync_carriage_return and byte == 0x0A:
                self._frame.extend(b"\r\n")
                self._state = _State.LABEL
                self._sync_carriage_return = False
            else:
                self._sync_carriage_return = byte == 0x0D
            return None

        if self._state is _State.HEX:
            if byte == 0x0A:
                self._reset_state()
            return None

        self._frame.append(byte)
        if len(self._frame) > _MAX_FRAME_LENGTH:
            self._mark_malformed(byte)
            return None

        if self._state is _State.LABEL:
            if not self._label and byte == ord(":"):
                self._state = _State.HEX
                return None
            if byte == 0x09:
                if not self._label:
                    self._mark_malformed(byte)
                elif bytes(self._label).lower() == _CHECKSUM_LABEL.lower():
                    self._state = _State.CHECKSUM
                else:
                    self._state = _State.VALUE
                return None
            if byte in (0x0D, 0x0A) or len(self._label) >= _MAX_LABEL_LENGTH:
                self._mark_malformed(byte)
                return None
            self._label.append(byte)
            return None

        if self._state is _State.VALUE:
            if byte == 0x0D:
                self._state = _State.AFTER_CARRIAGE_RETURN
                return None
            if len(self._value) >= _MAX_VALUE_LENGTH:
                self._mark_malformed(byte)
                return None
            self._value.append(byte)
            return None

        if self._state is _State.AFTER_CARRIAGE_RETURN:
            if byte != 0x0A:
                self._mark_malformed(byte)
                return None
            try:
                label = self._label.decode("ascii")
                value = self._value.decode("ascii")
            except UnicodeDecodeError:
                self._mark_malformed(byte)
                return None
            self._records.append((label, value))
            self._label.clear()
            self._value.clear()
            self._state = _State.LABEL
            return None

        # A checksum value is exactly one raw byte. It may itself be CR, LF,
        # whitespace, a control character, or invalid UTF-8.
        if self._state is _State.CHECKSUM:
            if sum(self._frame) & 0xFF:
                self.checksum_failures += 1
                self._reset_state()
                return None
            frame = VEDirectFrame(tuple(self._records))
            self._reset_state()
            return frame

        return None

    def _mark_malformed(self, current_byte: int) -> None:
        """Count a malformed frame and use a trailing CR to resynchronize."""
        self.malformed_frames += 1
        self._reset_state()
        self._sync_carriage_return = current_byte == 0x0D

    def _reset_state(self) -> None:
        self._state = _State.WAIT_FOR_FRAME
        self._sync_carriage_return = False
        self._frame.clear()
        self._label.clear()
        self._value.clear()
        self._records.clear()
