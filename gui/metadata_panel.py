"""
gui/metadata_panel.py

The right-hand side panel: a scrollable ComicInfo.xml field editor on
top, and a first-page thumbnail (the "cover", per the project's Sept
2026 scoping conversation) below it, in a resizable vertical splitter --
fields on top, cover below, exactly like epubredactor's own
gui/tag_panel.py ("Bulk Edit Tags" above, "Cover Image" below, with a
draggable divider between them). This panel had briefly had that
inverted (cover above, fixed-height, unresizable) until the user
flagged it as wrong relative to the established convention.

Deliberately dumb: this widget owns no CbzBook and does no file I/O --
MainWindow calls load_metadata()/read back edited fields via
apply_to_metadata(), and connects fieldsChanged to its own dirty-
tracking. Keeping I/O out of this class is what makes it painlessly
reusable for bulk editing too (set_bulk_mode()/bulk_changed_fields()):
when MainWindow has more than one file selected, it puts this panel
into bulk mode instead of loading any one file's metadata -- every
field starts blank, and only the fields the user actually types
something into get applied (to every selected file at once) when they
click "Apply to N Selected Files", via bulkApplyRequested. A field
left blank is left untouched on every file, not cleared -- same
"blank means don't touch" convention used throughout the sibling
Redactor tools' own bulk-edit features.

The Genre and Language (ISO) fields each get a small "+" quick-pick
button (same role as epubredactor's own Genre/Language "+" menus) --
built from gui/app_settings.py's hideable-defaults-plus-custom lists
(core/comic_genres.py, core/comic_languages.py), manageable via
Settings > Add/Remove Genres.../Add/Remove Languages... in MainWindow.
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from redactor_common.gui.collapsible_splitter import CollapseToggleButton
from redactor_common.gui.image_label import AspectRatioImageLabel

from core.comic_genres import add_genre
from core.comicinfo import AGE_RATING_VALUES, BLACK_AND_WHITE_VALUES, MANGA_VALUES, ComicInfoMetadata

# (label, attribute name on ComicInfoMetadata) pairs, grouped the same
# way the project's scoping conversation grouped them for the form UI.
IDENTITY_FIELDS = [
    ("Title", "title"), ("Series", "series"), ("Number", "number"),
    ("Count", "count"), ("Volume", "volume"),
    ("Alternate Series", "alternate_series"), ("Alternate Number", "alternate_number"),
    ("Alternate Count", "alternate_count"),
    ("GTIN", "gtin"),  # v2.1 draft -- ISBN/ISSN/EAN/JAN/etc, whichever the publisher used
]
STORY_FIELDS = [
    ("Genre", "genre"), ("Tags", "tags"),  # Tags: v2.1 draft
    ("Characters", "characters"), ("Teams", "teams"),
    ("Locations", "locations"), ("Main Character/Team", "main_character_or_team"),
    ("Story Arc", "story_arc"), ("Story Arc Number", "story_arc_number"),  # v2.1 draft
    ("Series Group", "series_group"),
]
CREDIT_FIELDS = [
    ("Writer", "writer"), ("Penciller", "penciller"), ("Inker", "inker"),
    ("Colorist", "colorist"), ("Letterer", "letterer"),
    ("Cover Artist", "cover_artist"), ("Editor", "editor"),
    ("Translator", "translator"),  # v2.1 draft
]
PUBLICATION_FIELDS = [
    ("Publisher", "publisher"), ("Imprint", "imprint"), ("Web", "web"),
    ("Language (ISO)", "language_iso"), ("Format", "format"),
    ("Year", "year"), ("Month", "month"), ("Day", "day"),
    ("Scan Information", "scan_information"),
]

# Fields that get a "+" quick-pick button next to their QLineEdit --
# handled specially in _build_group(); see _show_quick_pick_menu().
_QUICK_PICK_ATTRS = {"genre", "language_iso"}


class _ScrollSafeComboBox(QComboBox):
    """Ignores mouse-wheel scrolling unless this combo currently has
    keyboard focus -- without this, scrolling the mouse wheel to scroll
    the whole field list (see _build_fields_scroll_area()) silently
    changes THIS widget's value instead of scrolling past it, the
    moment the cursor happens to be hovering over it. A well-known Qt
    gotcha for any QScrollArea containing a combo/spin box; click into
    one first (giving it focus) to use the wheel to change its value on
    purpose."""

    def wheelEvent(self, event) -> None:  # noqa: N802 -- Qt override signature
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # bubbles up to the QScrollArea, which scrolls instead


class _ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    """Same fix as _ScrollSafeComboBox, for Community Rating's spin box."""

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ComicInfoPanel(QWidget):
    fieldsChanged = pyqtSignal()
    collapseToggleRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False  # True while load_metadata() populates widgets, to suppress fieldsChanged
        self.bulk_mode = False  # True when editing N>1 selected files at once -- see module docstring

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

        # Only shown in bulk mode (set_bulk_mode()) -- explains the
        # "blank means unchanged" convention. The actual apply action
        # lives on the toolbar/Operations menu ("Apply to N Selected
        # Files", MainWindow._apply_bulk_edit), same as epubredactor's
        # tag_panel -- not a button embedded in this panel, so it's
        # reachable without scrolling and shares one QAction (enabled
        # state + dynamic label) between both places.
        self.bulk_info_label = QLabel("")
        self.bulk_info_label.setWordWrap(True)
        self.bulk_info_label.setStyleSheet("color: palette(mid); font-style: italic;")
        self.bulk_info_label.setVisible(False)
        outer.addWidget(self.bulk_info_label)

        self._line_edits: dict[str, QLineEdit] = {}
        # The widget actually passed to QFormLayout.addRow() for each
        # field (the QLineEdit itself, the quick-pick wrapper container
        # for Genre/Language, or the classification combo/spin box) --
        # what set_visible_fields() hides/shows. Deliberately a
        # *different* dict from _line_edits: that one must keep holding
        # every field's actual data widget regardless of visibility,
        # since load_metadata()/apply_to_metadata()/bulk_changed_fields()
        # read and write every field unconditionally -- a hidden column
        # still round-trips its data, only the on-screen row disappears
        # (per the user's explicit "should still exist, and be written
        # to" requirement).
        self._row_widgets: dict[str, QWidget] = {}
        # Each field group's QGroupBox plus the attrs it contains, so a
        # group whose every field is hidden can hide its own title too,
        # instead of showing an empty "Identity / Sequence" box.
        self._group_boxes: list[tuple[QGroupBox, list[str]]] = []
        scroll = self._build_fields_scroll_area()
        cover_box = self._build_cover_box()

        # A real draggable divider between the two sections -- same
        # role as epubredactor's tag_panel.py: lets the cover be given
        # much more room by dragging, even if that squeezes the field
        # form down to something that needs to scroll, or vice versa.
        # Fields on top, cover below -- matching that established order.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(scroll)
        splitter.addWidget(cover_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([600, 300])  # initial bias toward fields; purely a starting hint
        outer.addWidget(splitter, 1)

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_fields_scroll_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)

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
        return scroll

    def _build_cover_box(self) -> QScrollArea:
        self.cover_box = QGroupBox("Cover (First Page)")
        box = self.cover_box
        layout = QVBoxLayout(box)

        self.cover_label = AspectRatioImageLabel()
        # An explicit small minimum WIDTH too, not just height -- a plain
        # QLabel's auto minimumSizeHint is based on its current text
        # ("No file selected" etc, whenever there's no pixmap loaded),
        # which would otherwise impose a much wider floor than needed
        # and fight the side panel's collapse-to-slim-strip behavior
        # (same fix epubredactor's own cover_preview uses).
        self.cover_label.setMinimumSize(60, 80)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("background-color: palette(base); border: 1px solid palette(mid);")
        self.cover_label.setText("No pages")
        layout.addWidget(self.cover_label, 1)

        self.page_count_label = QLabel("")
        self.page_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.page_count_label)

        # Wrapped in a QScrollArea for exactly one reason, unrelated to
        # actually scrolling: a bare QGroupBox's minimumSizeHint is
        # inflated by its own TITLE text width (confirmed directly --
        # "Cover (First Page)" alone forces a ~254px floor, regardless
        # of setMinimumWidth() calls, which don't affect
        # minimumSizeHint() at all). Since this box sits directly in
        # the panel's own splitter (not inside a scroll area the way
        # the field groups already are, via _build_fields_scroll_area()
        # -- a QScrollArea's OWN minimumSizeHint stays small regardless
        # of its content's), that title-driven floor was blocking the
        # whole side panel from ever reaching PANEL_COLLAPSED_WIDTH:
        # collapsing looked like it worked (the field list visibly
        # shrank) while the cover silently held the panel open, and
        # since the resulting width was still bigger than
        # collapsed_width, is_collapsed() reported False -- so the next
        # click tried to collapse again instead of restoring, and the
        # button looked stuck. This wrapper is purely a minimum-size
        # trick; the cover still renders at full size normally, a
        # scrollbar only appears if the panel is dragged narrower than
        # the cover's own natural width.
        self._cover_scroll = QScrollArea()
        self._cover_scroll.setWidgetResizable(True)
        self._cover_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._cover_scroll.setWidget(box)
        return self._cover_scroll

    def _build_group(self, title: str, fields: list) -> QGroupBox:
        box = QGroupBox(title)
        layout = QFormLayout(box)
        for label, attr in fields:
            edit = QLineEdit()
            edit.textChanged.connect(self._on_field_changed)
            self._line_edits[attr] = edit
            row_widget = self._wrap_with_quick_pick(attr, edit) if attr in _QUICK_PICK_ATTRS else edit
            layout.addRow(label, row_widget)
            self._row_widgets[attr] = row_widget
        self._group_boxes.append((box, [attr for _label, attr in fields]))
        return box

    def _wrap_with_quick_pick(self, attr: str, edit: QLineEdit) -> QWidget:
        """A QLineEdit plus a small "+" button opening a searchable
        picker dialog (see _show_quick_pick_dialog()) -- used for Genre
        and Language (ISO), the two fields with a curated default list
        plus user-manageable custom entries (Settings > Add/Remove
        Genres.../Add/Remove Languages...).

        A plain QMenu was the original design here, but it doesn't
        scale: once enough custom entries pile up (a real cbzredactor
        complaint -- "the genre list gets too long to see the apply
        button", after adding many custom genres) a flat menu can
        overflow the screen with no search and only the OS's own tiny
        scroll arrows to get through it. redactor_common.gui.
        quick_pick_dialog.QuickPickDialog fixes this at the root: a
        fixed-size dialog with a real internally-scrolling list and a
        filter box, so it never overflows and OK/Cancel stay visible
        no matter how long the list gets."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)

        button = QToolButton()
        button.setText("+")
        button.setToolTip("Pick from list, or add a custom entry")
        button.clicked.connect(lambda: self._show_quick_pick_dialog(attr, edit))
        row.addWidget(button)
        return container

    def _show_quick_pick_dialog(self, attr: str, edit: QLineEdit) -> None:
        from redactor_common.gui.quick_pick_dialog import QuickPickDialog

        from gui import app_settings  # local import: avoids a hard Qt/app_settings dependency at module load

        if attr == "genre":
            dialog = QuickPickDialog(
                "Pick Genre(s)",
                load_entries_fn=lambda: [(g, g) for g in app_settings.load_genres()],
                multi_select=True,  # Genre is a comma-separated field -- picking several at once makes sense
                add_custom_fn=self._add_custom_genre,
                parent=self,
            )
            if dialog.exec() == QuickPickDialog.DialogCode.Accepted:
                for genre in dialog.selected_keys():
                    edit.setText(add_genre(edit.text(), genre))
        elif attr == "language_iso":
            dialog = QuickPickDialog(
                "Pick Language",
                load_entries_fn=lambda: [(c, f"{n} ({c})") for c, n in app_settings.load_languages()],
                multi_select=False,  # replaces the field outright -- picking a second wouldn't mean anything
                add_custom_fn=self._add_custom_language,
                parent=self,
            )
            if dialog.exec() == QuickPickDialog.DialogCode.Accepted:
                keys = dialog.selected_keys()
                if keys:
                    edit.setText(keys[0])

    def _add_custom_genre(self, dialog) -> None:
        from gui import app_settings

        text, ok = QInputDialog.getText(dialog, "Add Custom Genre", "New genre name:")
        text = text.strip()
        if ok and text:
            app_settings.add_custom_genre(text)

    def _add_custom_language(self, dialog) -> None:
        from gui import app_settings

        code, ok = QInputDialog.getText(
            dialog, "Add Custom Language", 'Language code (ISO 639-1, e.g. "pt" for Portuguese):'
        )
        code = code.strip()
        if not (ok and code):
            return
        name, ok = QInputDialog.getText(dialog, "Add Custom Language", "Display name for this language:")
        name = name.strip()
        if ok and name:
            app_settings.add_custom_language(code, name)

    def _build_text_group(self, title: str, attr: str) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        text_edit = QPlainTextEdit()
        text_edit.setFixedHeight(80)
        text_edit.textChanged.connect(self._on_field_changed)
        self._line_edits[attr] = text_edit  # QPlainTextEdit exposes toPlainText()/setPlainText() below
        layout.addWidget(text_edit)
        # No separate label/row here -- the whole box (title included) IS
        # the row for a free-text field, so hiding/showing the box itself
        # is set_visible_fields()'s mechanism for Summary/Notes/Review.
        self._row_widgets[attr] = box
        self._group_boxes.append((box, [attr]))
        return box

    def _build_classification_group(self) -> QGroupBox:
        box = QGroupBox("Classification")
        layout = QFormLayout(box)

        self.age_rating_combo = _ScrollSafeComboBox()
        self.age_rating_combo.addItems(AGE_RATING_VALUES)
        self.age_rating_combo.setEditable(True)  # preserves an unrecognized value already in the file
        self.age_rating_combo.currentTextChanged.connect(self._on_field_changed)
        layout.addRow("Age Rating", self.age_rating_combo)
        self._row_widgets["age_rating"] = self.age_rating_combo

        self.manga_combo = _ScrollSafeComboBox()
        self.manga_combo.addItems(MANGA_VALUES)
        self.manga_combo.setEditable(True)
        self.manga_combo.currentTextChanged.connect(self._on_field_changed)
        layout.addRow("Manga", self.manga_combo)
        self._row_widgets["manga"] = self.manga_combo

        self.black_and_white_combo = _ScrollSafeComboBox()
        self.black_and_white_combo.addItems(BLACK_AND_WHITE_VALUES)
        self.black_and_white_combo.setEditable(True)
        self.black_and_white_combo.currentTextChanged.connect(self._on_field_changed)
        layout.addRow("Black && White", self.black_and_white_combo)
        self._row_widgets["black_and_white"] = self.black_and_white_combo

        self.community_rating_spin = _ScrollSafeDoubleSpinBox()
        self.community_rating_spin.setRange(0.0, 5.0)
        self.community_rating_spin.setSingleStep(0.1)
        self.community_rating_spin.setDecimals(1)
        self.community_rating_spin.setSpecialValueText(" ")  # 0.0 reads as "unset" in the GUI
        self.community_rating_spin.valueChanged.connect(self._on_field_changed)
        layout.addRow("Community Rating", self.community_rating_spin)
        self._row_widgets["community_rating"] = self.community_rating_spin

        self._group_boxes.append((box, ["age_rating", "manga", "black_and_white", "community_rating"]))
        return box

    def _on_field_changed(self, *_args) -> None:
        if not self._loading:
            self.fieldsChanged.emit()

    # ------------------------------------------------------------------
    # Column <-> field visibility
    # ------------------------------------------------------------------

    def set_visible_fields(self, visible_attrs: set[str]) -> None:
        """Shows/hides each field's row to match `visible_attrs` (the
        set of ComicInfo attribute names whose table column is currently
        shown) -- called by MainWindow whenever column visibility changes
        (Settings > Add/Remove Columns..., or right-clicking a header),
        so a column you've hidden from the table also stops cluttering
        this panel, and vice versa.

        Deliberately hides widgets rather than rebuilding rows from
        scratch: nothing is ever destroyed, so (a) there's no risk of
        losing an in-progress edit sitting in a widget mid-hide, and (b)
        every field keeps working exactly as before for every other
        code path -- load_metadata(), apply_to_metadata(),
        bulk_changed_fields() all iterate _line_edits unconditionally,
        completely unaware of visibility. A hidden field still exists
        and still gets written to by a lookup, Parse Filename, Search/
        Replace, or Case Conversion; only the on-screen row disappears.
        """
        for attr, row_widget in self._row_widgets.items():
            visible = attr in visible_attrs
            row_widget.setVisible(visible)
            parent = row_widget.parentWidget()
            form_layout = parent.layout() if parent else None
            if isinstance(form_layout, QFormLayout):
                label = form_layout.labelForField(row_widget)
                if label:
                    label.setVisible(visible)

        # A group whose every field just got hidden shouldn't show an
        # empty box with just a title -- hide the box itself too.
        for box, attrs in self._group_boxes:
            box.setVisible(any(attr in visible_attrs for attr in attrs))

    # ------------------------------------------------------------------
    # Load / read-back
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)
        if not enabled:
            self.set_bulk_mode(0)
            self.cover_label.set_original_pixmap(None)
            self.cover_label.setText("No file selected")
            self.page_count_label.setText("")

    def set_bulk_mode(self, count: int) -> None:
        """count <= 1: normal single-file mode (the cover/page-count
        area is shown; MainWindow separately calls load_metadata() for
        that one file). count > 1: bulk mode -- every field is cleared,
        the cover/page-count area (meaningless across different files)
        is hidden, and only fields the user actually fills in get
        applied, to every selected file, via the toolbar/Operations
        menu's "Apply to N Selected Files" action."""
        self.bulk_mode = count > 1
        # Hides the wrapping QScrollArea, not just the inner cover_box
        # -- hiding only the QGroupBox would leave an empty scroll
        # viewport occupying space instead of actually disappearing.
        self._cover_scroll.setVisible(not self.bulk_mode)
        self.bulk_info_label.setVisible(self.bulk_mode)
        if self.bulk_mode:
            self.bulk_info_label.setText(
                f"Editing {count} selected files at once. Leave a field blank to leave it "
                "unchanged on every file; fill one in to set it on all of them."
            )
            self.load_metadata(ComicInfoMetadata(), None, "")

    def bulk_changed_fields(self) -> dict:
        """Only the fields with something actually typed/selected in
        bulk mode -- what gets applied to every selected file on
        bulkApplyRequested. A field left blank means "don't touch it",
        not "clear it" (unlike apply_to_metadata(), single-file mode's
        equivalent, where a blank field IS the value to write)."""
        changed: dict = {}
        for attr, widget in self._line_edits.items():
            value = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
            value = value.strip()
            if value:
                changed[attr] = value

        for attr, combo in (
            ("age_rating", self.age_rating_combo),
            ("manga", self.manga_combo),
            ("black_and_white", self.black_and_white_combo),
        ):
            value = combo.currentText().strip()
            if value:
                changed[attr] = value

        rating = self.community_rating_spin.value()
        if rating > 0:
            changed["community_rating"] = f"{rating:.1f}"

        return changed

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
