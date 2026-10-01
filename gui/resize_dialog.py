"""
gui/resize_dialog.py

The "Resize Images..." dialog (Operations menu) -- collects a target
max width (plus an optional max height), a quality, an optional output
format, which files to act on, and whether to overwrite the loaded
files in place or export resized copies to a folder. MainWindow does
the actual resizing (via CbzBook.resize_images(), see
core/cbz_file.py) with a progress dialog, same split of responsibility
as redactor_common.gui.rename_pattern_dialog.RenamePatternDialog: this
dialog only plans the operation, since running it interacts with
progress reporting and error summarizing that's specific to this
project.

The height limit, output format and "only Oversized files" scope were
added after reviewing CbxConverter (github.com/tomek-o/CbxConverter),
which offers the same choices on top of ImageMagick; here they're done
in-process with Pillow.

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
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from core.image_resize import ResizeOptions
from core.page_dimensions import OVERSIZED_FROM

# Named target widths (single page). "Wide" is the default -- see
# gui/app_settings.py's _DEFAULT_RESIZE_MAX_WIDTH.
WIDTH_PRESETS: list[tuple[str, int]] = [
    ("Standard", 1280),
    ("Wide", 1440),
    ("HD", 1920),
    ("UHD", 2560),
]

# (label, output_format) -- None keeps each page's own format.
OUTPUT_FORMAT_CHOICES: list[tuple[str, Optional[str]]] = [
    ("Keep original format", None),
    ("JPEG", "JPEG"),
    ("WebP (smaller files; most modern readers support it)", "WEBP"),
]


class ResizeOptionsForm(QGroupBox):
    """The Target box: max width (with presets), optional max height, output
    format and quality. Shared by the Resize Images dialog and the "Resize
    pages in the same step?" question a conversion asks (gui/convert_resize_dialog.py),
    so both offer the same choices and read them the same way."""

    def __init__(self, options: ResizeOptions, parent=None, title: str = "Target"):
        super().__init__(title, parent)
        default_max_width = options.max_width
        default_max_height = options.max_height or 0
        default_output_format = options.output_format
        default_jpeg_quality = options.jpeg_quality
        form = QFormLayout(self)

        self.preset_combo = QComboBox()
        for label, width in WIDTH_PRESETS:
            self.preset_combo.addItem(f"{label} ({width}px)", width)
        self.preset_combo.addItem("Custom", None)
        form.addRow("Preset:", self.preset_combo)

        self.max_width_spin = QSpinBox()
        self.max_width_spin.setRange(200, 10000)
        self.max_width_spin.setSingleStep(10)
        self.max_width_spin.setSuffix(" px")
        self.max_width_spin.setValue(default_max_width)
        form.addRow("Max width (single page):", self.max_width_spin)
        self._sync_preset_to_width(default_max_width)
        self.preset_combo.activated.connect(self._on_preset_chosen)
        self.max_width_spin.valueChanged.connect(self._sync_preset_to_width)

        height_row = QHBoxLayout()
        self.max_height_check = QCheckBox("Also limit height to")
        self.max_height_spin = QSpinBox()
        self.max_height_spin.setRange(200, 30000)
        self.max_height_spin.setSingleStep(10)
        self.max_height_spin.setSuffix(" px")
        self.max_height_spin.setValue(default_max_height or 2160)
        self.max_height_check.setChecked(bool(default_max_height))
        self.max_height_spin.setEnabled(bool(default_max_height))
        self.max_height_check.toggled.connect(self.max_height_spin.setEnabled)
        self.max_height_check.setToolTip(
            "Useful for very tall pages (manga, webtoon strips). Applies to "
            "every page, spreads included -- a spread is only wider, not taller."
        )
        height_row.addWidget(self.max_height_check)
        height_row.addWidget(self.max_height_spin)
        height_row.addStretch(1)
        form.addRow("", height_row)

        self.format_combo = QComboBox()
        for label, fmt in OUTPUT_FORMAT_CHOICES:
            self.format_combo.addItem(label, fmt)
        index = self.format_combo.findData(default_output_format)
        self.format_combo.setCurrentIndex(max(0, index))
        self.format_combo.setToolTip(
            "Converting re-encodes EVERY page (not only oversized ones) and "
            "renames it to the new extension, keeping reading order."
        )
        form.addRow("Output format:", self.format_combo)

        self.jpeg_quality_spin = QSpinBox()
        self.jpeg_quality_spin.setRange(50, 100)
        self.jpeg_quality_spin.setValue(default_jpeg_quality)
        self.jpeg_quality_spin.setToolTip("Used for JPEG and WebP pages; PNG pages keep lossless quality.")
        form.addRow("Quality:", self.jpeg_quality_spin)

    def _on_preset_chosen(self, index: int) -> None:
        width = self.preset_combo.itemData(index)
        if width is not None:
            self.max_width_spin.setValue(width)

    def _sync_preset_to_width(self, width: int) -> None:
        """Shows the preset matching `width`, or "Custom" for any other
        value typed into the width box."""
        index = self.preset_combo.findData(width)
        if index < 0:
            index = self.preset_combo.count() - 1  # "Custom"
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

    def options(self) -> ResizeOptions:
        return ResizeOptions(
            max_width=self.max_width_spin.value(),
            jpeg_quality=self.jpeg_quality_spin.value(),
            max_height=self.max_height_spin.value() if self.max_height_check.isChecked() else None,
            output_format=self.format_combo.currentData(),
        )


class ResizeImagesDialog(QDialog):
    def __init__(
        self,
        file_count: int,
        default_max_width: int,
        default_jpeg_quality: int,
        parent=None,
        oversized_count: Optional[int] = None,
        default_max_height: int = 0,
        default_output_format: Optional[str] = None,
        default_in_place: bool = False,
        default_recycle_original: bool = True,
        default_oversized_only: bool = False,
        default_export_folder: str = "",
    ):
        """The `default_*` values are what the last run chose (gui/app_settings.py),
        so the dialog opens the way it was left."""
        super().__init__(parent)
        self.setWindowTitle("Resize Images")
        self.setMinimumWidth(460)
        self.output_folder: Optional[str] = None

        outer = QVBoxLayout(self)

        intro = QLabel(
            "Shrinks oversized page images down to a target maximum width. "
            "A page detected as a double-page spread (wider than it is tall) "
            "gets DOUBLE that width, so each half keeps the same effective "
            "resolution a single page would -- it won't be crushed down to "
            "half the detail."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scope_box = QGroupBox("Files")
        scope_layout = QVBoxLayout(scope_box)
        self.all_files_radio = QRadioButton(f"All {file_count} file(s)")
        self.oversized_only_radio = QRadioButton(
            f"Only files marked Oversized ({OVERSIZED_FROM}px or wider) -- "
            f"{oversized_count if oversized_count is not None else 0} of {file_count}"
        )
        scope_group = QButtonGroup(self)
        scope_group.addButton(self.all_files_radio)
        scope_group.addButton(self.oversized_only_radio)
        self.all_files_radio.setChecked(True)
        self.oversized_only_radio.setEnabled(bool(oversized_count))
        if default_oversized_only and oversized_count:
            self.oversized_only_radio.setChecked(True)
        scope_layout.addWidget(self.all_files_radio)
        scope_layout.addWidget(self.oversized_only_radio)
        outer.addWidget(scope_box)

        self.form = ResizeOptionsForm(
            ResizeOptions(default_max_width, default_jpeg_quality, default_max_height or None, default_output_format)
        )
        # The form's widgets, under the names this dialog always had.
        self.preset_combo = self.form.preset_combo
        self.max_width_spin = self.form.max_width_spin
        self.max_height_check = self.form.max_height_check
        self.max_height_spin = self.form.max_height_spin
        self.format_combo = self.form.format_combo
        self.jpeg_quality_spin = self.form.jpeg_quality_spin
        outer.addWidget(self.form)

        note = QLabel(
            "A page already within its target size (or its doubled target "
            "width, if it's a detected spread) is left completely untouched "
            "unless you picked an output format -- this never upscales anything."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-style: italic;")
        outer.addWidget(note)

        mode_box = QGroupBox("Action")
        mode_layout = QVBoxLayout(mode_box)
        self.in_place_radio = QRadioButton("Resize files in place (replaces the loaded files)")
        self.export_radio = QRadioButton("Export resized copies to a folder (originals untouched)")
        self.in_place_radio.setChecked(default_in_place)
        self.export_radio.setChecked(not default_in_place)
        if default_export_folder and os.path.isdir(default_export_folder):
            self.output_folder = default_export_folder
        group = QButtonGroup(self)
        group.addButton(self.in_place_radio)
        group.addButton(self.export_radio)
        mode_layout.addWidget(self.export_radio)

        export_row = QHBoxLayout()
        self.choose_folder_btn = QPushButton("Choose Folder…")
        self.choose_folder_btn.clicked.connect(self._choose_folder)
        export_row.addWidget(self.choose_folder_btn)
        self.folder_label = QLabel(self.output_folder or "(no folder chosen)")
        self.folder_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        export_row.addWidget(self.folder_label, 1)
        mode_layout.addLayout(export_row)

        mode_layout.addWidget(self.in_place_radio)
        self.recycle_check = QCheckBox("Send the original to the Recycle Bin (recommended)")
        self.recycle_check.setChecked(default_recycle_original)
        self.recycle_check.setToolTip(
            "The resized file replaces the original only after it was written and checked, and the "
            "original goes to the Recycle Bin, never deleted for good. Untick to overwrite it directly "
            "(faster for huge files, but the original pixels are gone)."
        )
        recycle_row = QHBoxLayout()
        recycle_row.addSpacing(22)
        recycle_row.addWidget(self.recycle_check)
        mode_layout.addLayout(recycle_row)
        self.in_place_radio.toggled.connect(self.recycle_check.setEnabled)
        self.recycle_check.setEnabled(default_in_place)
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

    def _on_preset_chosen(self, index: int) -> None:
        self.form._on_preset_chosen(index)

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

    def max_height(self) -> Optional[int]:
        """None when the height limit is off."""
        return self.max_height_spin.value() if self.max_height_check.isChecked() else None

    def output_format(self) -> Optional[str]:
        """"JPEG", "WEBP", or None to keep each page's format."""
        return self.format_combo.currentData()

    def jpeg_quality(self) -> int:
        return self.jpeg_quality_spin.value()

    def recycle_original(self) -> bool:
        """In place only: the original goes to the Recycle Bin as the resized file replaces it."""
        return self.recycle_check.isChecked()

    def options(self) -> ResizeOptions:
        return self.form.options()

    def oversized_only(self) -> bool:
        return self.oversized_only_radio.isChecked()

    def is_export_mode(self) -> bool:
        return self.export_radio.isChecked()

    def output_path_for(self, original_path: str) -> Optional[str]:
        """None means "resize in place"; otherwise the export path for
        this one file (same basename, in the chosen output folder)."""
        if not self.is_export_mode():
            return None
        return os.path.join(self.output_folder, os.path.basename(original_path))
