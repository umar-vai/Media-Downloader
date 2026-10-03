from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {'.py', '.md', '.bat', '.yml', '.yaml', '.txt'}

replacements = [
    ('Team Fahad Local YouTube Downloader', 'Media Downloader'),
    ('Team Fahad YouTube Downloader', 'Media Downloader'),
    ('Team Fahad Media Downloader', 'Media Downloader'),
    ('TEAM FAHAD AI STUDIO · LOCAL MODE', 'MEDIA DOWNLOADER · LOCAL MODE'),
    ('TEAM FAHAD MEDIA TOOLKIT', 'MEDIA DOWNLOADER'),
    ('TEAM FAHAD', 'MEDIA DOWNLOADER'),
    ('Team Fahad YouTube', 'Media Downloader'),
    ('TeamFahadDownloader', 'MediaDownloader'),
    ('TeamFahadUpdater', 'MediaDownloaderUpdater'),
    ('TeamFahadYouTubeDownloader', 'MediaDownloader'),
    ('team-fahad-transcriber', 'the original transcriber repository'),
]

for path in ROOT.rglob('*'):
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    if '.git' in path.parts or path.name == 'rebrand_v221.py':
        continue
    text = path.read_text(encoding='utf-8')
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if path.name == 'app.py' and path.parent.name == 'desktop_downloader':
        text = text.replace('text="TF",', 'text="MD",')
        text = text.replace('text="MEDIA TOOLKIT",', 'text="VIDEO • AUDIO",')
        text = text.replace('text="YouTube Downloader",', 'text="Media Downloader",')
        marker = 'CONFIG_FILE = CONFIG_DIR / "settings.json"\n'
        if marker in text and 'LEGACY_CONFIG_FILE' not in text:
            text = text.replace(marker, marker + 'LEGACY_CONFIG_DIR = Path(os.getenv("APPDATA") or Path.home()) / ("Team" + "Fahad" + "Downloader")\nLEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "settings.json"\n')
        old_load = '''def load_settings() -> dict[str, Any]:\n    settings = default_settings()\n    try:\n        payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))\n        if isinstance(payload, dict):\n            settings.update(payload)\n    except Exception:\n        pass\n    return settings\n'''
        new_load = '''def load_settings() -> dict[str, Any]:\n    settings = default_settings()\n    source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE\n    try:\n        payload = json.loads(source.read_text(encoding="utf-8"))\n        if isinstance(payload, dict):\n            settings.update(payload)\n            if source == LEGACY_CONFIG_FILE and not CONFIG_FILE.exists():\n                save_settings(settings)\n    except Exception:\n        pass\n    return settings\n'''
        text = text.replace(old_load, new_load)
        old_footer = '''        ctk.CTkLabel(\n            footer,\n            text="Use only for content you own or have permission to download.",\n            text_color="#637696",\n            font=("Segoe UI", 9),\n        ).grid(row=0, column=1, sticky="e")\n'''
        new_footer = '''        ctk.CTkLabel(\n            footer,\n            text="Use only for content you own or have permission to download.",\n            text_color="#637696",\n            font=("Segoe UI", 9),\n        ).grid(row=0, column=1, sticky="e")\n        ctk.CTkButton(\n            footer,\n            text="Developed by Md Omar Faruk  •  GitHub ↗",\n            width=220,\n            height=26,\n            fg_color="transparent",\n            hover_color=SURFACE_2,\n            text_color=CYAN,\n            font=("Segoe UI", 9),\n            command=lambda: webbrowser.open("https://github.com/umar-vai"),\n        ).grid(row=1, column=1, sticky="e", pady=(5, 0))\n'''
        text = text.replace(old_footer, new_footer)
    if path.name == 'app.py' and path.parent.name in {'local_downloader', 'streamlit_downloader'}:
        if 'Developed by Md Omar Faruk' not in text:
            text += '\n\nst.markdown("Developed by [Md Omar Faruk](https://github.com/umar-vai)")\n'
    if path.name == 'README.md' and path.parent == ROOT:
        text = re.sub(r'^# .*Media Downloader.*$', '# Media Downloader', text, count=1, flags=re.M)
        if '## Developer' not in text:
            text += '\n\n## Developer\n\nDeveloped by [Md Omar Faruk](https://github.com/umar-vai).\n'
    if text != original:
        path.write_text(text, encoding='utf-8')

# Version bump
version_file = ROOT / 'desktop_downloader' / 'version.py'
version_file.write_text('APP_VERSION = "2.2.1"\n', encoding='utf-8')

# Update manager: prefer the new neutral asset, but retain an internal bridge for v2.2.0 users.
manager = ROOT / 'desktop_downloader' / 'update_manager.py'
text = manager.read_text(encoding='utf-8')
text = re.sub(
    r'APP_ASSET_NAME = .*?\nCHECKSUM_ASSET_NAME = .*?\nUSER_AGENT = .*?\n',
    'APP_ASSET_NAME = "MediaDownloader.exe"\nLEGACY_APP_ASSET_NAME = "Team" + "Fahad" + "YouTubeDownloader.exe"\nASSET_CANDIDATES = (APP_ASSET_NAME, LEGACY_APP_ASSET_NAME)\nCHECKSUM_ASSET_NAME = f"{APP_ASSET_NAME}.sha256"\nUSER_AGENT = "MediaDownloader-Updater/1.0"\n',
    text,
    count=1,
)
old = '''    asset_url = assets.get(APP_ASSET_NAME, "")\n    checksum_url = assets.get(CHECKSUM_ASSET_NAME, "")\n    if not asset_url:\n        raise UpdateError(f"Release {tag_name} is missing {APP_ASSET_NAME}.")\n    if not checksum_url:\n        raise UpdateError(f"Release {tag_name} is missing the SHA-256 checksum file.")\n'''
new = '''    selected_name = next(\n        (name for name in ASSET_CANDIDATES if assets.get(name) and assets.get(f"{name}.sha256")),\n        "",\n    )\n    if not selected_name:\n        raise UpdateError(f"Release {tag_name} is missing a supported Media Downloader executable/checksum pair.")\n    asset_url = assets[selected_name]\n    checksum_url = assets[f"{selected_name}.sha256"]\n'''
text = text.replace(old, new)
manager.write_text(text, encoding='utf-8')

# Regular workflow: neutral build names + one legacy release alias so installed v2.2.0 can cross the rename safely.
workflow = ROOT / '.github' / 'workflows' / 'build-desktop-downloader.yml'
text = workflow.read_text(encoding='utf-8')
text = text.replace('--name MediaDownloader `\n            desktop_downloader/updater.py', '--name MediaDownloaderUpdater `\n            desktop_downloader/updater.py')
text = text.replace('--name MediaDownloader `\n            --collect-all yt_dlp', '--name MediaDownloader `\n            --collect-all yt_dlp')
text = text.replace('--add-binary "dist/MediaDownloaderUpdater.exe;."', '--add-binary "dist/MediaDownloaderUpdater.exe;."')
# Replace checksum/upload/release section wholesale from checksum step onward.
prefix = text.split('      - name: Generate SHA-256 checksum\n', 1)[0]
tail = '''      - name: Generate SHA-256 checksums and compatibility alias\n        shell: pwsh\n        run: |\n          $hash = (Get-FileHash "dist/MediaDownloader.exe" -Algorithm SHA256).Hash.ToLower()\n          "$hash  MediaDownloader.exe" | Out-File "dist/MediaDownloader.exe.sha256" -Encoding ascii\n          Copy-Item "dist/MediaDownloader.exe" "dist/TeamFahadYouTubeDownloader.exe"\n          "$hash  TeamFahadYouTubeDownloader.exe" | Out-File "dist/TeamFahadYouTubeDownloader.exe.sha256" -Encoding ascii\n\n      - name: Upload Windows build artifact\n        uses: actions/upload-artifact@v4\n        with:\n          name: MediaDownloader-Windows\n          path: |\n            dist/MediaDownloader.exe\n            dist/MediaDownloader.exe.sha256\n            dist/TeamFahadYouTubeDownloader.exe\n            dist/TeamFahadYouTubeDownloader.exe.sha256\n          if-no-files-found: error\n          retention-days: 30\n\n      - name: Publish GitHub Release assets\n        if: startsWith(github.ref, 'refs/tags/v')\n        uses: softprops/action-gh-release@v2\n        with:\n          generate_release_notes: true\n          files: |\n            dist/MediaDownloader.exe\n            dist/MediaDownloader.exe.sha256\n            dist/TeamFahadYouTubeDownloader.exe\n            dist/TeamFahadYouTubeDownloader.exe.sha256\n'''
workflow.write_text(prefix + tail, encoding='utf-8')

# Desktop README build command/output names.
readme = ROOT / 'desktop_downloader' / 'README.md'
text = readme.read_text(encoding='utf-8')
text = text.replace('TeamFahadYouTubeDownloader.exe', 'MediaDownloader.exe')
text = text.replace('TeamFahadUpdater.exe', 'MediaDownloaderUpdater.exe')
readme.write_text(text, encoding='utf-8')

print('Rebrand v2.2.1 applied.')
