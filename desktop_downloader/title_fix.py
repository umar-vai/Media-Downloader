"""Make a captured page title agree with the page address.

Sites that switch between episodes without reloading often keep the title of the first (or last)
episode while the address changes (".../ep-5"). The address is the one that follows the video
being watched, so its episode number wins.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

_URL_EPISODE = [
    re.compile(r"/(?:ep|eps|episode|episodes)[-_ ]?(\d{1,4})(?=$|[/?#\-_.])", re.I),
    re.compile(r"/(?:ep|eps|episode|episodes)/(\d{1,4})(?=$|[/?#\-_.])", re.I),
    re.compile(r"[?&#;](?:ep|episode|e|eps)=(\d{1,4})(?=$|[&#;])", re.I),
    re.compile(r"-episode-(\d{1,4})(?=$|[/?#\-_.])", re.I),
]
_TITLE_EPISODE = re.compile(r"\b(episode|ep\.?|eps)(\s*#?\s*)(\d{1,4})\b", re.I)
_NOISE = [
    re.compile(r"\s*(?:[-|–—:]\s*)?(?:watch\s+)?(?:online|english)?\s*(?:with\s+)?(?:sub\s*[/&]\s*dub|sub\s*&\s*dub|subbed|dubbed)\b.*$", re.I),
    re.compile(r"\s+online(\s+free)?$", re.I),
    re.compile(r"\s+english$", re.I),
]


def episode_from_url(url: str) -> str:
    parts = urlsplit(url or "")
    text = unquote(parts.path) + ("?" + parts.query if parts.query else "") + ("#" + parts.fragment if parts.fragment else "")
    for pattern in _URL_EPISODE:
        match = pattern.search(text)
        if match:
            return str(int(match.group(1)))
    return ""


def tidy_title(title: str) -> str:
    text = " ".join(str(title or "").split())
    text = re.sub(r"^watch\s+", "", text, flags=re.I)
    for pattern in _NOISE:
        text = pattern.sub("", text).strip(" -|–—:")
    return text


def reconcile_title(title: str, page_url: str) -> str:
    """The title with its episode number replaced by the one in the page address."""
    title = " ".join(str(title or "").split())
    episode = episode_from_url(page_url)
    if not title or not episode:
        return title
    title = tidy_title(title) or title
    if _TITLE_EPISODE.search(title):
        return _TITLE_EPISODE.sub(lambda m: f"{m.group(1)}{m.group(2)}{episode}", title, count=1)
    return f"{title} Episode {episode}"
