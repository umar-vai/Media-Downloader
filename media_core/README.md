# media_core

Shared extraction and download engine used by both the legacy desktop UI and the Web/PWA Local Core during migration.

This package is the single source of truth for URL detection, proxy routing, yt-dlp extraction attempts, installed-browser fallback, analyzed-media reuse, cancellation/progress callbacks, and video/audio download execution.

UI code should not implement its own yt-dlp worker.
