"""Tests for byte-oriented VE.Direct TEXT parsing."""

from __future__ import annotations

import unittest

from tests.module_loader import load_integration_module


def make_frame(
    records: tuple[tuple[bytes, bytes], ...] = ((b"PID", b"0xA381"),),
    *,
    checksum: int | None = None,
) -> bytes:
    """Build a valid frame, optionally forcing a particular raw checksum byte."""
    body = bytearray(b"\r\n")
    for label, value in records:
        body.extend(label + b"\t" + value + b"\r\n")

    if checksum is not None:
        # 65 is coprime to 256, so a run of A bytes can force every checksum.
        body.extend(b"X\t")
        for padding_length in range(256):
            candidate = body + (b"A" * padding_length) + b"\r\nChecksum\t"
            if (-sum(candidate)) & 0xFF == checksum:
                return bytes(candidate) + bytes((checksum,))
        raise AssertionError("unable to construct requested checksum")

    body.extend(b"Checksum\t")
    return bytes(body) + bytes(((-sum(body)) & 0xFF,))


class VEDirectParserTests(unittest.TestCase):
    """Exercise parser behavior independently from Home Assistant."""

    def setUp(self) -> None:
        module = load_integration_module("vedirect")
        self.parser = module.VEDirectParser()

    def test_binary_checksum_bytes_are_preserved(self) -> None:
        """Changing checksum handling back to text decoding breaks this test."""
        for checksum in (0x20, 0x09, 0x0D, 0x0A, 0x01, 0xB7):
            with self.subTest(checksum=checksum):
                parser = type(self.parser)()
                frames = []
                for byte in make_frame(checksum=checksum):
                    frames.extend(parser.feed(bytes((byte,))))

                self.assertEqual(1, len(frames))
                self.assertEqual(("PID", "0xA381"), frames[0].records[0])
                self.assertEqual(0, parser.checksum_failures)

    def test_bad_checksum_does_not_poison_next_frame(self) -> None:
        """Returning or terminating on checksum failure breaks this test."""
        corrupt = bytearray(make_frame())
        corrupt[-1] ^= 0x01
        valid = make_frame(((b"V", b"13044"),))

        frames = self.parser.feed(bytes(corrupt) + valid)

        self.assertEqual(((("V", "13044"),), 1), (frames[0].records, len(frames)))
        self.assertEqual(1, self.parser.checksum_failures)

    def test_malformed_record_resynchronizes_to_valid_frame(self) -> None:
        """Leaving the parser in a malformed state breaks this test."""
        malformed = b"\r\nBROKEN\r\nnot-a-record\r\n"
        valid = make_frame(((b"SOC", b"1000"),))

        frames = self.parser.feed(malformed + valid)

        self.assertEqual([(("SOC", "1000"),)], [frame.records for frame in frames])
        self.assertGreaterEqual(self.parser.malformed_frames, 1)


if __name__ == "__main__":
    unittest.main()
