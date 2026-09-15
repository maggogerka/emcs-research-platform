"""Small dependency-free protocol regression test."""

from __future__ import annotations

import struct
import unittest

from desktop_app.protocol import (
    CRC,
    FRAME_ACK,
    FRAME_MAGIC,
    FRAME_VERSION,
    HEADER,
    FrameParser,
    crc16_ccitt,
    decode_frame,
)


class ProtocolTest(unittest.TestCase):
    def test_fragmented_frame_and_resynchronization(self) -> None:
        payload = b"PONG"
        header = HEADER.pack(FRAME_MAGIC, FRAME_VERSION, FRAME_ACK, len(payload), 17, 123456)
        crc = CRC.pack(crc16_ccitt(header[2:] + payload))
        encoded = b"boot log\r\n" + header + payload + crc
        parser = FrameParser()
        frames = []
        for byte in encoded:
            frames.extend(parser.feed(bytes([byte])))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].sequence, 17)
        self.assertEqual(decode_frame(frames[0]), ("ack", "PONG"))
        self.assertEqual(parser.crc_errors, 0)

    def test_bad_crc_is_rejected(self) -> None:
        payload = b"BAD"
        header = HEADER.pack(FRAME_MAGIC, FRAME_VERSION, FRAME_ACK, len(payload), 1, 1)
        parser = FrameParser()
        frames = parser.feed(header + payload + struct.pack("<H", 0))
        self.assertEqual(frames, [])
        self.assertEqual(parser.crc_errors, 1)


if __name__ == "__main__":
    unittest.main()
