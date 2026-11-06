@echo off
REM Builds a standalone Windows .exe using PyInstaller.
REM Run this from a Windows machine with Python 3.10+ installed.
REM
REM Does NOT bump the version number -- run bump_version.py yourself
REM first if this build should carry a new version.

cd /d "%~dp0"
echo Working directory: %cd%
echo.

echo Checking for Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: "python" was not found on your PATH.
    echo Install Python 3.10+ from python.org and make sure to check
    echo "Add python.exe to PATH" during installation, then try again.
    pause
    exit /b 1
)
echo.

echo Making this repo's own .venv at the redactor_common pin ^(a minute the first time^)...
REM Never "pip install -r requirements.txt" into the shared Python: one repo's pin then breaks the others (2026-11-04).
python ..\redactor-build-tools\devenv\venv_sync.py .
if errorlevel 1 (
    echo.
    echo ERROR: venv_sync failed. It lives in ..\redactor-build-tools\devenv\ next to this repo;
    echo common causes: that folder is missing, no internet connection, or a proxy/firewall blocking pip.
    pause
    exit /b 1
)
echo.

echo Building cbzredactor.exe ...
REM "python -m PyInstaller" with the .venv's python: the pyinstaller console script is often not on PATH.
REM
REM No UPX (2026-10-30): the spec has upx=False, because Defender
REM flagged the UPX-packed release exe as a trojan (false positive).
.venv\Scripts\python -m PyInstaller cbzredactor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. See the error output above for details.
    echo Common causes: missing Python dependencies or a PyQt6 install
    echo problem.
    pause
    exit /b 1
)
echo.

if not exist "dist\cbzredactor.exe" (
    echo.
    echo ERROR: PyInstaller reported success but dist\cbzredactor.exe
    echo was not found. Please copy the full output above and report it.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo  SUCCESS. Your app is at: %cd%\dist\cbzredactor.exe
echo  That one file can be copied anywhere and run with no Python
echo  install needed.
echo ================================================================
