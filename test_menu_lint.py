"""The real MainWindow's menu bar conforms to the shared menu skeleton
(redactor_common's gui/menu_lint.py): heading order and count, unique
mnemonics, no duplicate shortcuts, platform-standard shortcuts, canonical
labels. Also covers the Ctrl+K command palette the skeleton installs."""

import sys

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow
from redactor_common.gui.command_palette import collect_commands, filter_commands
from redactor_common.gui.menu_lint import lint_menu_bar

_app = QApplication.instance() or QApplication(sys.argv)

# Explicitly documented exceptions to the skeleton (none at the moment).
KNOWN_EXCEPTIONS: list[str] = []
PORTABLE = QKeySequence.SequenceFormat.PortableText


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


def test_about_has_no_shortcut_and_f1_is_free():
    """F1 is Help contents everywhere; it used to open About. cbz has no
    other shortcut that moved, so there are no old-key aliases to keep."""
    window = MainWindow()
    assert window.actions_["about"].shortcuts() == []
    bound = {s.toString() for a in window.findChildren(QAction) for s in a.shortcuts()}
    assert "F1" not in bound


def test_no_shortcut_is_ambiguous_anywhere_in_the_window():
    """Qt fires NEITHER action when two share a key. The zoom toolbar's own
    StandardKey actions own Ctrl++ / Ctrl+-, so the View menu's Zoom In /
    Zoom Out carry no key of their own (Reset Zoom has Ctrl+0)."""
    window = MainWindow()
    owners: dict[str, list[str]] = {}
    for act in window.findChildren(QAction):
        for seq in act.shortcuts():
            owners.setdefault(seq.toString(), []).append(act.text())
    assert {k: v for k, v in owners.items() if len(v) > 1} == {}
    assert window.actions_["zoom_in"].shortcuts() == []
    assert window.actions_["zoom_out"].shortcuts() == []
    assert window.zoom.zoom_in_action.shortcuts() and window.zoom.zoom_out_action.shortcuts()
    assert window.actions_["reset_zoom"].shortcut() == QKeySequence("Ctrl+0")


def test_shortcuts_are_the_family_keys_with_ctrl_s_kept_on_save_all():
    window = MainWindow()
    expected = {
        "open_files": ["Ctrl+O"], "open_folder": ["Ctrl+Shift+O"], "save_as": ["Ctrl+Shift+S"],
        "save_all": ["Ctrl+Shift+A", "Ctrl+S"], "rename_file": ["F2"], "rename_export_move": ["Ctrl+E"],
        "remove_from_list": ["Del"], "refresh_list": ["F5", "Ctrl+R"], "undo": ["Ctrl+Z"], "redo": ["Ctrl+Y"],
        "search_replace": ["Ctrl+H"], "parse_filename": ["Ctrl+I"], "redact": ["Ctrl+Shift+E"],
        "command_palette": ["Ctrl+K"],
    }
    for key, seqs in expected.items():
        assert [s.toString(PORTABLE) for s in window.actions_[key].shortcuts()] == seqs, key
