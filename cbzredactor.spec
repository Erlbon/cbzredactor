# PyInstaller spec file for The ƆBZ Redactor.
#
# Build on Windows with: pyinstaller cbzredactor.spec
# (PyInstaller builds for whatever platform it runs ON -- cannot be
# cross-compiled from Linux/macOS to produce a Windows .exe.)
#
# redactor_common is not listed in datas/hiddenimports -- it's a plain
# importable Python package installed via pip, same as PyQt6/lxml, so
# PyInstaller's static import analysis picks it up automatically.

block_cipher = None

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
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="cbzredactor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
