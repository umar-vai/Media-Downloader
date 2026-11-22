from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from media_core import png_disguise  # noqa: E402


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


FAKE_PNG = (
    png_disguise.PNG_SIGNATURE
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    + _chunk(b"IEND", b"")
)


def _segment(n: int) -> bytes:
    return b"".join(bytes([0x47, n, 0, 0]) + bytes(184) for _ in range(50))


class PngDisguiseTests(unittest.TestCase):
    def test_strips_image_before_every_segment(self) -> None:
        segments = [_segment(i) for i in range(30)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.f1080p.mp4"
            path.write_bytes(b"".join(FAKE_PNG + seg for seg in segments))
            self.assertTrue(png_disguise.strip_png_disguise(path))
            self.assertEqual(path.read_bytes(), b"".join(segments))
            self.assertFalse(png_disguise.strip_png_disguise(path))  # already clean
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["v.f1080p.mp4"])

    def test_works_across_read_boundaries(self) -> None:
        old = png_disguise._CHUNK
        png_disguise._CHUNK = 1000  # force images to straddle reads
        try:
            segments = [_segment(i) for i in range(20)]
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "a.mp4"
                path.write_bytes(b"".join(FAKE_PNG + seg for seg in segments))
                self.assertTrue(png_disguise.strip_png_disguise(path))
                self.assertEqual(path.read_bytes(), b"".join(segments))
        finally:
            png_disguise._CHUNK = old

    def test_real_media_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mp4"
            data = b"\x00\x00\x00\x18ftypmp42" + png_disguise.PNG_SIGNATURE + b"junk" * 10
            path.write_bytes(data)
            self.assertFalse(png_disguise.strip_png_disguise(path))
            self.assertEqual(path.read_bytes(), data)

    def test_stray_signature_inside_stream_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mp4"
            body = _segment(1) + png_disguise.PNG_SIGNATURE + b"notachunk" + _segment(2)
            path.write_bytes(FAKE_PNG + body)
            self.assertTrue(png_disguise.strip_png_disguise(path))
            self.assertEqual(path.read_bytes(), body)


if __name__ == "__main__":
    unittest.main()
