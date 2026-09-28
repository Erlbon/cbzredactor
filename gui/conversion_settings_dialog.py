"""
gui/conversion_settings_dialog.py

Settings > Converting to CBZ... -- what happens when Load Files/Load
Folder meets a file that isn't a real CBZ (CBR/CBT/CB7, or a
mislabeled archive), and whether converted originals are moved to the
Recycle Bin. Both are stored via gui/app_settings.py.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from gui import app_settings

_BEHAVIOR_CHOICES = [
    (
        app_settings.FOREIGN_LOAD_UNCONVERTED,
        "Add to the list unconverted (recommended)",
        "Listed as greyed, read-only rows. Sort by Ext, select, then "
        "right-click > Convert to CBZ. Nothing is written until you ask.",
    ),
    (
        app_settings.FOREIGN_LOAD_CONVERT,
        "Convert automatically",
        "Converted while loading, without asking.",
    ),
    (
        app_settings.FOREIGN_LOAD_ASK,
        "Ask each time",
        "One prompt per load: Convert Now, Add Unconverted, or Skip.",
    ),
]


class ConversionSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Converting to CBZ")
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)

        behavior_box = QGroupBox("When loading CBR/CBT/CB7 (or mislabeled) files")
        behavior_layout = QVBoxLayout(behavior_box)
        self._group = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        current = app_settings.load_foreign_load_behavior()
        for key, label, help_text in _BEHAVIOR_CHOICES:
            radio = QRadioButton(label)
            radio.setChecked(key == current)
            self._group.addButton(radio)
            self._radios[key] = radio
            behavior_layout.addWidget(radio)
            hint = QLabel(help_text)
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 11px; margin-left: 22px; margin-bottom: 4px;")
            behavior_layout.addWidget(hint)
        outer.addWidget(behavior_box)

        self.recycle_check = QCheckBox("Move originals to the Recycle Bin after a successful conversion")
        self.recycle_check.setChecked(app_settings.load_recycle_originals())
        outer.addWidget(self.recycle_check)
        note = QLabel(
            "Applies to every conversion. An original is only moved after its "
            "new .cbz opened cleanly with the same number of pages -- never "
            "deleted permanently, never after a failure."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        outer.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def selected_behavior(self) -> str:
        for key, radio in self._radios.items():
            if radio.isChecked():
                return key
        return app_settings.FOREIGN_LOAD_UNCONVERTED

    def accept(self) -> None:
        app_settings.save_foreign_load_behavior(self.selected_behavior())
        app_settings.save_recycle_originals(self.recycle_check.isChecked())
        super().accept()
