from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
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


def _asset_size_map(release: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in release.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if name and size > 0:
            result[name] = size
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().lower()


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

    @staticmethod
    def _curl_cffi_bytes(url: str, *, timeout: float, accept: str = "*/*") -> bytes:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
            },
            timeout=timeout,
            impersonate="chrome",
            allow_redirects=True,
        )
        response.raise_for_status()
        return bytes(response.content)

    def _request_bytes(self, url: str, *, timeout: float, accept: str = "*/*") -> bytes:
        errors: list[str] = []
        for attempt in range(2):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": accept,
                    "Connection": "close",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read()
            except Exception as exc:
                errors.append(f"urllib {attempt + 1}: {exc}")
                time.sleep(0.35 * (attempt + 1))

        try:
            return self._curl_cffi_bytes(url, timeout=timeout, accept=accept)
        except Exception as exc:
            errors.append(f"curl_cffi: {exc}")

        detail = " | ".join(errors[-3:])
        if len(detail) > 700:
            detail = detail[:697] + "..."
        raise RuntimeError(f"Could not reach GitHub update service. {detail}")

    def _fetch_json(self, url: str) -> Any:
        payload = self._request_bytes(
            url,
            timeout=20,
            accept="application/vnd.github+json",
        )
        return json.loads(payload.decode("utf-8"))

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
            asset_sizes = _asset_size_map(release)
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
                "asset_size": int(asset_sizes.get(asset_name, 0)),
                "checksum_url": assets.get(checksum_name, ""),
                "updater_asset_url": assets.get(UPDATER_ASSET_NAME, ""),
                "updater_asset_size": int(asset_sizes.get(UPDATER_ASSET_NAME, 0)),
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
        return self._request_bytes(url, timeout=45)

    def _update_transfer_progress(
        self,
        *,
        partial: Path,
        expected_size: int,
        report_progress: bool,
    ) -> None:
        if not report_progress:
            return
        try:
            downloaded = int(partial.stat().st_size)
        except OSError:
            downloaded = 0
        if expected_size > 0:
            progress = downloaded / expected_size
            self._set(progress=max(0.0, min(0.96, progress)))
        elif downloaded > 0:
            self._set(progress=0.05)

    def _urllib_stream_resume(
        self,
        *,
        url: str,
        partial: Path,
        expected_size: int,
        report_progress: bool,
    ) -> None:
        offset = int(partial.stat().st_size) if partial.exists() else 0
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream",
            "Connection": "close",
        }
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            status = int(getattr(response, "status", 0) or response.getcode() or 0)
            if offset > 0 and status != 206:
                offset = 0
                partial.unlink(missing_ok=True)

            mode = "ab" if offset > 0 else "wb"
            with partial.open(mode) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    self._update_transfer_progress(
                        partial=partial,
                        expected_size=expected_size,
                        report_progress=report_progress,
                    )

    def _native_curl_resume(
        self,
        *,
        url: str,
        partial: Path,
        expected_size: int,
        report_progress: bool,
    ) -> None:
        curl = shutil.which("curl.exe") if os.name == "nt" else shutil.which("curl")
        if not curl:
            raise RuntimeError("Native curl is unavailable.")

        def run(*, resume: bool) -> int:
            command = [
                curl,
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "8",
                "--retry-all-errors",
                "--retry-delay",
                "2",
                "--connect-timeout",
                "20",
                "--output",
                str(partial),
                "--header",
                f"User-Agent: {USER_AGENT}",
                "--header",
                "Accept: application/octet-stream",
            ]
            if resume and partial.exists() and partial.stat().st_size > 0:
                command.extend(["--continue-at", "-"])
            command.append(url)

            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True,
            )
            while process.poll() is None:
                self._update_transfer_progress(
                    partial=partial,
                    expected_size=expected_size,
                    report_progress=report_progress,
                )
                time.sleep(0.5)
            self._update_transfer_progress(
                partial=partial,
                expected_size=expected_size,
                report_progress=report_progress,
            )
            return int(process.returncode or 0)

        resumed = partial.exists() and partial.stat().st_size > 0
        code = run(resume=resumed)
        if code == 0:
            return

        if resumed:
            partial.unlink(missing_ok=True)
            code = run(resume=False)
        if code != 0:
            raise RuntimeError(f"Native curl download failed with exit code {code}.")

    def _curl_cffi_stream_resume(
        self,
        *,
        url: str,
        partial: Path,
        expected_size: int,
        report_progress: bool,
    ) -> None:
        from curl_cffi import requests as curl_requests

        offset = int(partial.stat().st_size) if partial.exists() else 0
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream",
        }
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        response = curl_requests.get(
            url,
            headers=headers,
            timeout=600,
            impersonate="chrome",
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        status = int(getattr(response, "status_code", 0) or 0)
        if offset > 0 and status != 206:
            offset = 0
            partial.unlink(missing_ok=True)

        mode = "ab" if offset > 0 else "wb"
        with partial.open(mode) as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                self._update_transfer_progress(
                    partial=partial,
                    expected_size=expected_size,
                    report_progress=report_progress,
                )

    def _download_verified_asset(
        self,
        *,
        url: str,
        checksum_url: str,
        target: Path,
        asset_name: str,
        expected_size: int = 0,
        report_progress: bool = False,
    ) -> None:
        checksum_text = self._download_bytes(checksum_url).decode("utf-8", errors="replace")
        expected = _expected_checksum(checksum_text, asset_name)
        partial = target.with_suffix(target.suffix + ".part")

        if target.is_file() and _sha256_file(target) == expected:
            if report_progress:
                self._set(progress=0.96)
            return

        if partial.exists() and expected_size > 0 and partial.stat().st_size > expected_size:
            partial.unlink(missing_ok=True)

        resume_bytes = int(partial.stat().st_size) if partial.exists() else 0
        if report_progress and resume_bytes > 0:
            self._set(
                detail=f"Resuming Local Core update from {resume_bytes / 1024 / 1024:.1f} MB…"
            )
            self._update_transfer_progress(
                partial=partial,
                expected_size=expected_size,
                report_progress=True,
            )

        errors: list[str] = []
        try:
            self._urllib_stream_resume(
                url=url,
                partial=partial,
                expected_size=expected_size,
                report_progress=report_progress,
            )
        except Exception as exc:
            errors.append(f"urllib: {exc}")

        if not partial.is_file() or (expected_size > 0 and partial.stat().st_size < expected_size):
            try:
                self._native_curl_resume(
                    url=url,
                    partial=partial,
                    expected_size=expected_size,
                    report_progress=report_progress,
                )
            except Exception as exc:
                errors.append(f"native curl: {exc}")

        if not partial.is_file() or (expected_size > 0 and partial.stat().st_size < expected_size):
            try:
                self._curl_cffi_stream_resume(
                    url=url,
                    partial=partial,
                    expected_size=expected_size,
                    report_progress=report_progress,
                )
            except Exception as exc:
                errors.append(f"curl_cffi: {exc}")

        if not partial.is_file():
            raise RuntimeError("Update download did not create a partial file.")

        if expected_size > 0 and partial.stat().st_size != expected_size:
            detail = " | ".join(errors[-3:])
            raise RuntimeError(
                f"Update download is incomplete ({partial.stat().st_size} of {expected_size} bytes). "
                f"Press Download update again to resume."
                + (f" {detail}" if detail else "")
            )

        actual = _sha256_file(partial)
        if actual != expected:
            # A wrong checksum means the resumable bytes cannot be trusted.
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Downloaded {asset_name} failed SHA-256 verification. "
                "The partial file was cleared; retry will start cleanly."
            )
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
                expected_size=int(candidate.get("asset_size") or 0),
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
                    expected_size=int(candidate.get("updater_asset_size") or 0),
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
            self._set(
                status="error",
                detail=f"Update download paused: {exc}",
            )
