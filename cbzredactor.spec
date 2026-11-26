# PyInstaller spec file for The ƆBZ Redactor.
#
# Build on Windows with: build_exe.bat (both programs and the installer), or
#   .venv/Scripts/python -m PyInstaller cbzredactor.spec --noconfirm
# (PyInstaller builds for whatever platform it runs ON -- cannot be
# cross-compiled from Linux/macOS to produce a Windows .exe. The Linux
# download is built from this same spec by redactor-build-tools.)
#
# redactor_common is not listed in datas/hiddenimports -- it's a plain
# importable Python package installed via pip, same as PyQt6/lxml, so
# PyInstaller's static import analysis picks it up automatically.

import os
import sys

block_cipher = None

# CBZREDACTOR_BUILD=folder: the one-folder build the installer ships (dist/cbzredactor/cbzredactor.exe and its
# _internal folder), which starts faster than the one-file exe (that one unpacks itself at every start). The default
# stays the one-file dist/cbzredactor.exe: the portable download, and the file a bare copy's Check for Updates swaps.
FOLDER = os.environ.get("CBZREDACTOR_BUILD", "") == "folder"

# The update framework's channel (redactor_common docs/update-framework.md): how this copy updates itself. The one-file
# exe carries "windows-exe" inside (it swaps itself), the Linux one-file build "linux-tarball" (it cannot replace itself
# and says so); the folder build gets "windows-zip" beside its exe below, which the installer's contract replaces with
# "windows-installer".
CHANNEL = os.path.join(workpath, "update-channel")
if not FOLDER:
    os.makedirs(workpath, exist_ok=True)
    with open(CHANNEL, "w", encoding="utf-8") as channel:
        channel.write("windows-exe\n" if sys.platform == "win32" else "linux-tarball\n")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/icon.ico", "assets"),
        ("README.md", "."),
        ("CHANGELOG.md", "."),
        ("ABOUT.md", "."),
        ("CREDITS.md", "."),
    ] + ([] if FOLDER else [(CHANNEL, ".")]),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # numpy is not used by this app; without this PyInstaller bundles it whenever it is installed on
    # the build machine (2026-10-14: +8 MB for nothing).
    excludes=["numpy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    *([] if FOLDER else [a.binaries, a.zipfiles, a.datas]),
    [],
    exclude_binaries=FOLDER,
    name="cbzredactor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX off (2026-10-30): Defender flagged the UPX-packed, unsigned release
    # exe as Trojan:Win32/Yomal!rfn. A larger exe beats one that is quarantined.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app -- no console popup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

if FOLDER:
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, upx_exclude=[], name="cbzredactor")
    with open(os.path.join(DISTPATH, "cbzredactor", "update-channel"), "w", encoding="utf-8") as channel:
        channel.write("windows-zip\n")
