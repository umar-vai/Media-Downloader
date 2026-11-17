# Media Downloader — Desktop App

A Windows desktop app focused on one workflow:

1. Paste a public media URL.
2. Analyze it with yt-dlp.
3. Download video or audio.
4. Optionally open the downloaded file in the built-in editor.

Downloaded files are saved to:

`Downloads/Media Downloader`

You can change the save location from Settings.

## v4.5.0: screen recorder fixes, magnetic timeline, dark editor

- Screen recorder: Stop no longer hangs. The step that adds the sound to the video could run forever with the ffmpeg build the app ships, so the recording never finished and the app froze. Every part now has a known length, and a stuck step is stopped instead of blocking.
- Select area: as soon as a box is drawn, a toolbar appears on its top corner with ● REC (start now), OK and Cancel, exact width × height fields, a ratio lock (16:9, 9:16, 4:3, 1:1, 21:9...) and a full-screen button. No more Enter or double-click needed. The inside of the box stays clear while choosing.
- While recording, the bar on the box's corner shows the time, the size, live microphone and system sound meters, Pause/Resume and Stop.
- Sound section: live level meters for the microphone and system sound before recording starts, so you can see the microphone hears you.
- Media editor: magnetic timeline. A deleted part disappears and the parts after it slide over to close the hole (like Premiere ripple delete or CapCut). "Delete, leave gap" shows empty space. Clips are shown as blocks with thumbnails, a real time ruler and a red playhead.
- Media editor: new dark, modern look (dark panels, blue accent buttons, dark tabs and fields).

## v4.4.3 — screen recorder and blade cutting

- Media editor: Space plays and pauses from anywhere in the editor except text boxes, also right after clicking the video.
- Blade tool (B) cuts the timeline, Ctrl+K cuts at the playhead; Selection tool (V) picks parts and Delete removes them. Ripple delete closes the gap; with it off (or Shift+Delete) the part becomes a black, silent gap. Works for video and audio, with undo/redo and snapping.
- New screen recorder (Record button, File menu or tray): full screen, a dragged area or a fixed size; quality, frame rate, size, MP4/MKV and GPU encoders; system sound and microphone in sync; webcam overlay, cursor and click highlight, countdown, auto-stop, pause/resume and F12 / Shift+F12 hotkeys. Interrupted recordings are recovered.

## v4.4.2 — partial file cleanup

- Removing a cancelled, failed or running download from the list now also deletes its leftover `.part`, `.ytdl` and fragment files.
- New Downloads > Clean up partial files... command removes old leftovers from your download folders; downloads you can still resume are kept.

## v4.4.1 — visual editor crop and transform tools

- Crop directly on the video preview: draw, move and resize the crop box; use ratio presets or a freeform custom shape.
- Add crop presets for 4:3, 3:4, 3:2, 2:3 and 21:9, with keyboard nudging and Enter/Escape controls.
- Flip horizontally or vertically and rotate in 90-degree steps.
- Adjust brightness, contrast and saturation with live preview and matching export filters.
- Change playback/export speed from 0.25x to 4x while preserving audio pitch.
- Export at the original size or downscale to 2160p, 1440p, 1080p, 720p, 480p or 360p without upscaling.
- Save the edited frame under the playhead as PNG or JPG; all new edits participate in undo/redo.

## v4.4.0 — download manager workflow

- Drag completed downloads from the list into Explorer, Telegram, WhatsApp or browser upload areas; copy selected files with Ctrl+Shift+C.
- Use Explorer-style Ctrl/Shift multi-selection, drag selection and bulk context-menu actions.
- Launching the app while it is already in the tray now restores the running window.

## v3.11.3 — tray-safe updates and single-instance launch

- Normal window Close continues to hide to the system tray; background downloads
  remain active, as before.
- Restart & Update now requests a real app shutdown (including the tray icon)
  instead of destroying only its window, which prevented the updater from
  replacing a locked executable.
- If transfers or queued jobs exist, the app asks before stopping them for an
  update. Finish important downloads before updating; the desktop queue does not
  survive a restart.
- Windows allows only one Media Downloader GUI instance per logged-in session,
  preventing multiple tray icons after repeated app launches.
- Each update handoff uses its own updater executable to avoid collisions with
  earlier failed updater processes; stale copies are cleaned up on later startup.
- For users of versions 3.11.1/3.11.2 whose auto-update already loops, the
  *one-time recovery* is to finish active downloads, Quit all old tray icons,
  then manually run MediaDownloaderSetup.exe (installed mode) or replace the
  portable EXE using the verified official 3.11.3 release. Subsequent updates
  can use the fixed Restart & Update flow.

## v3.11.2 — batch concurrency and useful link diagnostics

- Batch mode now defaults to **6 simultaneous downloads** rather than 3, with a
  selectable **1–10** limit under Settings → Downloads.
- New slots are filled as soon as the setting increases; reducing it never
  interrupts an already-running download.
- Failed page extraction reports a likely cause (rate limit, access restriction,
  missing stream, expired link, or network failure) instead of only one generic
  message. The hint is diagnostic, not a guarantee that any particular website
  supports downloading.
- 50 URLs may still be queued in one batch. The concurrency limit controls how
  many workers are active at once, not how many links may be submitted.
- Large parallel batches can increase HTTP 429 failures. If this happens, lower
  the setting to 2–3 and retry failed items.

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

- **Crop on video**: press **✂ Crop** (or `C`, or double-click the preview) and drag the box drawn on the
  video. Drag inside to move it, drag a corner or edge to resize it, or drag outside it to draw a new box.
  Pick a size (16:9, 9:16, 1:1, 4:5, 4:3, 3:4, 3:2, 2:3, 21:9) to lock the shape, or **Custom** for any
  shape. Arrow keys nudge the box, Enter applies it and Esc cancels. The X/Y/W/H numbers stay available.
- **Rotate and flip**: rotate in 90° steps and flip horizontally or vertically.
- **Color**: brightness, contrast and saturation, shown live in the preview.
- **Speed**: 0.25× to 4×, with audio kept in pitch.
- **Output size**: export at the source size or scale down to 2160p, 1440p, 1080p, 720p, 480p or 360p.
- **Save current frame**: save the edited frame under the playhead as PNG or JPG.

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


## v3.9.6 Desktop Batch Download

The original CustomTkinter Windows desktop EXE now includes **Batch Download**, accessible from
the top toolbar. This is part of the desktop `v3.9.x` series, not the separate Web/PWA Local Core.

- Paste up to 50 HTTP(S) URLs, one per line. Duplicate URLs in the same list are skipped.
- Select Video/Audio mode and output quality once for the batch.
- Items enter the same existing download queue, with up to 3 concurrent analyze/download workers.
- Each URL is analyzed separately in its worker before download. Successful analysis supplies its
  source title and cached media format data to the shared downloader.
- Every file has a unique batch-ID and position prefix to avoid title collisions.
- The separate Batch Download window shows each item's status/progress, per-item Cancel/Retry,
  overall batch completion counts, and Cancel Remaining/Retry Failed actions.
- One failed link does not stop the other queued links; completed downloads are still recorded
  in normal Download History and remain available to the editor.
- A maximum of 150 pending/running jobs prevents queue overload.

**Note:** In the v3.9.6 Desktop branch, batch job state is in memory while the EXE is running.
Completed files and Download History persist, but unfinished batch jobs are not automatically
resumed after closing/restarting the desktop app. Individual download success still depends
on the site's compatible non-DRM media extraction support.
