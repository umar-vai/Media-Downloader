# Media Downloader — Desktop App

A portable Windows desktop media downloader for public YouTube, Facebook and Instagram videos/reels. By default, downloaded media is saved to:

`Downloads/Media Downloader`

The desktop app includes a **Save Location** control. Use **Choose folder** to select any folder or drive. The selected folder is stored in `%APPDATA%\MediaDownloader\settings.json` and is preserved across app updates.

## Supported platforms

- YouTube videos and Shorts
- Facebook public videos and Reels
- Instagram public videos and Reels

The app automatically detects the platform from the pasted URL. Facebook and Instagram requests use yt-dlp browser impersonation via curl_cffi for more reliable TLS/network compatibility. Private, friends-only, login-required, DRM-protected or otherwise access-restricted media is not bypassed. Platform changes can occasionally require a newer `yt-dlp` release.

## Auto-update system

Starting with **v2.2.0**, the Windows EXE can update itself through GitHub Releases.

Update flow:

1. The app checks the repository's latest GitHub Release in the background.
2. If the release version is newer than the installed version, an in-app update notice appears.
3. **Update Now** downloads `MediaDownloader.exe`.
4. The matching `.sha256` release asset is downloaded and verified before installation.
5. The bundled `MediaDownloaderUpdater.exe` is copied to the local update folder and launched.
6. The main app closes.
7. The updater stages the new EXE, backs up the current EXE, replaces it, and launches the new build.
8. If the new build exits immediately, the updater restores and relaunches the previous executable.

Update logs are written to:

`%APPDATA%\MediaDownloader\update.log`

Temporary update downloads are written under:

`%LOCALAPPDATA%\MediaDownloader\updates\`

User settings and downloaded media are not stored inside the EXE, so an update does not reset the selected download folder or other update preferences.

### Update preferences

The app includes:

- **Automatically check for updates** — enabled by default.
- **Automatically download updates** — disabled by default.
- **Check for updates** — manual check at any time.
- **Later** — snoozes the update reminder for 24 hours.
- **View changes** — opens the current GitHub Release page.

Automatic installation is only enabled in the packaged Windows EXE. Running `app.py` directly can check/download an update, but it will not replace source files.

## Versioning

The single source of truth is:

`desktop_downloader/version.py`

Example:

```python
APP_VERSION = "2.2.3"
```

Use semantic versions such as:

- `2.2.0` — feature release
- `2.2.1` — bug-fix release
- `3.0.0` — major release

The release tag must match `APP_VERSION`. The GitHub Actions workflow checks this automatically.

## Build locally

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r desktop_downloader\requirements.txt

pyinstaller --noconfirm --clean --onefile --windowed `
  --name MediaDownloaderUpdater `
  desktop_downloader\updater.py

pyinstaller --noconfirm --clean --onefile --windowed `
  --name MediaDownloader `
  --collect-all yt_dlp `
  --collect-all imageio_ffmpeg `
  --collect-all customtkinter `
  --collect-all PIL `
  --add-binary "dist/MediaDownloaderUpdater.exe;." `
  desktop_downloader\app.py
```

The distributable EXE is:

`dist/MediaDownloader.exe`

The updater is embedded inside that main EXE, so users still receive one portable application file.

## GitHub Actions build

`.github/workflows/build-desktop-downloader.yml` now:

- validates Python files;
- runs updater unit tests;
- builds the updater;
- embeds it in the main EXE;
- creates a SHA-256 checksum;
- uploads a normal Actions artifact on pushes to `main`;
- publishes the EXE + checksum as GitHub Release assets when a `v*` tag is pushed.

## Publishing a future update

### 1. Change the version

Edit:

`desktop_downloader/version.py`

For example:

```python
APP_VERSION = "2.3.0"
```

### 2. Commit and push

```powershell
git add .
git commit -m "Release v2.3.0"
git push origin main
```

### 3. Tag the exact same version

```powershell
git tag v2.3.0
git push origin v2.3.0
```

GitHub Actions will build the Windows EXE, generate the SHA-256 file, and publish both files to the `v2.3.0` GitHub Release.

Installed copies of an older version will detect that release the next time they perform an update check.

## First auto-update-enabled release

Because builds before v2.2.0 do not contain the updater logic, users must install/download **v2.2.0 once**. After they are running v2.2.0 or newer, later GitHub Releases can be installed from inside the app.

## Security and rollback

The app will not install a release if:

- the expected EXE asset is missing;
- the SHA-256 asset is missing;
- the download is empty;
- SHA-256 verification fails;
- the current process does not close;
- the updater cannot safely stage/replace the executable.

The old EXE is backed up before replacement. If the newly launched EXE exits immediately, the updater restores the backup.

Use the downloader only for content you own or have permission to download.

### Social format fallback

Facebook and Instagram downloads prefer the selected quality, but if that exact resolution/stream is unavailable the app automatically falls back to the best compatible combined video or merged stream instead of failing with a requested-format error.

### Facebook connection fallback

Facebook public videos/Reels automatically retry through the mobile watch endpoint, IPv4, standard yt-dlp TLS, and Chrome/curl_cffi transport when a network terminates one Facebook TLS path early. Instagram keeps the standard transport that is more reliable on the tested Windows network.
