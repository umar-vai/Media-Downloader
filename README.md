# Media Downloader

Standalone media-downloader project separated from `the original transcriber repository`.

The transcriber repository is now focused only on transcription. Downloader-specific desktop/local/cloud files and the Windows build workflow live here.

## Included apps

### Desktop app

Location: `desktop_downloader/`

- Classic download-manager main window: menu bar, icon toolbar (Add URL, Resume, Stop, Start/Stop All, Delete,
  Delete Completed, Options, Browsers, Editor, Record), a category tree (All Downloads by file type, Unfinished, Finished,
  Queues) and one sortable, multi-select download table with live percent, speed and time left
- Drag finished files straight out of the list into Telegram, WhatsApp, a browser upload box or a folder
  (like IDM / NDM); Ctrl+Shift+C copies the files so Ctrl+V pastes them anywhere
- Explorer-style selection: Ctrl+click picks single rows, Shift+click picks a range, dragging over rows (or
  from the empty area) selects them; Resume, Stop, Download again, Copy and Delete act on every selected row
- Launching the app again while it sits in the system tray brings the open window to the front
- Right-click menu, double-click to open, keyboard shortcuts (Ctrl+N, Ctrl+B, Del, Enter, Alt+Enter, F5, Ctrl+F),
  details pane and status bar; columns can be shown/hidden and resized
- Light theme by default, dark theme in View > Theme or Settings; icons drawn at the screen's real DPI
- Batch download dialog (up to 300 links); each batch shows up under Queues
- Browser integration: the extension hands the browser's own downloads (zip, exe, pdf, mp4...) to the app,
  adds "Download link with Media Downloader" to the right-click menu, and keeps the browser download if the
  app is not running. Alt+click a link to let the browser keep it. Plain files resume after Stop.
- The exported extension pairs itself with the app (no token to copy) and is kept up to date by the app
- Options, Properties, Add URL, Batch, error and delete dialogs use native Windows controls
- YouTube video download
- Audio-only download
- MP3 / M4A output
- Video quality selection
- Custom filenames
- Custom download folder
- Progress, speed and status display
- Portable Windows EXE build through GitHub Actions
- Built-in local video/audio editor
- Edit & Download flow after online media download
- Local file editing for existing media
- Trim/cut range, crop presets and custom crop, rotate, speed, mute/volume and audio fades
- Frame preview plus MP4/MKV/MOV and MP3/M4A/WAV export
- Editor keys work like a pro editor: Space plays/pauses from anywhere except text boxes; Blade tool (B)
  cuts the timeline (or Ctrl+K at the playhead), Selection tool (V) picks parts, Delete removes them.
  Magnetic timeline: a ripple-deleted part disappears and the rest slides together; with ripple off
  (or Shift+Delete) the part becomes a black, silent gap. Works for video and audio files, with
  undo/redo, snapping and a right-click menu (restore, join, clear). Dark, modern editor look
- Screen recorder (Record button, File menu or tray): full screen, a dragged area (with a REC / OK /
  Cancel toolbar on the box, exact width × height and a ratio lock) or a fixed size; live microphone and
  system sound meters; quality,
  frame rate, output size, MP4/MKV, GPU encoders (NVENC, Quick Sync, AMF) when present; system sound and
  microphone (mixed, kept in sync), webcam overlay, cursor and click highlight, countdown, auto-stop timer,
  pause/resume and global hotkeys (F12 start/stop, Shift+F12 pause). Interrupted recordings are recovered
  and a finished recording can open straight in the editor

Run locally:

```bash
python -m pip install -r desktop_downloader/requirements.txt
python desktop_downloader/app.py
```

### Local browser app

Location: `local_downloader/`

A Streamlit app that runs on the user's own computer and writes downloads directly to the local Downloads folder.

On Windows, double-click:

`Start_Local_YouTube_Downloader.bat`

The launcher creates its own virtual environment, installs dependencies and opens the app at `http://127.0.0.1:8765`.

### Standalone Streamlit downloader

Location: `streamlit_downloader/`

This is the web/Streamlit version of the downloader, kept fully separate from the transcriber. It prepares supported audio/video files in temporary server storage and exposes the finished file through a browser download button.

Run it with:

```bash
python -m pip install -r streamlit_downloader/requirements.txt
streamlit run streamlit_downloader/app.py
```

## Windows EXE

The workflow `.github/workflows/build-desktop-downloader.yml` builds `MediaDownloader.exe` and uploads it as a GitHub Actions artifact.

## Project separation

This repository contains downloader functionality only. `the original transcriber repository` remains focused on transcription workflows.

Use this software only for content you own, public-domain media, or content you have permission to download. It is not intended to bypass DRM, private access, login requirements or other platform restrictions.


## Developer

Developed by [Md Omar Faruk](https://github.com/umar-vai).
