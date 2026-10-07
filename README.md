# Media Downloader

Standalone media-downloader project separated from `the original transcriber repository`.

The transcriber repository is now focused only on transcription. Downloader-specific desktop/local/cloud files and the Windows build workflow live here.

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
- Built-in local video/audio editor
- Edit & Download flow after online media download
- Local file editing for existing media
- Trim/cut range, crop presets and custom crop, rotate, speed, mute/volume and audio fades
- Frame preview plus MP4/MKV/MOV and MP3/M4A/WAV export

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
