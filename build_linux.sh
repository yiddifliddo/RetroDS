#!/bin/sh
# Builds dist/RetroDS (single file). Needs python3 with tkinter (python3-tk).
set -e
python3 -m pip install --upgrade -r requirements-dev.txt
python3 -m PyInstaller --noconfirm --clean --onefile --windowed --name RetroDS \
  --collect-submodules retrods retrods_launcher.py
echo "Built dist/RetroDS (run with sudo to write SD cards)"
