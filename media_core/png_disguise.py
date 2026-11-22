"""Undo the "fake PNG" trick some streaming sites use on their HLS segments.

Each video segment is served as a tiny 1x1 PNG image followed by the real MPEG-TS data, so a
browser's image cache or a casual scraper sees a picture. yt-dlp joins the segments as they are,
so the downloaded stream starts with a PNG and has one more PNG before every segment. ffmpeg then
reads the whole file as a picture ("png_pipe") with no audio, and merging video + audio fails with
"Error opening output files: Invalid argument" every time, however often it is retried.

Removing those PNG images leaves the plain MPEG-TS stream, which ffmpeg merges normally.
"""

from __future__ import annotations

import os
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHUNK = 8 << 20
# A disguise image is a few dozen bytes; anything this large is not one.
_MAX_IMAGE = 1 << 20


def is_png_disguised(path: str | os.PathLike[str]) -> bool:
    """True when a downloaded media file actually starts with a PNG image."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return False
    return head.startswith(PNG_SIGNATURE) and head[12:16] == b"IHDR"


def _image_length(buf: bytes, start: int) -> int | None:
    """Byte length of the PNG image at ``buf[start:]`` (signature to the end of IEND).

    None when it is not a complete, well-formed PNG within the buffer."""
    pos = start + len(PNG_SIGNATURE)
    first = True
    while pos + 8 <= len(buf):
        length = int.from_bytes(buf[pos:pos + 4], "big")
        kind = buf[pos + 4:pos + 8]
        if first and kind != b"IHDR":
            return None
        first = False
        if not kind.isalpha() or length > _MAX_IMAGE:
            return None
        pos += 12 + length  # length + type + data + CRC
        if pos - start > _MAX_IMAGE or pos > len(buf):
            return None
        if kind == b"IEND":
            return pos - start
    return None


def strip_png_disguise(path: str | os.PathLike[str]) -> bool:
    """Remove every disguise PNG from a downloaded stream, in place.

    Does nothing (and returns False) for a file that does not start with a PNG, so real media
    is never touched. Returns True when the file was cleaned."""
    source = Path(path)
    if not is_png_disguised(source):
        return False
    temp = source.with_name(source.name + ".unpng")
    try:
        with open(source, "rb") as src, open(temp, "wb") as dst:
            buf = b""
            eof = False
            while True:
                if not eof and len(buf) < _CHUNK + _MAX_IMAGE:
                    data = src.read(_CHUNK)
                    eof = not data
                    buf += data
                index = buf.find(PNG_SIGNATURE)
                if index < 0:
                    # Keep a tail that could be the start of a signature split across reads.
                    keep = len(buf) if eof else max(0, len(buf) - (len(PNG_SIGNATURE) - 1))
                    dst.write(buf[:keep])
                    buf = buf[keep:]
                    if eof:
                        break
                    continue
                dst.write(buf[:index])
                buf = buf[index:]
                if not eof and len(buf) < _MAX_IMAGE:
                    continue  # read more so the whole image is in the buffer
                size = _image_length(buf, 0)
                if size is None:
                    # Media data that happens to contain the signature: keep it.
                    dst.write(buf[:1])
                    buf = buf[1:]
                else:
                    buf = buf[size:]
                if eof and not buf:
                    break
        os.replace(temp, source)
        return True
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
