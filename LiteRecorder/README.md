# Lite Recorder

Tiny Windows audio recorder (microphone + PC sound -> MP3). ~45 KB exe, no install, no extra DLLs.
Uses only what Windows already has (.NET Framework 4, WASAPI, Media Foundation MP3 encoder).

- Build: run `build.bat` (needs nothing but Windows). Output: `LiteRecorder.exe`.
- Copy `LiteRecorder.exe` to any Windows 10/11 PC and double-click it.
- Settings (folder, quality, pin) are remembered in `%APPDATA%\LiteRecorder\settings.ini`.
- If a PC has no MP3 encoder it falls back to WAV automatically.
