@echo off
setlocal
cd /d "%~dp0.."
echo Building single-file fleetkeeper.exe from repository root: %CD%
python -m pip install --quiet pyinstaller
if errorlevel 1 goto fail
python -m PyInstaller --onefile --console --clean --noconfirm ^
  --name fleetkeeper --paths src ^
  --hidden-import fleetkeeper.ui ^
  --hidden-import fleetkeeper.cli ^
  --hidden-import fleetkeeper.watchdog ^
  scripts\exe_entry.py
if errorlevel 1 goto fail
echo.
echo OK: %CD%\dist\fleetkeeper.exe
echo Double-click it. A browser page opens by itself.
exit /b 0
:fail
echo Build failed. Check the PyInstaller output above.
pause
exit /b 1
