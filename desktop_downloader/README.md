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


## v3.9.2 repeated-analysis fix

- Fixed the state where a second URL could show **Analyzing...** but never actually start its worker after a previous thumbnail had been rendered.
- Thumbnail clearing is now failure-safe and can rebuild the preview label instead of aborting the analysis callback.
- The Analyze button is disabled while the same URL is already being analyzed; use **Cancel analysis** to stop it.
- Added a 45-second watchdog so a blocked website cannot leave the app stuck in Analyzing forever.
- Eporner's XHR metadata endpoint is rewritten from HTTP to HTTPS at runtime and Eporner extraction prefers yt-dlp's legacy urllib handler to avoid recurring Windows curl/BoringSSL failures.


## v3.9.3 analyzed-media download reuse

- Downloads now reuse the exact media-info snapshot produced by the successful **Analyze** step instead of immediately re-fetching the webpage and metadata API.
- This fixes the common case where analysis succeeds but a second request made at download time is reset, rate-limited, or fails with SSL/EOF errors.
- The requested Video/Audio quality is still selected at download time from the cached format list.
- If cached direct media URLs fail or expire, the app automatically falls back to the normal URL extraction paths.
- Download Activity now labels those retries as **URL fallback 1/N** so it is clear when the app had to re-contact the website.


## v3.9.4 installed-browser fallback

When normal yt-dlp extraction fails because a site resets Python/OpenSSL connections, the app can now fall back internally to an installed Chromium browser (Chrome, Edge or Brave) without any browser extension.

The fallback launches a temporary headless browser session for the pasted URL, watches network responses for direct video/audio, HLS and DASH media, and returns those media URLs to the normal download queue. The temporary browser profile is deleted when analysis finishes.

This keeps the product workflow unchanged: **paste link → analyze → download → edit**. It does not reintroduce the removed browser extension or overlay system, and it does not bypass DRM.


## v3.9.5 retry failed downloads

Failed items in **Download Activity** now show a **Retry** button.

Retry keeps the original URL, filename, output folder, selected video/audio settings, and cached analyzed media data. The failed item is reset to queued state and immediately starts again when a download slot is available. Existing successful or active downloads are not affected.
