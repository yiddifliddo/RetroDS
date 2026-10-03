@echo off
REM Builds dist\RetroDS.exe (single file, needs Python 3.10+ with tkinter installed).
REM Run from a normal command prompt inside the RetroDS folder.
python -m pip install --upgrade -r requirements-dev.txt || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name RetroDS ^
  --uac-admin ^
  --collect-submodules retrods ^
  retrods_launcher.py || exit /b 1
echo.
echo Built dist\RetroDS.exe
