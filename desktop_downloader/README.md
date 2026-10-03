# Team Fahad YouTube Downloader — Desktop App

A portable Windows desktop build of the local downloader. By default, files are saved to:

`Downloads/Team Fahad YouTube`

The desktop app includes a **Save Location** control. Use **Choose folder** to select any folder or drive. The selected folder is remembered for future launches. Use **Default** to switch back to the standard Team Fahad download folder.

## Build locally

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r desktop_downloader\requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name TeamFahadYouTubeDownloader --collect-all yt_dlp --collect-all imageio_ffmpeg --collect-all customtkinter --collect-all PIL desktop_downloader\app.py
```

The EXE will be created at:

`dist/TeamFahadYouTubeDownloader.exe`

## GitHub Actions build

The workflow `.github/workflows/build-desktop-downloader.yml` builds a portable Windows EXE automatically whenever desktop downloader files change.

Use the downloader only for content you own or have permission to download.
