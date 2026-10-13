"""
gui/conversion_settings_dialog.py

Tools > Conversion Settings... -- the same three settings as Tools >
Preferences > Conversion, shown with the same labels and read from / written
to the same gui/app_settings.py storage (the controls are built from
gui/preferences.py's Conversion page, so the two can never disagree):

- what happens when Load Files/Load Folder meets a file that isn't a real
  CBZ (CBR/CBT/CB7, or a mislabeled archive);
- whether converted originals are moved to the Recycle Bin;
- whether a conversion also resizes the pages (Ask / Always / Never).
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
from gui import preferences


def _conversion_specs() -> dict:
    section = next(s for s in preferences.preference_sections() if s.key == "conversion")
    return {spec.key: spec for spec in section.specs}


def _hint(text: str, indent: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("font-size: 11px;" + (" margin-left: 22px; margin-bottom: 4px;" if indent else ""))
    return label


class ConversionSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conversion Settings")
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)
        specs = _conversion_specs()

        self._group, self._radios = self._choice_box(
            outer, specs[preferences.KEY_ON_LOAD], app_settings.load_foreign_load_behavior()
        )

        recycle = specs[preferences.KEY_RECYCLE_ORIGINALS]
        self.recycle_check = QCheckBox(recycle.label)
        self.recycle_check.setChecked(app_settings.load_recycle_originals())
        outer.addWidget(self.recycle_check)
        outer.addWidget(_hint(recycle.help))

        self._resize_group, self._resize_radios = self._choice_box(
            outer, specs[preferences.KEY_RESIZE_ON_CONVERT], app_settings.load_resize_on_convert()
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _choice_box(self, outer, spec, current: str):
        box = QGroupBox(spec.label)
        layout = QVBoxLayout(box)
        group = QButtonGroup(self)
        radios: dict[str, QRadioButton] = {}
        for key, label in spec.choices:
            radio = QRadioButton(label)
            radio.setChecked(key == current)
            group.addButton(radio)
            radios[key] = radio
            layout.addWidget(radio)
        if spec.help:
            layout.addWidget(_hint(spec.help, indent=True))
        outer.addWidget(box)
        return group, radios

    @staticmethod
    def _checked(radios: dict, fallback: str) -> str:
        return next((key for key, radio in radios.items() if radio.isChecked()), fallback)

    def selected_behavior(self) -> str:
        return self._checked(self._radios, app_settings.FOREIGN_LOAD_UNCONVERTED)

    def selected_resize_on_convert(self) -> str:
        return self._checked(self._resize_radios, app_settings.RESIZE_ON_CONVERT_ASK)

    def accept(self) -> None:
        app_settings.save_foreign_load_behavior(self.selected_behavior())
        app_settings.save_recycle_originals(self.recycle_check.isChecked())
        app_settings.save_resize_on_convert(self.selected_resize_on_convert())
        super().accept()
