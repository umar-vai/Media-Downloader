# Media Downloader — Desktop App

A Windows desktop app focused on one workflow:

1. Paste a public media URL.
2. Analyze it with yt-dlp.
3. Download video or audio.
4. Optionally open the downloaded file in the built-in editor.

Downloaded files are saved to:

`Downloads/Media Downloader`

You can change the save location from Settings.

## Link support

Any valid `http://` or `https://` URL is accepted by the extraction engine. The app tries:

- the normal yt-dlp extractor;
- browser-like Chrome impersonation when appropriate;
- yt-dlp's generic webpage extractor as a fallback;
- existing Facebook mobile/share transport fallbacks;
- direct media/HLS/DASH URLs when yt-dlp can resolve them.

This broadens compatibility beyond YouTube, Facebook and Instagram.

It is not technically possible to guarantee every website. DRM-protected media, login-only/private media, anti-bot challenges, unsupported JavaScript-only players, expired signed URLs, or sites that yt-dlp does not currently understand can still fail. The app does not bypass DRM or access controls.

## Editing

After download, choose **Edit downloaded media**, or use **Edit local media** to open an existing audio/video file. The editor remains fully local.

## Build

```powershell
pip install -r desktop_downloader/requirements.txt
python desktop_downloader/launcher.py
```

The GitHub Actions workflow builds:

- `MediaDownloader.exe`
- `MediaDownloaderSetup.exe`
- `MediaDownloaderPortable.zip`
- SHA-256 checksum files

## Updates

The app checks GitHub Releases and can download verified updates. Installer mode uses the installer package; portable mode uses the portable executable/update flow.


## Continuous workflow and multiple downloads

Version 3.9.0 separates link analysis from downloads. A completed or active download no longer blocks the next URL from being analyzed.

- **Cancel analysis** stops/invalidate the current analysis immediately.
- Each active/queued download has its own **Cancel** control.
- Up to **3 files download concurrently**; additional files wait in the queue.
- The compact Download button sits beside the analyzed media information.
- Download Activity shows per-file progress, state and errors while you continue analyzing new links.


## v3.9.1 extraction reliability fix

- Fixed the Generic extractor fallback so it is actually passed to yt-dlp's `extract_info(..., force_generic_extractor=True)` call instead of being placed only in the options dictionary.
- Added an Eporner-specific recovery path that tries the canonical embed URL and Generic extraction without curl_cffi impersonation, avoiding observed Windows curl (52) and BoringSSL curl (35) failures.
- Reduced analysis retry latency so failed links return control sooner.
- Fixed an analysis UI race that could show `LINK READY` while the same URL was already being analyzed.
- Failed or cancelled analyses now leave the media card in a clear terminal state instead of continuing to display `Analyzing...`.
