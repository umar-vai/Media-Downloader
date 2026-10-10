# Media Downloader — Web/PWA + Lightweight Local Core Roadmap

## Goal

Replace the all-in-one desktop UI with a browser/PWA interface while keeping download, FFmpeg, filesystem, queue and browser-resolver work on the user's own computer.

Target user flow:

```
Start Media Downloader Core
        ↓
Browser opens http://127.0.0.1:38477
        ↓
Paste URL → Analyze → Download → Local folder
        ↓
Edit local/downloaded media (Phase 3)
```

No browser extension, overlay button or pairing flow is part of this architecture.

## Architecture

```
┌───────────────────────────────┐
│ Web/PWA UI                    │
│ HTML/CSS/JS                   │
│ installable from localhost    │
└───────────────┬───────────────┘
                │ same-origin HTTP API
                ▼
┌───────────────────────────────┐
│ Lightweight Local Core        │
│ FastAPI on 127.0.0.1 only     │
│ analysis / queue / settings   │
└───────┬────────┬────────┬─────┘
        │        │        │
        ▼        ▼        ▼
      yt-dlp   FFmpeg   Local filesystem
        │
        ▼
 Installed-browser resolver fallback
```

The browser UI never uploads the user's media to a cloud server in local mode.

## Security model

- Core binds only to `127.0.0.1`.
- State-changing API calls require an ephemeral per-process `X-Media-Core-Key`.
- CORS is not enabled.
- The PWA and API are same-origin.
- A random key is created on every core launch and is not written to disk.
- No remote page can read the bootstrap response cross-origin or send the required custom header without a successful preflight.

## Phase 0 — Architecture freeze ✅

- Keep the existing desktop app available during migration.
- Create a new `hybrid_core/` and `web_pwa/` side-by-side.
- Do not reintroduce the removed browser-extension system.
- Reuse the proven downloader/extractor/browser-resolver modules during migration.

## Phase 1 — Working local PWA MVP ✅ (initial implementation)

- Local FastAPI core.
- Installable PWA shell.
- Analyze URL.
- Cancel analysis.
- Download video/audio.
- Up to 3 concurrent downloads.
- Per-download progress.
- Cancel failed/running work.
- Retry failed downloads.
- Save directly to a local folder.
- Reuse analyzed media data before re-contacting a website.
- Installed Chromium resolver remains available as an extraction fallback.

## Phase 2 — Core extraction/refactor 🟡

### Phase 2A — Shared media engine ✅

- Moved reusable extraction/downloading logic out of the legacy desktop UI into a dedicated shared `media_core` package.
- Legacy desktop and Hybrid/PWA now call the same analysis/download workers.
- Desktop compatibility modules are thin import shims; extraction logic has one source of truth.
- Shared engine has its own CI/unit tests.

### Phase 2B — Persistence + local system integration ✅

- Download activity/history is persisted to an atomic JSON state file across Local Core restarts.
- Interrupted queued/running downloads are restored as failed/retryable items instead of disappearing.
- Retry after restart refreshes media information when the old signed/cached format data is no longer in memory.
- Added a native Windows folder picker endpoint and PWA **Choose folder** control.
- Added structured rotating JSONL logs and a diagnostics API/PWA panel.
- Diagnostics expose core version, Python/platform, proxy route, installed-browser resolver, FFmpeg, state path and job counts.

### Phase 2C — Independent settings + updater framework ✅

- Added `hybrid-settings.json`, fully separate from the legacy desktop settings file.
- PWA defaults now persist for download folder, media defaults, concurrency, update channel and startup behavior.
- Local Core v0.3.0 reads startup settings before opening the browser or creating its worker pool.
- Added an independent `core-vX.Y.Z` GitHub release channel so desktop `vX.Y.Z` releases cannot be mistaken for Local Core updates.
- Added background update check/download APIs and PWA controls.
- Core update downloads require a matching SHA-256 asset and are staged under the Local Core update directory.
- Automatic replacement/apply is intentionally deferred to Phase 4, where the standalone `MediaDownloaderCore.exe` agent can safely restart itself.

### Phase 2 status

Core extraction/refactor is functionally complete. The next major block is Phase 3 (Web editor), while Phase 4 will turn the Python Local Core into the small installable Windows agent.

## Phase 3 — Web editor 🟡

### Phase 3A — Local FFmpeg editor MVP ✅

The PWA now exposes the first end-to-end local editor workflow:

- choose a local video/audio file with the native Windows file picker
- open any completed download directly in the editor
- probe duration, video/audio streams, dimensions and FPS
- trim start/end
- crop presets: Original, 16:9, 9:16, 1:1 and 4:5
- rotate 0° / 90° / 180° / 270°
- speed 0.5× through 2×
- mute or set volume up to 200%
- audio fade in/out
- High / Balanced / Small video export quality
- FFmpeg preview frame with current crop/rotation
- audio waveform rendering
- collision-safe local export filenames
- live FFmpeg export progress
- cancel a running export and delete the incomplete part file

All source and rendered files remain local. Editor work runs in a dedicated single-worker pool so a heavy export does not block link analysis/download queue workers.

### Phase 3B — Timeline, playable proxy, custom crop + export retry ✅

- Added synchronized trim start/end range sliders while retaining exact numeric entry.
- Added timeline selection summary showing start, end and selected duration.
- Added temporary 6-second H.264/AAC proxy generation for in-browser playback with the active crop, rotation, speed, mute and volume settings.
- Added freeform custom crop X/Y/width/height controls with source-bound validation.
- Added editor export history for the current Local Core session.
- Failed editor exports can be retried in-place; active exports can be cancelled from history and completed exports can be loaded back into the editor.
- Local Core bumped to v0.5.0.

### Remaining Phase 3 work

- richer audio controls / equalizer presets
- reusable editor presets
- persistent editor project/export history across Local Core restarts
- optional visual crop overlay / drag handles

## Phase 4 — Lightweight Windows agent

- Build `MediaDownloaderCore.exe` without the old CustomTkinter UI.
- Optional tray icon: Open App / Open Downloads / Restart Core / Quit.
- Start Menu shortcut.
- Optional launch at Windows sign-in.
- Single-instance lock.
- Automatic local-core update.
- Open the PWA automatically.

## Phase 5 — Production hardening

- Resume/recover queued downloads after restart.
- Persistent retry metadata.
- Better signed-URL refresh.
- Download speed / ETA metrics.
- Rate limiting and disk-space checks.
- Integration tests for Analyze → Download → Retry → Cancel.
- Crash recovery.
- Signed release artifacts.

## Phase 6 — Optional remote UI channel

Only if needed later, the UI bundle can be versioned remotely while the API and media work stay local. The default design remains local-first to avoid server bandwidth/storage costs.

## Current migration rule

Do not delete the existing `desktop_downloader/` yet. It remains the fallback/reference implementation until the local-core PWA reaches feature parity and passes regression testing.
