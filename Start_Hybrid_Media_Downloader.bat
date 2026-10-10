@echo off
setlocal
cd /d "%~dp0"

if not exist ".hybrid-venv\Scripts\python.exe" (
  echo Creating Media Downloader Local Core environment...
  py -3 -m venv .hybrid-venv || python -m venv .hybrid-venv || exit /b 1
)

call ".hybrid-venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check -q -r hybrid_core\requirements.txt || exit /b 1
python -m hybrid_core.launcher
