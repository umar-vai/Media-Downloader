"""Plain HTTP file downloads (zip, exe, pdf, iso, direct mp4...) handed over by the browser.

Video pages go through yt-dlp; a file the browser was about to save is fetched as-is here,
with the browser's cookies and referer, into ``<name>.mdpart`` and renamed when complete.
Stopping keeps the partial file, and the next attempt continues it with an HTTP Range
request when the server supports that.
"""

from __future__ import annotations

import http.cookiejar
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

PART_SUFFIX = ".mdpart"
CHUNK = 256 * 1024
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0 Safari/537.36")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_STREAM_EXTENSIONS = {"m3u8", "mpd", "ism", "f4m"}
# Extensions that are files to save as-is (anything a video site would not serve as a page).
FILE_EXTENSIONS = {
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "cab", "dmg",
    "exe", "msi", "msix", "appx", "apk", "xapk", "deb", "rpm", "appimage", "pkg", "bin",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt", "csv", "epub",
    "torrent", "jar", "psd", "ai", "svg", "ttf", "otf", "woff2",
}

# What yt-dlp may legitimately save as media; anything else it calls a "direct" video is a file.
MEDIA_EXTENSIONS = {
    "mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "ts", "3gp", "mpg", "mpeg", "ogv",
    "mp3", "m4a", "aac", "flac", "opus", "ogg", "oga", "wav", "wma", "mka",
}

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[float, str], None]
MetricsCallback = Callable[[dict[str, Any]], None]


class Cancelled(RuntimeError):
    pass


def clean_filename(name: str, fallback: str = "download") -> str:
    name = _BAD_CHARS.sub("_", unquote(str(name or ""))).strip().strip(". ")
    name = re.sub(r"\s+", " ", name)
    if len(name) > 180:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 180 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else name[:180]
    return name or fallback


def extension_of(name: str) -> str:
    suffix = Path(str(name or "")).suffix.lower().lstrip(".")
    return suffix if suffix.isalnum() and len(suffix) <= 9 else ""


def name_from_url(url: str) -> str:
    try:
        return clean_filename(Path(unquote(urlsplit(url).path)).name, "")
    except ValueError:
        return ""


def looks_like_file(url: str) -> bool:
    """A link whose path ends in a file type that is saved as-is (not a video page or a stream)."""
    ext = extension_of(name_from_url(url))
    return ext in FILE_EXTENSIONS and ext not in _STREAM_EXTENSIONS


def filename_from_disposition(value: str) -> str:
    if not value:
        return ""
    message = Message()
    message["content-disposition"] = value
    name = message.get_filename() or ""
    if not name:
        match = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";]+)", value, re.IGNORECASE)
        name = match.group(1) if match else ""
    return clean_filename(name, "")


def choose_filename(*, suggested: str, disposition: str, url: str, content_type: str) -> str:
    """The name the browser chose (it already read Content-Disposition), else the server's, else the URL's."""
    suggested = clean_filename(suggested, "")
    name = (suggested if extension_of(suggested) else "") or filename_from_disposition(disposition) or suggested         or name_from_url(url) or "download"
    if not extension_of(name):
        guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
        if guessed and guessed not in {".html", ".htm"}:
            name += guessed
    return name


def is_plain_file_info(info: dict[str, Any] | None) -> bool:
    """yt-dlp's generic extractor accepted a direct link that is not audio/video (it would be
    saved as ``name.unknown_video``)."""
    if not isinstance(info, dict) or not info.get("direct"):
        return False
    return str(info.get("ext") or "").lower() not in MEDIA_EXTENSIONS


def _is_local(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in {"localhost", "::1"} or host.startswith("127.")


def _opener(cookiefile: str, url: str = "") -> urllib.request.OpenerDirector:
    handlers: list[Any] = []
    if cookiefile and Path(cookiefile).exists():
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(cookiefile, ignore_discard=True, ignore_expires=True)
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        except (OSError, http.cookiejar.LoadError):
            pass
    try:
        from network_proxy import active_proxy_url
        proxy = active_proxy_url()
    except Exception:
        proxy = ""
    if proxy.startswith(("http://", "https://")) and not _is_local(url):
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def _request(url: str, headers: dict[str, str], referer: str, start: int = 0) -> urllib.request.Request:
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", DEFAULT_UA)
    request.add_header("Accept", "*/*")
    for key, value in (headers or {}).items():
        if key and value and "\n" not in str(value) and "\r" not in str(value):
            request.add_header(str(key), str(value))
    if referer:
        request.add_header("Referer", referer)
    if start > 0:
        request.add_header("Range", f"bytes={start}-")
    return request


def probe_file(url: str, *, headers: dict[str, str] | None = None, cookiefile: str = "", referer: str = "",
               timeout: float = 15) -> dict[str, Any] | None:
    """When the link answers with a downloadable file instead of a web page: its name and size.

    A stream playlist (HLS .m3u8 / DASH .mpd) is never a file to save: it is a few hundred bytes
    that point at the real video, so a failed analysis must be reported, not saved as "master.m3u8"."""
    if extension_of(name_from_url(url)) in _STREAM_EXTENSIONS:
        return None
    try:
        with _opener(cookiefile, url).open(_request(url, headers or {}, referer), timeout=timeout) as response:
            disposition = response.headers.get("Content-Disposition") or ""
            content_type = (response.headers.get("Content-Type") or "").lower()
            size = int(response.headers.get("Content-Length") or 0)
            final_url = response.geturl() or url
    except Exception:
        return None
    if any(tag in content_type for tag in ("mpegurl", "dash+xml")):
        return None
    if "attachment" not in disposition.lower() and (
        not content_type or content_type.startswith(("text/html", "application/xhtml"))
    ):
        return None
    name = choose_filename(suggested="", disposition=disposition, url=final_url, content_type=content_type)
    return {"name": name, "size": size, "content_type": content_type}


def probe_is_file(url: str, **kwargs: Any) -> bool:
    return probe_file(url, **kwargs) is not None


def _target(directory: Path, name: str) -> Path:
    """First free name: a finished file is never overwritten, a matching .mdpart is continued."""
    stem, ext = Path(name).stem, Path(name).suffix
    for index in range(1, 1000):
        candidate = directory / (name if index == 1 else f"{stem} ({index}){ext}")
        if not candidate.exists():
            return candidate
    return directory / f"{stem} {int(time.time())}{ext}"


def direct_download(
    *,
    url: str,
    download_dir: Path,
    filename: str = "",
    cancel_event: threading.Event,
    headers: dict[str, str] | None = None,
    cookiefile: str = "",
    referer: str = "",
    on_status: StatusCallback | None = None,
    on_progress: ProgressCallback | None = None,
    on_metrics: MetricsCallback | None = None,
    timeout: float = 30,
) -> Path:
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    opener = _opener(cookiefile, url)
    if on_status:
        on_status("Connecting")

    def connect(offset: int) -> Any:
        try:
            return opener.open(_request(url, headers or {}, referer, offset), timeout=timeout)
        except urllib.error.HTTPError as exc:
            if offset and exc.code == 416:
                return None  # the saved part no longer matches the file
            raise RuntimeError(f"HTTP Error {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Connection failed: {exc.reason}") from exc

    # When the browser already named the file, a stopped download is continued with the very
    # first request; otherwise the name comes from the response headers.
    known = clean_filename(filename, "") if extension_of(filename) else ""
    final = _target(download_dir, known) if known else None
    part = final.with_name(final.name + PART_SUFFIX) if final else None
    start = part.stat().st_size if part is not None and part.exists() else 0
    response = connect(start) if start else None
    if response is None:
        start = 0
        response = connect(0)

    with response:
        if final is None or part is None:
            name = choose_filename(suggested=filename, disposition=response.headers.get("Content-Disposition") or "",
                                   url=response.geturl() or url,
                                   content_type=response.headers.get("Content-Type") or "")
            final = _target(download_dir, name)
            part = final.with_name(final.name + PART_SUFFIX)
            start = part.stat().st_size if part.exists() else 0
            accepts_ranges = "bytes" in (response.headers.get("Accept-Ranges") or "").lower()
            length = int(response.headers.get("Content-Length") or 0)
            if start and accepts_ranges and (not length or start < length):
                ranged = connect(start)
                if ranged is not None:
                    response.close()
                    response = ranged
        if response.status != 206:
            start = 0
            total = int(response.headers.get("Content-Length") or 0)
        else:
            match = re.search(r"/(\d+)\s*$", response.headers.get("Content-Range") or "")
            total = int(match.group(1)) if match else start + int(response.headers.get("Content-Length") or 0)
        if on_status:
            on_status("Resuming download" if start else "Downloading")

        done = start
        window: list[tuple[float, int]] = [(time.monotonic(), done)]
        last_report = 0.0
        with open(part, "ab" if start else "wb") as handle:
            while True:
                if cancel_event.is_set():
                    raise Cancelled("Download stopped.")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                window.append((now, done))
                while len(window) > 2 and now - window[0][0] > 3.0:
                    window.pop(0)
                if now - last_report >= 0.25:
                    last_report = now
                    span = now - window[0][0]
                    speed = (done - window[0][1]) / span if span > 0.2 else 0.0
                    eta = int((total - done) / speed) if total and speed > 0 else None
                    if on_metrics:
                        on_metrics({"downloaded_bytes": done, "total_bytes": total, "speed_bps": speed,
                                    "eta_seconds": eta})
                    if on_progress:
                        on_progress(min(0.999, done / total) if total else 0.0, "Downloading")
        response.close()
    if total and done < total:
        raise RuntimeError(f"Incomplete download: received {done} of {total} bytes (connection reset).")
    os.replace(part, final)
    if on_metrics:
        on_metrics({"downloaded_bytes": done, "total_bytes": total or done, "speed_bps": 0.0, "eta_seconds": 0})
    if on_progress:
        on_progress(1.0, "Download finished")
    return final
