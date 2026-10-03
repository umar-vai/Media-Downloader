from __future__ import annotations

import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

import streamlit as st
import yt_dlp

APP_NAME = "Media Downloader"
MAX_OUTPUT_MB = 450
MAX_OUTPUT_BYTES = MAX_OUTPUT_MB * 1024 * 1024
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

st.set_page_config(page_title=APP_NAME, page_icon="⬇️", layout="wide")

st.markdown(
    """
    <style>
    .stApp {
        background:
            radial-gradient(circle at 12% 0%, rgba(44, 92, 255, .10), transparent 28%),
            radial-gradient(circle at 88% 10%, rgba(124, 58, 237, .10), transparent 30%),
            #07101f;
    }
    .block-container {max-width: 1120px; padding-top: 2.2rem; padding-bottom: 4rem;}
    .hero {
        padding: 1.45rem 1.6rem;
        border: 1px solid rgba(148,163,184,.18);
        border-radius: 22px;
        background: linear-gradient(135deg, rgba(15,23,42,.88), rgba(15,23,42,.58));
        box-shadow: 0 18px 50px rgba(0,0,0,.22);
        margin-bottom: 1.2rem;
    }
    .hero h1 {margin:0; font-size:2rem; letter-spacing:-.03em;}
    .hero p {margin:.45rem 0 0; color:#a9b7cc; font-size:1rem;}
    .badge {
        display:inline-flex; align-items:center; gap:.4rem; padding:.28rem .65rem;
        border-radius:999px; border:1px solid rgba(96,165,250,.25);
        background:rgba(37,99,235,.10); color:#bfdbfe; font-size:.78rem; font-weight:700;
        margin-bottom:.65rem;
    }
    div[data-testid="stDownloadButton"] button,
    div[data-testid="stButton"] button {border-radius:12px; min-height:44px; font-weight:700;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <div class="badge">MEDIA DOWNLOADER</div>
      <h1>Audio & Video Downloader</h1>
      <p>Paste a supported public YouTube link, choose audio or video, then prepare the file.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def is_youtube_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value.strip())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in YOUTUBE_HOSTS


def safe_filename(value: str, fallback: str = "media_download") -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.(mp3|m4a|mp4|webm|mkv|mov)$", "", text, flags=re.I)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text[:120] or fallback).strip()


def format_duration(seconds: Any) -> str:
    try:
        total = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        return "—"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@st.cache_data(ttl=1800, show_spinner=False)
def get_video_info(url: str) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("No media information was returned.")
    return {
        "title": str(info.get("title") or "YouTube video"),
        "channel": str(info.get("channel") or info.get("uploader") or "YouTube"),
        "duration": info.get("duration"),
        "thumbnail": str(info.get("thumbnail") or ""),
    }


def progress_hook(progress_bar: Any, status_box: Any):
    def hook(data: dict[str, Any]) -> None:
        if data.get("status") == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            if total:
                ratio = min(1.0, downloaded / total)
                progress_bar.progress(ratio)
                status_box.caption(f"Downloading… {ratio * 100:.1f}%")
        elif data.get("status") == "finished":
            progress_bar.progress(1.0)
            status_box.caption("Download finished. Finalizing file…")
    return hook


def prepare_download(
    url: str,
    mode: str,
    custom_name: str,
    audio_format: str,
    audio_bitrate: str,
    video_quality: str,
    progress_bar: Any,
    status_box: Any,
) -> tuple[bytes, str, str]:
    with tempfile.TemporaryDirectory(prefix="team_fahad_media_") as tmp:
        folder = Path(tmp)
        outtmpl = str(folder / f"{custom_name}.%(ext)s")
        opts: dict[str, Any] = {
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "cachedir": False,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 4,
            "max_filesize": MAX_OUTPUT_BYTES,
            "progress_hooks": [progress_hook(progress_bar, status_box)],
            "overwrites": True,
        }
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg

        if mode == "Audio":
            codec = audio_format.lower()
            opts.update(
                {
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": codec,
                            "preferredquality": audio_bitrate,
                        }
                    ],
                }
            )
            fallback_mime = "audio/mpeg" if codec == "mp3" else "audio/mp4"
        else:
            if video_quality == "Best available":
                opts["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
            else:
                height = int(video_quality.rstrip("p"))
                opts["format"] = (
                    f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
                    f"b[height<={height}][ext=mp4]/"
                    f"bv*[height<={height}]+ba/b[height<={height}]"
                )
            opts["merge_output_format"] = "mp4"
            fallback_mime = "video/mp4"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(f"Download failed: {str(exc)[:700]}") from exc

        candidates = [
            p for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() not in {".part", ".ytdl", ".tmp", ".temp"}
        ]
        if not candidates:
            raise RuntimeError("The download finished, but no output file was created.")
        output = max(candidates, key=lambda p: p.stat().st_mtime)
        if output.stat().st_size > MAX_OUTPUT_BYTES:
            raise RuntimeError(f"Prepared file exceeds the {MAX_OUTPUT_MB} MB app limit.")
        mime = {
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
        }.get(output.suffix.lower(), fallback_mime)
        return output.read_bytes(), output.name, mime


url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...")
info: dict[str, Any] | None = None

if url.strip():
    if not is_youtube_url(url):
        st.error("Please paste a valid YouTube or youtu.be URL.")
    else:
        try:
            with st.spinner("Reading video information…"):
                info = get_video_info(url.strip())
        except Exception as exc:
            st.error(str(exc))

if info:
    left, right = st.columns([1, 2.2], gap="large")
    with left:
        if info.get("thumbnail"):
            st.image(info["thumbnail"], use_container_width=True)
    with right:
        st.subheader(info["title"])
        st.caption(f"{info['channel']} • {format_duration(info.get('duration'))}")

    mode = st.segmented_control("Download type", ["Audio", "Video"], default="Audio") or "Audio"
    default_name = safe_filename(info["title"])
    custom_name = safe_filename(st.text_input("Custom file name", value=default_name), default_name)

    if mode == "Audio":
        c1, c2 = st.columns(2)
        with c1:
            audio_format = st.selectbox("Audio format", ["MP3", "M4A"], index=0)
        with c2:
            audio_bitrate = st.selectbox("Audio quality", ["128", "192", "256", "320"], index=1)
        video_quality = "Best available"
    else:
        audio_format = "MP3"
        audio_bitrate = "192"
        video_quality = st.selectbox("Video quality", ["360p", "480p", "720p", "1080p", "Best available"], index=2)

    signature = (url.strip(), mode, custom_name, audio_format, audio_bitrate, video_quality)
    prepared = st.session_state.get("prepared_media_download")
    if prepared and prepared.get("signature") != signature:
        st.session_state.pop("prepared_media_download", None)
        prepared = None

    if st.button(f"Prepare {mode} Download", type="primary", use_container_width=True):
        progress = st.progress(0.0)
        status = st.empty()
        try:
            payload, filename, mime = prepare_download(
                url.strip(), mode, custom_name, audio_format, audio_bitrate, video_quality, progress, status
            )
            st.session_state["prepared_media_download"] = {
                "signature": signature,
                "bytes": payload,
                "filename": filename,
                "mime": mime,
            }
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    prepared = st.session_state.get("prepared_media_download")
    if prepared and prepared.get("signature") == signature:
        st.success(f"Ready: {prepared['filename']}")
        st.download_button(
            "Download file",
            data=prepared["bytes"],
            file_name=prepared["filename"],
            mime=prepared["mime"],
            type="primary",
            use_container_width=True,
        )

st.divider()
st.caption(
    "Use this downloader only for content you own, public-domain material, or media you have permission to download. "
    "Private, DRM-protected, login-only or otherwise restricted media is not bypassed."
)


st.markdown("Developed by [Md Omar Faruk](https://github.com/umar-vai)")
