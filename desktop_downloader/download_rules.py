"""Per-site download rules: send links from a host to a folder and/or a fixed quality.

Rules are typed one per line in Settings, for example::

    example.com => D:\\Videos\\Example | 1080p
    youtube.com => D:\\Music
    other.com | 480p
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

QUALITIES = {"Best available", "1080p", "720p", "480p", "360p"}


@dataclass(frozen=True)
class Rule:
    host: str
    folder: str = ""
    quality: str = ""


def parse_rules(text: str) -> list[Rule]:
    rules: list[Rule] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        folder, quality = "", ""
        if "|" in line:
            line, _, tail = line.rpartition("|")
            quality = tail.strip()
            if quality.lower() == "best":
                quality = "Best available"
            if quality not in QUALITIES:
                match = next((q for q in QUALITIES if q.lower() == quality.lower()), "")
                quality = match
        if "=>" in line:
            host_part, _, folder = line.partition("=>")
        else:
            host_part = line
        host = host_part.strip().lower()
        host = re.sub(r"^https?://", "", host).split("/")[0].removeprefix("www.")
        if not host:
            continue
        rules.append(Rule(host=host, folder=folder.strip().strip('"'), quality=quality))
    return rules


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def match_rule(url: str, rules: list[Rule]) -> Rule | None:
    host = _host(url)
    if not host:
        return None
    best: Rule | None = None
    for rule in rules:
        if host == rule.host or host.endswith("." + rule.host):
            if best is None or len(rule.host) > len(best.host):
                best = rule
    return best


def resolve_target(
    url: str,
    *,
    rules: list[Rule],
    default_dir: Path,
    default_quality: str,
    organize_by_site: bool = False,
    platform: str = "",
) -> tuple[Path, str]:
    """Return ``(download_dir, video_quality)`` for a link after applying rules."""
    rule = match_rule(url, rules)
    folder = default_dir
    quality = default_quality
    if rule is not None:
        if rule.folder:
            folder = Path(rule.folder).expanduser()
        if rule.quality:
            quality = rule.quality
        return folder, quality
    if organize_by_site:
        label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", platform or _host(url)).strip(" ._")
        if label:
            folder = default_dir / label
    return folder, quality
