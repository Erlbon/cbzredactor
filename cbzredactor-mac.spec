# PyInstaller spec for the macOS .app (experimental, unsigned).
# Build on a Mac with: pyinstaller cbzredactor-mac.spec --noconfirm
# The icon is generated from assets/icon.png as assets/icon.icns by the
# macos-build workflow (iconutil); the build still works without it.
import os

icon = "assets/icon.icns" if os.path.exists("assets/icon.icns") else None

a = Analysis(
    ["main.py"],
    datas=[
        ("assets/icon.png", "assets"),
        ("README.md", "."),
        ("CHANGELOG.md", "."),
        ("ABOUT.md", "."),
        ("CREDITS.md", "."),
    ],
    excludes=["numpy"],
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="cbzredactor", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="cbzredactor")
app = BUNDLE(coll, name="cbzredactor.app", icon=icon, bundle_identifier="io.github.erlbon.cbzredactor")
