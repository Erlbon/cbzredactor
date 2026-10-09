"""
The ƆBZ Redactor - entry point.

Run with:  python main.py
Build a standalone .exe with:  build_exe.bat  (see README.md)

Startup (crash logging + "Unexpected Error" dialog, taskbar icon ID,
theme, message-box width) is redactor_common.gui.app_bootstrap.run_app(),
shared with the other Redactor apps.
"""

import sys

from core import crash_log
from core.app_paths import asset_path
from cbzcli import cli_requested
from core.version import APP_NAME
from redactor_common.gui.app_bootstrap import run_app


def _window():
    from gui import app_settings
    from gui.main_window import MainWindow

    # Move a pre-secret-store Comic Vine key / GCD password out of the
    # ini file (no-op once done, or while no secure store exists).
    app_settings.migrate_legacy_secrets()

    return MainWindow()


def main() -> int:
    if cli_requested(sys.argv):
        # One exe: `cbzredactor info ...` is the command line (no window). See cbzcli/main.py.
        from cbzcli.main import main as cli_main
        from redactor_common.cli import run

        return run(cli_main, sys.argv[1:])
    return run_app(
        app_name=APP_NAME,
        window_factory=_window,
        crash_log_path=crash_log.log_path(),
        app_user_model_id="Erlbon.CbzRedactor.GUI.1",
        icon_path=asset_path("assets", "icon.ico"),
    )


if __name__ == "__main__":
    sys.exit(main())
