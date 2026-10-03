from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GITHUB_REPOSITORY = "umar-vai/Media-Downloader"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
APP_ASSET_NAME = "TeamFahadYouTubeDownloader.exe"
CHECKSUM_ASSET_NAME = f"{APP_ASSET_NAME}.sha256"
USER_AGENT = "TeamFahadYouTubeDownloader-Updater/1.0"


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


def _request(url: str, timeout: int = 12) -> urllib.response.addinfourl:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published update release is available yet.") from exc
        raise UpdateError(f"Update server returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError("Could not connect to the update server.") from exc
    except TimeoutError as exc:
        raise UpdateError("The update check timed out.") from exc


def fetch_latest_release(timeout: int = 12) -> ReleaseInfo:
    with _request(LATEST_RELEASE_API, timeout=timeout) as response:
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
    asset_url = assets.get(APP_ASSET_NAME, "")
    checksum_url = assets.get(CHECKSUM_ASSET_NAME, "")
    if not asset_url:
        raise UpdateError(f"Release {tag_name} is missing {APP_ASSET_NAME}.")
    if not checksum_url:
        raise UpdateError(f"Release {tag_name} is missing the SHA-256 checksum file.")

    return ReleaseInfo(
        version=tag_name.lstrip("vV"),
        tag_name=tag_name,
        notes=str(payload.get("body") or "").strip(),
        asset_url=asset_url,
        checksum_url=checksum_url,
        html_url=str(payload.get("html_url") or "").strip(),
    )


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
    timeout: int = 30,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    partial_path = destination_dir / f"{APP_ASSET_NAME}.part"
    final_path = destination_dir / APP_ASSET_NAME

    try:
        with _request(release.checksum_url, timeout=timeout) as response:
            expected_hash = parse_checksum(response.read(16_384).decode("utf-8", errors="replace"))

        request = urllib.request.Request(release.asset_url, headers={"User-Agent": USER_AGENT})
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise UpdateError(f"Update download returned HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise UpdateError("The update download could not be started.") from exc

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
