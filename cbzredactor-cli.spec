# PyInstaller spec file for the CBZ Redactor's command-line tool (cbzredactor-cli.exe).
#
# A console build: the windowed cbzredactor.exe cannot print to a terminal. Same code as the app's core
# (the engines, settings, redactor_common) with no windows, so the GUI parts of Qt are left out.
# Built by build_exe.bat after the app itself.

block_cipher = None

a = Analysis(
    ["cbzredactor_cli.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt6.QtWidgets", "PyQt6.QtGui", "PyQt6.QtNetwork", "PyQt6.QtSvg", "tkinter"],
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
    name="cbzredactor-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
