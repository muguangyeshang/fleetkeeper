@echo off
setlocal
cd /d "%~dp0.."
echo Building single-file fleetkeeper.exe from repository root: %CD%
python -m pip install --quiet pyinstaller
if errorlevel 1 goto fail
REM fleetkeeper.spec is checked in and is the single source of truth for the
REM module list (ui, cli, watchdog, discover). Using the spec here keeps the
REM batch build byte-for-byte equivalent to the CI/manual PyInstaller build.
python -m PyInstaller --clean --noconfirm fleetkeeper.spec
if errorlevel 1 goto fail
echo.
echo OK: %CD%\dist\fleetkeeper.exe
echo Double-click it. A browser page opens by itself.
exit /b 0
:fail
echo Build failed. Check the PyInstaller output above.
pause
exit /b 1
