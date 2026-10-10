from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any


REPOSITORY = "umar-vai/Media-Downloader"
TAG_PREFIX = "core-v"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=40"
USER_AGENT = "MediaDownloaderLocalCore/1.0"
ASSET_NAMES = ("MediaDownloaderCore.exe",)
UPDATER_ASSET_NAME = "MediaDownloaderCoreUpdater.exe"


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", str(value or ""))
    if not match:
        raise ValueError(f"Invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def select_release(releases: list[dict[str, Any]], *, channel: str) -> dict[str, Any] | None:
    candidates: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for release in releases:
        tag = str(release.get("tag_name") or "")
        if not tag.startswith(TAG_PREFIX):
            continue
        if bool(release.get("draft")):
            continue
        if channel == "stable" and bool(release.get("prerelease")):
            continue
        try:
            parsed = version_tuple(tag[len(TAG_PREFIX):])
        except ValueError:
            continue
        candidates.append((parsed, release))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _asset_map(release: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in release.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        if name and url:
            result[name] = url
    return result


def _expected_checksum(text: str, asset_name: str) -> str:
    for line in str(text or "").splitlines():
        match = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if match and Path(match.group(2)).name == asset_name:
            return match.group(1).lower()
    single = re.search(r"\b([0-9a-fA-F]{64})\b", str(text or ""))
    if single:
        return single.group(1).lower()
    raise ValueError("Release checksum file does not contain a SHA-256 hash.")


class CoreUpdateService:
    def __init__(
        self,
        *,
        current_version: str,
        update_dir: Path,
        channel: str = "stable",
        can_apply: bool = False,
    ) -> None:
        self.current_version = current_version
        self.update_dir = Path(update_dir)
        self.channel = channel if channel in {"stable", "beta"} else "stable"
        self.can_apply = bool(can_apply)
        self._lock = threading.RLock()
        self._candidate: dict[str, Any] | None = None
        self._state: dict[str, Any] = {
            "status": "idle",
            "detail": "Update check has not run yet.",
            "current_version": current_version,
            "latest_version": current_version,
            "available": False,
            "progress": 0.0,
            "release_url": "",
            "asset_name": "",
            "staged_path": "",
            "staged_updater_path": "",
            "can_apply": False,
        }

    def set_channel(self, channel: str) -> None:
        with self._lock:
            self.channel = channel if channel in {"stable", "beta"} else "stable"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def start_check(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] in {"checking", "downloading"}:
                return dict(self._state)
            self._state.update(status="checking", detail="Checking Local Core releases…", progress=0.0)
        threading.Thread(target=self._check_worker, daemon=True, name="core-update-check").start()
        return self.snapshot()

    def _fetch_json(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _check_worker(self) -> None:
        try:
            releases = self._fetch_json(RELEASES_API)
            if not isinstance(releases, list):
                raise RuntimeError("GitHub returned an unexpected releases response.")

            release = select_release(releases, channel=self.channel)
            if release is None:
                self._candidate = None
                self._set(
                    status="current",
                    detail="No Local Core release has been published on this channel yet.",
                    latest_version=self.current_version,
                    available=False,
                    release_url="",
                    asset_name="",
                    staged_path="",
                    staged_updater_path="",
                )
                return

            tag = str(release.get("tag_name") or "")
            latest = tag[len(TAG_PREFIX):]
            assets = _asset_map(release)
            asset_name = next((name for name in ASSET_NAMES if name in assets), "")
            checksum_name = f"{asset_name}.sha256" if asset_name else ""
            available = version_tuple(latest) > version_tuple(self.current_version)

            updater_checksum_name = f"{UPDATER_ASSET_NAME}.sha256"
            self._candidate = {
                "version": latest,
                "tag": tag,
                "release_url": str(release.get("html_url") or ""),
                "asset_name": asset_name,
                "asset_url": assets.get(asset_name, ""),
                "checksum_url": assets.get(checksum_name, ""),
                "updater_asset_url": assets.get(UPDATER_ASSET_NAME, ""),
                "updater_checksum_url": assets.get(updater_checksum_name, ""),
            }

            if available:
                detail = (
                    f"Local Core v{latest} is available."
                    if asset_name
                    else f"Local Core v{latest} is published, but the Windows Core asset is not attached yet."
                )
                self._set(
                    status="available",
                    detail=detail,
                    latest_version=latest,
                    available=True,
                    release_url=self._candidate["release_url"],
                    asset_name=asset_name,
                    staged_path="",
                    staged_updater_path="",
                    progress=0.0,
                )
            else:
                self._set(
                    status="current",
                    detail=f"Local Core v{self.current_version} is up to date.",
                    latest_version=latest,
                    available=False,
                    release_url=self._candidate["release_url"],
                    asset_name=asset_name,
                    staged_path="",
                    progress=0.0,
                )
        except Exception as exc:
            self._candidate = None
            self._set(status="error", detail=f"Update check failed: {exc}", available=False)

    def start_download(self) -> dict[str, Any]:
        with self._lock:
            candidate = dict(self._candidate or {})
            if not self._state.get("available"):
                raise ValueError("No Local Core update is available.")
            if not candidate.get("asset_url"):
                raise ValueError("The release does not include a Local Core Windows asset yet.")
            if not candidate.get("checksum_url"):
                raise ValueError("The release is missing the required SHA-256 checksum asset.")
            if self._state["status"] == "downloading":
                return dict(self._state)
            self._state.update(status="downloading", detail="Downloading Local Core update…", progress=0.0)

        threading.Thread(
            target=self._download_worker,
            args=(candidate,),
            daemon=True,
            name="core-update-download",
        ).start()
        return self.snapshot()

    def _download_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    def _download_verified_asset(
        self,
        *,
        url: str,
        checksum_url: str,
        target: Path,
        asset_name: str,
        report_progress: bool = False,
    ) -> None:
        checksum_text = self._download_bytes(checksum_url).decode("utf-8", errors="replace")
        expected = _expected_checksum(checksum_text, asset_name)
        partial = target.with_suffix(target.suffix + ".part")
        partial.unlink(missing_ok=True)

        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            digest = hashlib.sha256()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if report_progress:
                    progress = (downloaded / total) if total else 0.0
                    self._set(progress=max(0.0, min(0.98, progress)))

        actual = digest.hexdigest().lower()
        if actual != expected:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded {asset_name} failed SHA-256 verification.")
        partial.replace(target)

    def _download_worker(self, candidate: dict[str, Any]) -> None:
        try:
            version = str(candidate["version"])
            asset_name = str(candidate["asset_name"])
            target_dir = self.update_dir / f"core-v{version}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / asset_name
            self._download_verified_asset(
                url=str(candidate["asset_url"]),
                checksum_url=str(candidate["checksum_url"]),
                target=target,
                asset_name=asset_name,
                report_progress=True,
            )

            staged_updater = ""
            updater_url = str(candidate.get("updater_asset_url") or "")
            updater_checksum_url = str(candidate.get("updater_checksum_url") or "")
            if updater_url and updater_checksum_url:
                updater_target = target_dir / UPDATER_ASSET_NAME
                self._download_verified_asset(
                    url=updater_url,
                    checksum_url=updater_checksum_url,
                    target=updater_target,
                    asset_name=UPDATER_ASSET_NAME,
                )
                staged_updater = str(updater_target)

            self._set(
                status="ready",
                detail=f"Local Core v{version} is downloaded and SHA-256 verified.",
                progress=1.0,
                staged_path=str(target),
                staged_updater_path=staged_updater,
                can_apply=self.can_apply,
            )
        except Exception as exc:
            self._set(status="error", detail=f"Update download failed: {exc}", progress=0.0)
