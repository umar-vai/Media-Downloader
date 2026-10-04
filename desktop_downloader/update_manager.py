from __future__ import annotations

import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GITHUB_REPOSITORY = "umar-vai/Media-Downloader"
GITHUB_WEB_BASE = f"https://github.com/{GITHUB_REPOSITORY}"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
APP_ASSET_NAME = "MediaDownloader.exe"
LEGACY_APP_ASSET_NAME = "Team" + "Fahad" + "YouTubeDownloader.exe"
ASSET_CANDIDATES = (APP_ASSET_NAME, LEGACY_APP_ASSET_NAME)
CHECKSUM_ASSET_NAME = f"{APP_ASSET_NAME}.sha256"
LATEST_CHECKSUM_REDIRECT = f"{GITHUB_WEB_BASE}/releases/latest/download/{CHECKSUM_ASSET_NAME}"
USER_AGENT = "MediaDownloader-Updater/1.2"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    notes: str
    asset_url: str
    checksum_url: str
    html_url: str


def normalize_version(value: str) -> tuple[int, int, int]:
    text = (value or "").strip()
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?", text, re.I)
    if not match:
        raise ValueError(f"Unsupported version: {value!r}")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def is_newer_version(latest: str, current: str) -> bool:
    return normalize_version(latest) > normalize_version(current)


def _reason_text(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    text = str(reason or exc).strip()
    return text[:240] or exc.__class__.__name__


def _open_with_retries(
    request: urllib.request.Request,
    timeout: int,
    attempts: int = 4,
) -> urllib.response.addinfourl:
    last_error: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= attempts:
                raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt >= attempts:
                raise
        time.sleep(min(8.0, 1.25 * (2 ** (attempt - 1))))
    if last_error:
        raise last_error
    raise UpdateError("The network request could not be started.")


def _headers_for_url(url: str, accept: str | None = None) -> dict[str, str]:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    headers = {
        "User-Agent": USER_AGENT,
        "Connection": "close",
    }
    if host == "api.github.com":
        headers.update(
            {
                "Accept": accept or "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
    else:
        headers["Accept"] = accept or "*/*"
    return headers


def _request(
    url: str,
    timeout: int = 12,
    attempts: int = 4,
    accept: str | None = None,
) -> urllib.response.addinfourl:
    request = urllib.request.Request(url, headers=_headers_for_url(url, accept=accept))
    try:
        return _open_with_retries(request, timeout=timeout, attempts=attempts)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published update release is available yet.") from exc
        raise UpdateError(f"Update server returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
        raise UpdateError(f"Could not connect to the update server: {_reason_text(exc)}") from exc


def _release_from_api(timeout: int) -> ReleaseInfo:
    with _request(LATEST_RELEASE_API, timeout=timeout, attempts=4) as response:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("The update server returned an invalid response.") from exc

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("The latest release does not contain a version tag.")

    try:
        normalize_version(tag_name)
    except ValueError as exc:
        raise UpdateError(f"The release version {tag_name!r} is not supported.") from exc

    assets = {
        str(asset.get("name") or ""): str(asset.get("browser_download_url") or "")
        for asset in payload.get("assets") or []
        if isinstance(asset, dict)
    }
    selected_name = next(
        (name for name in ASSET_CANDIDATES if assets.get(name) and assets.get(f"{name}.sha256")),
        "",
    )
    if not selected_name:
        raise UpdateError(f"Release {tag_name} is missing a supported Media Downloader executable/checksum pair.")

    return ReleaseInfo(
        version=tag_name.lstrip("vV"),
        tag_name=tag_name,
        notes=str(payload.get("body") or "").strip(),
        asset_url=assets[selected_name],
        checksum_url=assets[f"{selected_name}.sha256"],
        html_url=str(payload.get("html_url") or "").strip(),
    )


def _release_from_web_redirect(timeout: int) -> ReleaseInfo:
    """Resolve the latest release without api.github.com.

    GitHub's /releases/latest/download/<asset> endpoint redirects to a versioned
    /releases/download/<tag>/<asset> URL. Reading the tiny checksum file lets us
    recover the published tag and construct the matching executable URL while
    avoiding the GitHub REST API entirely.
    """
    with _request(
        LATEST_CHECKSUM_REDIRECT,
        timeout=max(timeout, 20),
        attempts=4,
        accept="application/octet-stream",
    ) as response:
        final_url = response.geturl()
        checksum_text = response.read(16_384).decode("utf-8", errors="replace")

    # Validate the checksum while we already have it, so a captive portal or
    # HTML error page cannot masquerade as a valid release redirect.
    parse_checksum(checksum_text)

    match = re.search(r"/releases/download/([^/]+)/", final_url, re.I)
    if not match:
        raise UpdateError("The GitHub web fallback did not resolve a release tag.")

    tag_name = urllib.parse.unquote(match.group(1)).strip()
    try:
        normalize_version(tag_name)
    except ValueError as exc:
        raise UpdateError(f"The fallback release version {tag_name!r} is not supported.") from exc

    encoded_tag = urllib.parse.quote(tag_name, safe="")
    base = f"{GITHUB_WEB_BASE}/releases/download/{encoded_tag}"
    return ReleaseInfo(
        version=tag_name.lstrip("vV"),
        tag_name=tag_name,
        notes="Latest release detected through the GitHub web fallback.",
        asset_url=f"{base}/{APP_ASSET_NAME}",
        checksum_url=f"{base}/{CHECKSUM_ASSET_NAME}",
        html_url=f"{GITHUB_WEB_BASE}/releases/tag/{encoded_tag}",
    )


def fetch_latest_release(timeout: int = 15) -> ReleaseInfo:
    api_error: UpdateError | None = None
    try:
        return _release_from_api(timeout)
    except UpdateError as exc:
        api_error = exc

    try:
        return _release_from_web_redirect(timeout)
    except UpdateError as fallback_error:
        raise UpdateError(
            f"Both update paths failed. API: {api_error}. Web fallback: {fallback_error}"
        ) from fallback_error


def parse_checksum(text: str) -> str:
    token = (text or "").strip().split()
    if not token or not re.fullmatch(r"[0-9a-fA-F]{64}", token[0]):
        raise UpdateError("The release checksum file is invalid.")
    return token[0].lower()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_release(
    release: ReleaseInfo,
    destination_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    timeout: int = 45,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    partial_path = destination_dir / f"{APP_ASSET_NAME}.part"
    final_path = destination_dir / APP_ASSET_NAME

    try:
        with _request(
            release.checksum_url,
            timeout=timeout,
            attempts=5,
            accept="application/octet-stream",
        ) as response:
            expected_hash = parse_checksum(response.read(16_384).decode("utf-8", errors="replace"))

        last_error: BaseException | None = None
        for attempt in range(1, 6):
            try:
                partial_path.unlink(missing_ok=True)
                request = urllib.request.Request(
                    release.asset_url,
                    headers=_headers_for_url(release.asset_url, accept="application/octet-stream"),
                )
                response = _open_with_retries(request, timeout=timeout, attempts=2)
                downloaded = 0
                total = int(response.headers.get("Content-Length") or 0)
                with response, partial_path.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)
                if partial_path.exists() and partial_path.stat().st_size > 0:
                    last_error = None
                    break
                last_error = UpdateError("The downloaded update file is empty.")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                last_error = exc
                try:
                    partial_path.unlink(missing_ok=True)
                except Exception:
                    pass
            if attempt < 5:
                time.sleep(min(10.0, 1.5 * (2 ** (attempt - 1))))

        if last_error:
            if isinstance(last_error, urllib.error.HTTPError):
                raise UpdateError(f"Update download returned HTTP {last_error.code}.") from last_error
            raise UpdateError(f"The update download could not be started: {_reason_text(last_error)}") from last_error

        if not partial_path.exists() or partial_path.stat().st_size <= 0:
            raise UpdateError("The downloaded update file is empty.")

        actual_hash = sha256_file(partial_path)
        if actual_hash.lower() != expected_hash.lower():
            raise UpdateError("Update verification failed: SHA-256 checksum mismatch.")

        if final_path.exists():
            final_path.unlink()
        partial_path.replace(final_path)
        return final_path
    except Exception:
        try:
            partial_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
