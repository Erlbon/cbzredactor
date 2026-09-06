"""
gui/resize_dialog.py

The "Resize Images..." dialog (Operations menu) -- collects a target
max width, a JPEG quality, and whether to overwrite the loaded files in
place or export resized copies to a folder. MainWindow does the actual
resizing (via CbzBook.resize_images(), see core/cbz_file.py) with a
progress dialog, same split of responsibility as
redactor_common.gui.rename_pattern_dialog.RenamePatternDialog: this
dialog only plans the operation, since running it interacts with
progress reporting and error summarizing that's specific to this
project.

Deliberately no before/after preview table: computing an accurate size
estimate means actually decoding and re-encoding every page once
already, which is exactly the expensive extra pass this feature exists
to help someone avoid paying on "extremely large" files. Instead this
dialog is upfront about that in its warning label, and MainWindow shows
a real (not estimated) summary once the resize has actually run.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QFileDialog,
)


class ResizeImagesDialog(QDialog):
    def __init__(
        self,
        file_count: int,
        default_max_width: int,
        default_jpeg_quality: int,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Resize Images")
        self.setMinimumWidth(420)
        self.output_folder: Optional[str] = None

        outer = QVBoxLayout(self)

        intro = QLabel(
            f"Shrinks oversized page images in {file_count} file(s) down to a "
            "target maximum width. A page detected as a double-page spread "
            "(wider than it is tall) gets DOUBLE that width, so each half "
            "keeps the same effective resolution a single page would -- it "
            "won't be crushed down to half the detail."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form_box = QGroupBox("Target")
        form = QFormLayout(form_box)

        self.max_width_spin = QSpinBox()
        self.max_width_spin.setRange(200, 10000)
        self.max_width_spin.setSingleStep(100)
        self.max_width_spin.setSuffix(" px")
        self.max_width_spin.setValue(default_max_width)
        form.addRow("Max width (single page):", self.max_width_spin)

        self.jpeg_quality_spin = QSpinBox()
        self.jpeg_quality_spin.setRange(50, 100)
        self.jpeg_quality_spin.setValue(default_jpeg_quality)
        self.jpeg_quality_spin.setToolTip("Only affects pages actually re-saved as JPEG; PNG pages keep lossless quality.")
        form.addRow("JPEG quality:", self.jpeg_quality_spin)

        outer.addWidget(form_box)

        note = QLabel(
            "A page already at or under its target width (or its doubled "
            "target, if it's a detected spread) is left completely "
            "untouched -- this never upscales anything."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-style: italic;")
        outer.addWidget(note)

        mode_box = QGroupBox("Action")
        mode_layout = QVBoxLayout(mode_box)
        self.in_place_radio = QRadioButton("Resize files in place (overwrites the loaded files)")
        self.export_radio = QRadioButton("Export resized copies to a folder (originals untouched)")
        self.export_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.in_place_radio)
        group.addButton(self.export_radio)
        mode_layout.addWidget(self.export_radio)

        export_row = QHBoxLayout()
        self.choose_folder_btn = QPushButton("Choose Folder…")
        self.choose_folder_btn.clicked.connect(self._choose_folder)
        export_row.addWidget(self.choose_folder_btn)
        self.folder_label = QLabel("(no folder chosen)")
        self.folder_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        export_row.addWidget(self.folder_label, 1)
        mode_layout.addLayout(export_row)

        mode_layout.addWidget(self.in_place_radio)
        outer.addWidget(mode_box)

        self.warning_label = QLabel(
            "This re-encodes page images and cannot be undone (unlike a "
            "metadata edit, there's no in-memory original to restore once "
            "pixels are actually rewritten to disk)."
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b45309; font-size: 11px;")
        outer.addWidget(self.warning_label)

        self.in_place_radio.toggled.connect(self._update_ok_enabled)
        self.export_radio.toggled.connect(self._update_ok_enabled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Resize")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._update_ok_enabled()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Export Folder")
        if folder:
            self.output_folder = folder
            self.folder_label.setText(folder)
            self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        needs_folder = self.export_radio.isChecked() and not self.output_folder
        self._ok_button.setEnabled(not needs_folder)
        self.folder_label.setStyleSheet(
            "color: #b45309; font-size: 11px;" if needs_folder else "color: palette(mid); font-size: 11px;"
        )
        if needs_folder:
            self.folder_label.setText("(choose a folder before resizing)")
        elif not self.output_folder:
            self.folder_label.setText("(no folder chosen)")

    # ------------------------------------------------------------------
    # Result accessors
    # ------------------------------------------------------------------

    def max_width(self) -> int:
        return self.max_width_spin.value()

    def jpeg_quality(self) -> int:
        return self.jpeg_quality_spin.value()

    def is_export_mode(self) -> bool:
        return self.export_radio.isChecked()

    def output_path_for(self, original_path: str) -> Optional[str]:
        """None means "resize in place"; otherwise the export path for
        this one file (same basename, in the chosen output folder)."""
        if not self.is_export_mode():
            return None
        return os.path.join(self.output_folder, os.path.basename(original_path))
