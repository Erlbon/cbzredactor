"""The real MainWindow's menu bar conforms to the shared menu skeleton
(redactor_common's gui/menu_lint.py): heading order and count, unique
mnemonics, no duplicate shortcuts, platform-standard shortcuts, canonical
labels. Also covers the Ctrl+K command palette the skeleton installs."""

import sys

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

from core.version import APP_NAME
from gui.main_window import MainWindow
from redactor_common.gui.command_palette import collect_commands, filter_commands
from redactor_common.gui.menu_lint import lint_menu_bar

_app = QApplication.instance() or QApplication(sys.argv)

# Explicitly documented exceptions to the skeleton. F1 stays on About for one
# more step so the menu-move commit changes no shortcut; the shortcut-fix
# commit unbinds it and empties this list.
KNOWN_EXCEPTIONS: list[str] = [
    f"Help > About {APP_NAME} is bound to F1: F1 is Help contents and must not be bound to About",
]


def test_menu_bar_passes_the_shared_lint():
    problems = [p for p in lint_menu_bar(MainWindow()) if p not in KNOWN_EXCEPTIONS]
    assert problems == []


def test_command_palette_is_installed_on_ctrl_k():
    window = MainWindow()
    action = window.actions_["command_palette"]
    assert action.shortcut() == QKeySequence("Ctrl+K")
    assert action.isEnabled()
    assert window.command_palette is not None


def test_palette_lists_every_menu_action_and_finds_moved_items():
    window = MainWindow()
    commands = collect_commands(window, window.action_registry, exclude=window.actions_["command_palette"])
    titles = {c.title for c in commands}
    assert {"Open Files", "Redact", "Validate and Fix", "Scan Collection Folder", "Comic Vine", "API Keys"} <= titles
    found = filter_commands("collection", commands)
    assert any(c.title == "Scan Collection Folder" and c.path == "Tools ▸ Collection" for c in found)
    lookup = next(c for c in commands if c.title == "Comic Vine")
    assert lookup.path == "Metadata ▸ Look Up"
