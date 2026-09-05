"""
gui/metadata_panel.py

The right-hand side panel: a first-page thumbnail (the "cover", per the
project's Sept 2026 scoping conversation) on top, and a scrollable
ComicInfo.xml field editor below it. Same collapsible-side-panel role
as epubredactor's gui/tag_panel.py, rebuilt here for CBZ's field set.

Deliberately dumb: this widget owns no CbzBook and does no file I/O --
MainWindow calls load_metadata()/read back edited fields via
current_metadata(), and connects fieldsChanged to its own dirty-
tracking. Keeping I/O out of this class is what makes it painlessly
reusable for a future "edit fields, apply to N selected files" batch
flow without dragging file handling along with it.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from redactor_common.gui.collapsible_splitter import CollapseToggleButton
from redactor_common.gui.image_label import AspectRatioImageLabel

from core.comicinfo import AGE_RATING_VALUES, BLACK_AND_WHITE_VALUES, MANGA_VALUES, ComicInfoMetadata

# (label, attribute name on ComicInfoMetadata) pairs, grouped the same
# way the project's scoping conversation grouped them for the form UI.
IDENTITY_FIELDS = [
    ("Title", "title"), ("Series", "series"), ("Number", "number"),
    ("Count", "count"), ("Volume", "volume"),
    ("Alternate Series", "alternate_series"), ("Alternate Number", "alternate_number"),
    ("Alternate Count", "alternate_count"),
]
STORY_FIELDS = [
    ("Genre", "genre"), ("Characters", "characters"), ("Teams", "teams"),
    ("Locations", "locations"), ("Main Character/Team", "main_character_or_team"),
    ("Story Arc", "story_arc"), ("Series Group", "series_group"),
]
CREDIT_FIELDS = [
    ("Writer", "writer"), ("Penciller", "penciller"), ("Inker", "inker"),
    ("Colorist", "colorist"), ("Letterer", "letterer"),
    ("Cover Artist", "cover_artist"), ("Editor", "editor"),
]
PUBLICATION_FIELDS = [
    ("Publisher", "publisher"), ("Imprint", "imprint"), ("Web", "web"),
    ("Language (ISO)", "language_iso"), ("Format", "format"),
    ("Year", "year"), ("Month", "month"), ("Day", "day"),
]


class ComicInfoPanel(QWidget):
    fieldsChanged = pyqtSignal()
    collapseToggleRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False  # True while load_metadata() populates widgets, to suppress fieldsChanged

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        # No label in this row (just the toggle, right-aligned) --
        # deliberately, matching epubredactor's tag_panel.py: a fixed-
        # width text label here would impose a minimum width on the
        # whole row, which fights SplitterPaneCollapser's attempt to
        # shrink the panel down to near collapsed_width on toggle.
        header = QHBoxLayout()
        header.addStretch(1)
        self.collapse_toggle_btn = CollapseToggleButton()
        self.collapse_toggle_btn.clicked.connect(self.collapseToggleRequested.emit)
        header.addWidget(self.collapse_toggle_btn)
        outer.addLayout(header)

        self.cover_label = AspectRatioImageLabel()
        # An explicit small minimum WIDTH too, not just height -- a plain
        # QLabel's auto minimumSizeHint is based on its current text
        # ("No file selected" etc, whenever there's no pixmap loaded),
        # which would otherwise impose a much wider floor than 220px-tall
        # actually needs and fight the side panel's collapse-to-slim-strip
        # behavior (same fix epubredactor's own cover_preview uses -- see
        # its COVER_PREVIEW_MIN_SIZE).
        self.cover_label.setMinimumSize(60, 220)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("background-color: palette(base); border: 1px solid palette(mid);")
        self.cover_label.setText("No pages")
        outer.addWidget(self.cover_label)

        self.page_count_label = QLabel("")
        self.page_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.page_count_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)

        self._line_edits: dict[str, QLineEdit] = {}
        form_layout.addWidget(self._build_group("Identity / Sequence", IDENTITY_FIELDS))
        form_layout.addWidget(self._build_group("Story", STORY_FIELDS))
        form_layout.addWidget(self._build_group("Credits", CREDIT_FIELDS))
        form_layout.addWidget(self._build_group("Publication", PUBLICATION_FIELDS))
        form_layout.addWidget(self._build_classification_group())
        form_layout.addWidget(self._build_text_group("Summary", "summary"))
        form_layout.addWidget(self._build_text_group("Notes", "notes"))
        form_layout.addWidget(self._build_text_group("Review", "review"))
        form_layout.addStretch(1)

        scroll.setWidget(form_container)
        outer.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_group(self, title: str, fields: list) -> QGroupBox:
        box = QGroupBox(title)
        layout = QFormLayout(box)
        for label, attr in fields:
            edit = QLineEdit()
            edit.textChanged.connect(self._on_field_changed)
            self._line_edits[attr] = edit
            layout.addRow(label, edit)
        return box

    def _build_text_group(self, title: str, attr: str) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        text_edit = QPlainTextEdit()
        text_edit.setFixedHeight(80)
        text_edit.textChanged.connect(self._on_field_changed)
        self._line_edits[attr] = text_edit  # QPlainTextEdit exposes toPlainText()/setPlainText() below
        layout.addWidget(text_edit)
        return box

    def _build_classification_group(self) -> QGroupBox:
        box = QGroupBox("Classification")
        layout = QFormLayout(box)

        self.age_rating_combo = QComboBox()
        self.age_rating_combo.addItems(AGE_RATING_VALUES)
        self.age_rating_combo.setEditable(True)  # preserves an unrecognized value already in the file
        self.age_rating_combo.currentTextChanged.connect(self._on_field_changed)
        layout.addRow("Age Rating", self.age_rating_combo)

        self.manga_combo = QComboBox()
        self.manga_combo.addItems(MANGA_VALUES)
        self.manga_combo.setEditable(True)
        self.manga_combo.currentTextChanged.connect(self._on_field_changed)
        layout.addRow("Manga", self.manga_combo)

        self.black_and_white_combo = QComboBox()
        self.black_and_white_combo.addItems(BLACK_AND_WHITE_VALUES)
        self.black_and_white_combo.setEditable(True)
        self.black_and_white_combo.currentTextChanged.connect(self._on_field_changed)
        layout.addRow("Black && White", self.black_and_white_combo)

        self.community_rating_spin = QDoubleSpinBox()
        self.community_rating_spin.setRange(0.0, 5.0)
        self.community_rating_spin.setSingleStep(0.1)
        self.community_rating_spin.setDecimals(1)
        self.community_rating_spin.setSpecialValueText(" ")  # 0.0 reads as "unset" in the GUI
        self.community_rating_spin.valueChanged.connect(self._on_field_changed)
        layout.addRow("Community Rating", self.community_rating_spin)

        return box

    def _on_field_changed(self, *_args) -> None:
        if not self._loading:
            self.fieldsChanged.emit()

    # ------------------------------------------------------------------
    # Load / read-back
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)
        if not enabled:
            self.cover_label.set_original_pixmap(None)
            self.cover_label.setText("No file selected")
            self.page_count_label.setText("")

    def load_metadata(self, metadata: ComicInfoMetadata, cover_bytes: Optional[bytes], page_count_text: str) -> None:
        """Populates every field from `metadata` and shows `cover_bytes`
        (the first page's raw image bytes) as the thumbnail. Field
        change signals are suppressed while this runs -- this is a load,
        not an edit."""
        self._loading = True
        try:
            for attr, widget in self._line_edits.items():
                value = getattr(metadata, attr, "")
                if isinstance(widget, QPlainTextEdit):
                    widget.setPlainText(value)
                else:
                    widget.setText(value)

            self.age_rating_combo.setCurrentText(metadata.age_rating)
            self.manga_combo.setCurrentText(metadata.manga)
            self.black_and_white_combo.setCurrentText(metadata.black_and_white)
            try:
                self.community_rating_spin.setValue(float(metadata.community_rating or 0.0))
            except ValueError:
                self.community_rating_spin.setValue(0.0)

            if cover_bytes:
                pixmap = QPixmap()
                pixmap.loadFromData(cover_bytes)
                self.cover_label.set_original_pixmap(pixmap if not pixmap.isNull() else None)
                self.cover_label.setText("" if not pixmap.isNull() else "Could not read first page")
            else:
                self.cover_label.set_original_pixmap(None)
                self.cover_label.setText("No pages")

            self.page_count_label.setText(page_count_text)
        finally:
            self._loading = False

    def apply_to_metadata(self, metadata: ComicInfoMetadata) -> None:
        """Writes every widget's current value back into `metadata` in
        place. PageCount is deliberately not touched here -- see
        core/cbz_file.py, which always recomputes it from the archive
        itself at save time."""
        for attr, widget in self._line_edits.items():
            value = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
            setattr(metadata, attr, value.strip())

        metadata.age_rating = self.age_rating_combo.currentText().strip()
        metadata.manga = self.manga_combo.currentText().strip()
        metadata.black_and_white = self.black_and_white_combo.currentText().strip()
        rating = self.community_rating_spin.value()
        metadata.community_rating = f"{rating:.1f}" if rating > 0 else ""
