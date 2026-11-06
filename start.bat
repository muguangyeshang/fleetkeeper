@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.8+ is required. Download: https://www.python.org/downloads/
  echo Or just double-click dist\fleetkeeper.exe instead.
  pause
  exit /b 1
)
python -c "import fleetkeeper" >nul 2>nul
if errorlevel 1 (
  echo Installing from the local wheel...
  for %%F in (dist\*.whl) do python -m pip install --quiet --force-reinstall "%%F"
)
python -c "import fleetkeeper" >nul 2>nul
if errorlevel 1 (
  echo No installed package and no wheel in dist\. Run: python -m pip install -e .
  pause
  exit /b 1
)
python -m fleetkeeper.cli start
if errorlevel 1 pause
