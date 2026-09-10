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

echo Installing dependencies ^(this can take a minute the first time^)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Common causes: no internet connection,
    echo or a proxy/firewall blocking pip. See the error above for details.
    pause
    exit /b 1
)
echo.

echo Building cbzredactor.exe ...
REM "python -m PyInstaller" instead of the bare "pyinstaller" command --
REM pip installs the pyinstaller console script into a "Scripts" folder
REM that often isn't on PATH, especially for a per-user (non-admin)
REM Python install. "python -m" always finds it as long as it's
REM installed in this same Python environment.
REM
REM The spec's upx=True is a silent no-op unless upx.exe is actually
REM findable -- prepend the shared copy so PyInstaller's own DLL
REM compression pass (CFG-protected DLLs are auto-skipped by
REM PyInstaller itself) actually runs instead of doing nothing.
set "PATH=%~dp0..\_shared-tools\upx;%PATH%"
python -m PyInstaller cbzredactor.spec --noconfirm
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
pause
