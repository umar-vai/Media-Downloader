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

### Remaining Phase 2 work

- Auto-update only the local core when engine changes.
- Package/refine persistent settings separately from the legacy desktop settings.

## Phase 3 — Web editor

Expose local FFmpeg editing through the API:

- probe media
- trim
- crop presets
- rotate
- speed
- mute / volume
- fade in/out
- preview frame
- waveform
- export progress
- cancel export

All source and rendered files remain local.

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
