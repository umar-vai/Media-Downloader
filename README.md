# Team Fahad Media Downloader

Standalone media-downloader project separated from `team-fahad-transcriber`.

The transcriber repository is now focused only on transcription. Downloader-specific desktop/local files and the Windows build workflow live here.

## Included apps

### Desktop app

Location: `desktop_downloader/`

- Native Windows-style CustomTkinter UI
- YouTube video download
- Audio-only download
- MP3 / M4A output
- Video quality selection
- Custom filenames
- Custom download folder
- Progress, speed and status display
- Portable Windows EXE build through GitHub Actions

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

## Windows EXE

The workflow `.github/workflows/build-desktop-downloader.yml` builds `TeamFahadYouTubeDownloader.exe` and uploads it as a GitHub Actions artifact.

## Project separation

This repository should contain downloader functionality only. `team-fahad-transcriber` should remain focused on transcription workflows.

Use this software only for content you own, public-domain media, or content you have permission to download. It is not intended to bypass DRM, private access, login requirements or other platform restrictions.
