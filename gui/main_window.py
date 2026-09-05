"""
gui/main_window.py

The main window: a collapsible metadata side panel (gui/metadata_panel.py,
scrollable ComicInfo.xml form + cover thumbnail) on the left, and a table
of loaded CBZ files on the right -- same 2-pane layout convention as the
sibling Redactor tools, built on redactor_common.gui.collapsible_splitter.

The table's columns are field-name-based (redactor_common.core.
table_settings), not index-based -- drag a header to reorder, right-click
a header for a show/hide checklist or "Add/Remove Columns...", and both
order and visibility persist across restarts via gui/app_settings.py.
"""

from __future__ import annotations

import os
import shutil
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
)

from redactor_common.core.table_settings import is_column_visible, merge_column_order, sanitize_hidden_fields
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.gui.manage_list_dialog import ManageListDialog
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.parse_filename_dialog import ParseFilenameDialog
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

from core.cbr_convert import CbrConversionError, convert_cbr_to_cbz
from core.cbz_file import CbzBook, CbzError
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui import app_settings
from gui.comicvine_lookup_dialog import ComicVineLookupDialog
from gui.gcd_lookup_dialog import GcdLookupDialog
from gui.metadata_panel import (
    CREDIT_FIELDS,
    IDENTITY_FIELDS,
    PUBLICATION_FIELDS,
    STORY_FIELDS,
    ComicInfoPanel,
)

# Table columns, field-key based -- see redactor_common.core.table_settings's
# own docstring for why (a persisted index-based preference silently
# breaks the moment a column is added/removed/reordered in code).
COLUMN_SPECS: list[tuple[str, str]] = [
    ("filename", "Filename"),
    ("title", "Title"),
    ("series", "Series"),
    ("number", "Number"),
    ("pages", "Pages"),
    ("status", "Status"),
]
_COLUMN_LABELS: dict[str, str] = dict(COLUMN_SPECS)
_ALL_COLUMN_KEYS: list[str] = [key for key, _ in COLUMN_SPECS]
PROTECTED_COLUMNS = frozenset({"filename"})  # the one column you always need to tell rows apart

# Metadata fields offered as %placeholder% tokens in Rename/Export and
# Parse Filename -- a curated subset of every ComicInfo field (the ones
# that actually make sense in a filename), not the full form.
FILENAME_PLACEHOLDERS: list[tuple[str, str]] = [
    ("series", "Series"), ("number", "Number"), ("title", "Title"),
    ("volume", "Volume"), ("year", "Year"), ("publisher", "Publisher"),
    ("writer", "Writer"),
]
NUMERIC_FILENAME_FIELDS = {"number", "volume", "year"}
DEFAULT_RENAME_PATTERN = "%series% %number% - %title%"

LOAD_PROGRESS_THRESHOLD = 3
SAVE_PROGRESS_THRESHOLD = 3
# Slim strip, not zero -- keeps the panel's own toggle button reachable
# (same convention as epubredactor's TAG_PANEL_COLLAPSED_WIDTH). Not
# 32 (redactor_common's own doc-comment default): ComicInfoPanel's
# cover box has its own explicit minimum height/width (see
# metadata_panel.py), which a QSplitter's minimum-size clamping
# enforces regardless of what's requested here -- setting this any
# smaller than that real floor would make SplitterPaneCollapser.
# is_collapsed() permanently disagree with the pane's actual achieved
# width, leaving the toggle button stuck unable to expand it back.
PANEL_COLLAPSED_WIDTH = 70

# attr -> human label, for the "this would overwrite existing data"
# conflict prompt (see MainWindow._resolve_overwrite_conflicts) --
# shared by every metadata-writing path (lookups, bulk edit, Parse
# Filename). Built from the same (label, attr) pairs the metadata form
# itself uses, plus the handful of fields those groups don't cover, so
# the prompt never drifts out of sync with what the form actually
# calls each field.
_FIELD_LABELS: dict[str, str] = {
    attr: label
    for label, attr in [*IDENTITY_FIELDS, *STORY_FIELDS, *CREDIT_FIELDS, *PUBLICATION_FIELDS]
}
_FIELD_LABELS.update(
    {
        "summary": "Summary",
        "notes": "Notes",
        "review": "Review",
        "age_rating": "Age Rating",
        "manga": "Manga",
        "black_and_white": "Black & White",
        "community_rating": "Community Rating",
    }
)


def _field_label(attr: str) -> str:
    return _FIELD_LABELS.get(attr, attr.replace("_", " ").title())


def resource_path(*parts: str) -> str:
    """Resolves a bundled resource (icon, README, ...) whether running
    from source or from a frozen PyInstaller one-file build -- same
    sys._MEIPASS pattern the sibling tools use (safe here specifically
    because this is a read-only bundled asset, unlike core.app_paths.
    base_dir(), which is deliberately NOT sys._MEIPASS for anything
    meant to persist between runs)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)

        self.books: list[CbzBook] = []
        self._selected_rows: list[int] = []

        self._column_keys = merge_column_order(app_settings.load_column_order(), _ALL_COLUMN_KEYS)
        self._col_index = {key: i for i, key in enumerate(self._column_keys)}

        self.table = QTableWidget(0, len(self._column_keys))
        self.table.setHorizontalHeaderLabels([_COLUMN_LABELS[key] for key in self._column_keys])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Multi-select (ctrl/shift-click, same as Explorer) -- needed for
        # both bulk metadata editing (see ComicInfoPanel.set_bulk_mode())
        # and looking up several files via one API search in one go.
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self._setup_column_persistence()

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._show_header_context_menu)

        self.panel = ComicInfoPanel()
        self.panel.set_enabled(False)
        self.panel.fieldsChanged.connect(self._on_fields_changed)
        self.panel.collapseToggleRequested.connect(self._toggle_panel)
        self.panel.bulkApplyRequested.connect(self._apply_bulk_edit)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        # Side panel on the left, table on the right -- matches
        # epubredactor's and videoredactor's own layout (both put their
        # tag_panel/cover+metadata panel first, table second).
        self.splitter.addWidget(self.panel)
        self.splitter.addWidget(self.table)
        self.splitter.setSizes([340, 760])
        self._panel_collapser = SplitterPaneCollapser(
            self.splitter, pane_index=0, collapsed_width=PANEL_COLLAPSED_WIDTH, default_width=340
        )
        self.setCentralWidget(self.splitter)

        self.setStatusBar(QStatusBar())
        self._build_menu()
        self._build_toolbar()
        self._update_status()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        specs = {
            "File": [
                MenuAction("load_files", "&Load Files...", self.load_files_dialog, shortcut="Ctrl+O"),
                MenuAction("load_folder", "Load &Folder...", self.load_folder_dialog, shortcut="Ctrl+Shift+O"),
                Separator(),
                MenuAction("save", "&Save", self.save_current, shortcut="Ctrl+S"),
                MenuAction("save_as", "Save &As...", self.save_current_as, shortcut="Ctrl+Shift+S"),
                Separator(),
                MenuAction("rename_files", "&Rename / Export Files...", self.open_rename_dialog, shortcut="F2"),
                Separator(),
                MenuAction("exit", "E&xit", self.close, shortcut="Ctrl+Q"),
            ],
            "Import": [
                MenuAction("parse_filename", "&Parse Filename...", self.open_parse_filename_dialog, shortcut="F3"),
                MenuAction("convert_cbr", "Convert &CBR to CBZ...", self.convert_cbr_dialog),
                Separator(),
                MenuAction("comicvine_lookup", "Look Up via Comic &Vine...", self.open_comicvine_lookup_dialog),
                MenuAction("gcd_lookup", "Look Up via &Grand Comics Database...", self.open_gcd_lookup_dialog),
            ],
            "Operations": [
                MenuAction("save_all", "Save &All Changed", self.save_all_changed, shortcut="Ctrl+Shift+A"),
            ],
            "Settings": [
                MenuAction("comicvine_api_key", "Comic Vine API &Key...", self.change_comicvine_api_key),
                Separator(),
                MenuAction("column_settings", "Add/Remove &Columns...", self.open_column_settings_dialog),
                MenuAction("genre_settings", "Add/Remove &Genres...", self.open_genre_settings_dialog),
                MenuAction("language_settings", "Add/Remove &Languages...", self.open_language_settings_dialog),
            ],
            "Help": [
                MenuAction("about", f"&About {APP_NAME}", self.open_about_dialog),
                MenuAction("changelog", "View &Changelog", self.open_changelog_dialog),
                MenuAction("credits", "View C&redits", self.open_credits_dialog),
            ],
        }
        self.actions_ = build_menu_bar(self, specs)

    def _build_toolbar(self) -> None:
        """Quick-access buttons for the most common actions -- reuses
        the exact QAction objects the menu bar already built (per
        menu_builder.py's own docstring: "actions['save'] is the QAction,
        reusable on a toolbar"), so enabled state/shortcuts stay in sync
        with the menu automatically rather than needing a second copy."""
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(self.actions_["load_files"])
        toolbar.addSeparator()
        toolbar.addAction(self.actions_["save"])
        toolbar.addAction(self.actions_["save_all"])

    # ------------------------------------------------------------------
    # Columns: order/visibility/widths, persisted by field key
    # ------------------------------------------------------------------

    def _setup_column_persistence(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)  # drag headers to reorder columns
        header.sectionMoved.connect(self._on_columns_reordered)

        hidden = sanitize_hidden_fields(app_settings.load_hidden_columns(), PROTECTED_COLUMNS)
        for key in hidden:
            if key in self._col_index:
                self.table.setColumnHidden(self._col_index[key], True)

        # A persisted width for a field no longer present (e.g. removed
        # in a later version) is silently skipped -- same "preference,
        # not a hard requirement" tolerance as merge_column_order().
        widths = app_settings.load_column_widths()
        for key, width in widths.items():
            if key in self._col_index:
                header.resizeSection(self._col_index[key], width)
        if not widths:
            # First ever run: no saved widths yet -- give Filename the
            # stretch behavior it always had, rather than every column
            # starting at some arbitrary default width.
            header.setSectionResizeMode(self._col_index["filename"], QHeaderView.ResizeMode.Stretch)

    def _on_columns_reordered(self, *_args) -> None:
        """`*_args` absorbs QHeaderView.sectionMoved's (logical,
        old_visual, new_visual) arguments -- not needed here, we just
        re-read the header's current full visual order and persist it."""
        header = self.table.horizontalHeader()
        visual_order = [self._column_keys[header.logicalIndex(v)] for v in range(header.count())]
        app_settings.save_column_order(visual_order)

    def _on_column_visibility_toggled(self, key: str, visible: bool) -> None:
        if key not in self._col_index:
            return
        self.table.setColumnHidden(self._col_index[key], not visible)
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        app_settings.save_hidden_columns(sanitize_hidden_fields(hidden, PROTECTED_COLUMNS))

    def _show_header_context_menu(self, pos) -> None:
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        show_column_header_context_menu(
            self, self.table, pos,
            column_order=self._column_keys,
            label_lookup=_COLUMN_LABELS,
            protected_columns=PROTECTED_COLUMNS,
            hidden_fields=hidden,
            is_visible=lambda key, hidden_set: is_column_visible(key, hidden_set, PROTECTED_COLUMNS),
            on_toggle=self._on_column_visibility_toggled,
            open_column_settings_dialog=self.open_column_settings_dialog,
        )

    def open_column_settings_dialog(self) -> None:
        all_columns = [(key, _COLUMN_LABELS[key]) for key in self._column_keys]
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        dialog = ColumnSettingsDialog(all_columns, hidden, PROTECTED_COLUMNS, self)
        dialog.exec()
        new_hidden = dialog.hidden_fields()
        for key in self._column_keys:
            self.table.setColumnHidden(self._col_index[key], key in new_hidden)
        app_settings.save_hidden_columns(new_hidden)

    def _show_table_context_menu(self, pos) -> None:
        # Selection-fix, and the generic Open Containing Folder/Copy
        # Path actions, are handled by the shared helper.
        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._target_books,
            get_path=lambda book: book.path,
        )

    # ------------------------------------------------------------------
    # Loading files
    # ------------------------------------------------------------------

    def load_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load CBZ Files", "", "Comic Book Archives (*.cbz *.cbr);;All Files (*)"
        )
        if paths:
            self._load_paths(paths)

    def load_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Load Folder")
        if not folder:
            return
        paths = [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder))
            if name.lower().endswith((".cbz", ".cbr"))
        ]
        if not paths:
            QMessageBox.information(self, "No Files Found", "No .cbz or .cbr files were found in that folder.")
            return
        self._load_paths(paths)

    def _load_paths(self, paths: list[str]) -> None:
        errors: list[str] = []

        def _step(path: str, _index: int) -> None:
            resolved_path = path
            if path.lower().endswith(".cbr"):
                try:
                    resolved_path = convert_cbr_to_cbz(path)
                except CbrConversionError as exc:
                    errors.append(f"{os.path.basename(path)}: {exc}")
                    return
            book = CbzBook(resolved_path)
            if book.load_error:
                errors.append(f"{os.path.basename(resolved_path)}: {book.load_error}")
            self.books.append(book)
            self._add_table_row(book)

        run_with_progress(self, paths, _step, "Loading files...", threshold=LOAD_PROGRESS_THRESHOLD)

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Load", summarize_errors(errors))
        self._update_status()

    def _add_table_row(self, book: CbzBook) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._refresh_table_row(row, book)

    def _refresh_table_row(self, row: int, book: CbzBook) -> None:
        status = book.load_error or ("Modified" if book.dirty else "OK")
        if book.page_count_mismatch and not book.load_error:
            status = "Page count mismatch"
        values_by_key = {
            "filename": os.path.basename(book.path),
            "title": book.metadata.title,
            "series": book.metadata.series,
            "number": book.metadata.number,
            "pages": str(book.actual_page_count),
            "status": status,
        }
        for key, value in values_by_key.items():
            self.table.setItem(row, self._col_index[key], QTableWidgetItem(value))

    # ------------------------------------------------------------------
    # Selection / editing
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self._commit_current_edits()
        self._selected_rows = sorted(index.row() for index in self.table.selectionModel().selectedRows())

        if not self._selected_rows:
            self.panel.set_enabled(False)
            return

        self.panel.set_enabled(True)
        if len(self._selected_rows) == 1:
            book = self.books[self._selected_rows[0]]
            self.panel.set_bulk_mode(0)
            page_count_text = f"{book.actual_page_count} page(s)"
            if book.page_count_mismatch:
                page_count_text += f" -- ComicInfo.xml says {book.metadata.page_count}, will be corrected on save"
            self.panel.load_metadata(book.metadata, book.read_first_page_bytes(), page_count_text)
        else:
            self.panel.set_bulk_mode(len(self._selected_rows))

    def _commit_current_edits(self) -> None:
        """Writes the panel's current widget values back into whichever
        book was selected *before* the selection changes -- otherwise
        an in-progress edit is silently discarded the instant the user
        clicks a different row. Only applies in single-selection mode;
        a bulk edit in progress is deliberately NOT auto-committed just
        because the selection changed -- see ComicInfoPanel's module
        docstring on why that needs an explicit Apply instead."""
        if len(self._selected_rows) != 1:
            return
        row = self._selected_rows[0]
        if row >= len(self.books):
            return
        self.panel.apply_to_metadata(self.books[row].metadata)

    def _on_fields_changed(self) -> None:
        if len(self._selected_rows) != 1:
            return  # bulk mode: nothing applies until the explicit Apply button
        row = self._selected_rows[0]
        self.books[row].dirty = True
        self._refresh_table_row(row, self.books[row])

    def _apply_bulk_edit(self) -> None:
        changed_fields = self.panel.bulk_changed_fields()
        if not changed_fields:
            QMessageBox.information(self, "Nothing to Apply", "No fields were filled in.")
            return

        target_books = [self.books[row] for row in self._selected_rows]
        metadata_changes = {i: dict(changed_fields) for i in range(len(target_books))}
        metadata_changes = self._resolve_overwrite_conflicts(target_books, metadata_changes)
        if metadata_changes is None:
            return

        for i, fields in metadata_changes.items():
            book = target_books[i]
            for attr, value in fields.items():
                setattr(book.metadata, attr, value)
            book.dirty = True
            self._refresh_table_row(self.books.index(book), book)

        self.panel.set_bulk_mode(len(self._selected_rows))  # clears the fields, ready for another round
        self._update_status()

    def _toggle_panel(self) -> None:
        self._panel_collapser.toggle()
        self.panel.collapse_toggle_btn.set_collapsed(self._panel_collapser.is_collapsed())

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save_current(self) -> None:
        """Saves every selected file -- one file, the usual case, saves
        exactly like before; several selected at once saves all of
        them, matching a normal multi-select "Save" convention."""
        self._commit_current_edits()
        if not self._selected_rows:
            return
        if len(self._selected_rows) == 1:
            self._save_book(self._selected_rows[0])
            return

        errors: list[str] = []
        for row in self._selected_rows:
            book = self.books[row]
            try:
                book.save()
            except CbzError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
            self._refresh_table_row(row, book)
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Save", summarize_errors(errors))
        self._update_status()

    def save_current_as(self) -> None:
        self._commit_current_edits()
        if len(self._selected_rows) != 1:
            QMessageBox.information(self, "Save As", "Select exactly one file to Save As.")
            return
        row = self._selected_rows[0]
        book = self.books[row]
        path, _ = QFileDialog.getSaveFileName(self, "Save As", book.path, "Comic Book ZIP (*.cbz)")
        if path:
            self._save_book(row, output_path=path)

    def _save_book(self, row: int, output_path: str | None = None) -> None:
        book = self.books[row]
        try:
            book.save(output_path)
        except CbzError as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
        self._refresh_table_row(row, book)
        self._update_status()

    def save_all_changed(self) -> None:
        self._commit_current_edits()
        changed_rows = [i for i, book in enumerate(self.books) if book.dirty]
        if not changed_rows:
            QMessageBox.information(self, "Nothing to Save", "No files have unsaved changes.")
            return

        errors: list[str] = []

        def _step(row: int, _index: int) -> None:
            book = self.books[row]
            try:
                book.save()
            except CbzError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
            self._refresh_table_row(row, book)

        run_with_progress(self, changed_rows, _step, "Saving files...", threshold=SAVE_PROGRESS_THRESHOLD)

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Save", summarize_errors(errors))
        self._update_status()

    # ------------------------------------------------------------------
    # Rename / Export by pattern, and the reverse: Parse Filename
    # ------------------------------------------------------------------

    def open_rename_dialog(self) -> None:
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to rename).")
            return

        def get_values(book: CbzBook) -> dict[str, str]:
            return {key: getattr(book.metadata, key, "") for key, _ in FILENAME_PLACEHOLDERS}

        dialog = RenamePatternDialog(
            target_books, FILENAME_PLACEHOLDERS, get_values, lambda book: book.path,
            pattern_history=app_settings.load_pattern_history(),
            default_pattern=DEFAULT_RENAME_PATTERN,
            title="Rename / Export by Metadata Pattern",
            item_noun="file",
            zero_pad_field="number",
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        app_settings.save_pattern_used(dialog.pattern_edit.text())
        export_mode = dialog.is_export_mode()
        errors: list[str] = []
        for book, old_path, new_path in dialog.planned_renames():
            try:
                if export_mode:
                    shutil.copy2(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    book.path = new_path
            except OSError as exc:
                errors.append(f"{os.path.basename(old_path)}: {exc}")

        for book in target_books:
            self._refresh_table_row(self.books.index(book), book)
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed", summarize_errors(errors))
        self._update_status()

    def open_parse_filename_dialog(self) -> None:
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to parse).")
            return

        dialog = ParseFilenameDialog(
            target_books, FILENAME_PLACEHOLDERS, lambda book: book.path,
            pattern_history=app_settings.load_pattern_history(),
            default_pattern=DEFAULT_RENAME_PATTERN,
            valid_field_keys={key for key, _ in FILENAME_PLACEHOLDERS},
            numeric_fields=NUMERIC_FILENAME_FIELDS,
            strip_leading_zeros_fields={"number"},
            title="Parse Filename → Metadata",
            item_noun="file",
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        app_settings.save_pattern_used(dialog.pattern_edit.text())
        changes = dialog.accepted_changes()  # index into target_books -> {field: value}
        if not changes:
            return

        changes = self._resolve_overwrite_conflicts(target_books, changes)
        if changes is None:
            return

        for index, fields in changes.items():
            book = target_books[index]
            for attr, value in fields.items():
                setattr(book.metadata, attr, value)
            book.dirty = True
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                page_count_text = f"{book.actual_page_count} page(s)"
                self.panel.load_metadata(book.metadata, book.read_first_page_bytes(), page_count_text)
        self._update_status()

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def convert_cbr_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Convert CBR to CBZ", "", "CBR Files (*.cbr)")
        if not paths:
            return
        errors: list[str] = []
        converted: list[str] = []

        def _step(path: str, _index: int) -> None:
            try:
                converted.append(convert_cbr_to_cbz(path))
            except CbrConversionError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")

        run_with_progress(self, paths, _step, "Converting CBR files...", threshold=LOAD_PROGRESS_THRESHOLD)

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Convert", summarize_errors(errors))
        if converted:
            QMessageBox.information(
                self, "Conversion Complete", f"Converted {len(converted)} file(s) to .cbz."
            )
            self._load_paths(converted)

    def _target_books(self) -> list[CbzBook]:
        """The selected book(s), or every loaded book if none is
        selected -- same fallback epubredactor's own lookup dialogs
        use, so "look up this one file", "look up these N I selected",
        and "look up everything I loaded" all work without a separate
        mode switch."""
        if self._selected_rows:
            return [self.books[row] for row in self._selected_rows]
        return list(self.books)

    def _run_lookup_dialog(self, dialog_class) -> None:
        """Shared flow for every online lookup dialog (Comic Vine, GCD,
        ...): they all take (target_books, parent) and expose the same
        accepted_metadata() -> {index: {field: value}} shape (see
        gui/comicvine_lookup_dialog.py / gui/gcd_lookup_dialog.py), so
        opening one, applying its results, and refreshing the affected
        rows is identical regardless of which source it is."""
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first.")
            return

        dialog = dialog_class(target_books, self)
        if dialog.exec() != dialog_class.DialogCode.Accepted:
            return

        metadata_changes = dialog.accepted_metadata()  # index into target_books -> {field: value}
        if not metadata_changes:
            return

        metadata_changes = self._resolve_overwrite_conflicts(target_books, metadata_changes)
        if metadata_changes is None:
            return  # user cancelled outright

        for index, fields in metadata_changes.items():
            book = target_books[index]
            for attr, value in fields.items():
                setattr(book.metadata, attr, value)
            book.dirty = True
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                page_count_text = f"{book.actual_page_count} page(s)"
                self.panel.load_metadata(book.metadata, book.read_first_page_bytes(), page_count_text)
        self._update_status()

    def _resolve_overwrite_conflicts(
        self, target_books: list[CbzBook], metadata_changes: dict[int, dict[str, str]]
    ) -> dict[int, dict[str, str]] | None:
        """Checks whether applying `metadata_changes` would overwrite
        any field that already has a non-blank, different value, and
        if so asks once how to proceed -- applies uniformly no matter
        which path produced the changes (a lookup, bulk edit, or Parse
        Filename all funnel through this same method).

        Returns the changes to actually apply (all of them, or only
        the ones that don't clobber existing data), or None if the
        user cancelled outright."""
        conflicts: list[tuple[str, str]] = []  # (filename, field label) pairs, for the prompt text
        for index, fields in metadata_changes.items():
            book = target_books[index]
            for attr, new_value in fields.items():
                current_value = (getattr(book.metadata, attr, "") or "").strip()
                if current_value and current_value != new_value:
                    conflicts.append((os.path.basename(book.path), _field_label(attr)))

        if not conflicts:
            return metadata_changes

        preview = "; ".join(f"{name} ({label})" for name, label in conflicts[:5])
        if len(conflicts) > 5:
            preview += f", and {len(conflicts) - 5} more"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Existing Metadata Found")
        box.setText(
            f"{len(conflicts)} field(s) already have a value that this would "
            f"overwrite:\n\n{preview}\n\nHow do you want to proceed?"
        )
        overwrite_btn = box.addButton("Overwrite All", QMessageBox.ButtonRole.AcceptRole)
        keep_btn = box.addButton("Keep Existing (fill blanks only)", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is overwrite_btn:
            return metadata_changes
        if clicked is not keep_btn:
            return None  # Cancel (or the dialog was closed)

        filtered: dict[int, dict[str, str]] = {}
        for index, fields in metadata_changes.items():
            book = target_books[index]
            kept = {
                attr: value
                for attr, value in fields.items()
                if not (getattr(book.metadata, attr, "") or "").strip()
            }
            if kept:
                filtered[index] = kept
        return filtered

    def open_comicvine_lookup_dialog(self) -> None:
        self._run_lookup_dialog(ComicVineLookupDialog)

    def open_gcd_lookup_dialog(self) -> None:
        self._run_lookup_dialog(GcdLookupDialog)

    def change_comicvine_api_key(self) -> None:
        current = app_settings.load_comicvine_api_key()
        text, ok = QInputDialog.getText(
            self,
            "Comic Vine API Key",
            "Enter your Comic Vine API key (free -- register at comicvine.gamespot.com/api/):",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if ok:
            app_settings.save_comicvine_api_key(text)

    # ------------------------------------------------------------------
    # Settings menu: Genres / Languages
    # ------------------------------------------------------------------

    def open_genre_settings_dialog(self) -> None:
        def load_defaults_fn() -> list[tuple[str, str]]:
            return [(g, g) for g in app_settings.load_visible_default_genres()]

        def load_custom_fn() -> list[tuple[str, str]]:
            return [(g, g) for g in app_settings.load_custom_genres()]

        def add_dialog_fn(parent_widget) -> None:
            text, ok = QInputDialog.getText(parent_widget, "Add Genre", "New genre name:")
            if ok and text.strip():
                app_settings.add_custom_genre(text.strip())

        dialog = ManageListDialog(
            "Add/Remove Genres",
            load_defaults_fn, load_custom_fn, add_dialog_fn,
            remove_custom_fn=app_settings.remove_custom_genre,
            hide_default_fn=app_settings.hide_default_genre,
            restore_defaults_fn=app_settings.restore_default_genres,
            parent=self,
        )
        dialog.exec()

    def open_language_settings_dialog(self) -> None:
        def load_defaults_fn() -> list[tuple[str, str]]:
            return [(code, f"{name} ({code})") for code, name in app_settings.load_visible_default_languages()]

        def load_custom_fn() -> list[tuple[str, str]]:
            return [(code, f"{name} ({code})") for code, name in app_settings.load_custom_languages()]

        def add_dialog_fn(parent_widget) -> None:
            code, ok = QInputDialog.getText(
                parent_widget, "Add Custom Language", 'Language code (ISO 639-1, e.g. "pt" for Portuguese):'
            )
            code = code.strip()
            if not (ok and code):
                return
            name, ok = QInputDialog.getText(parent_widget, "Add Custom Language", "Display name for this language:")
            if ok and name.strip():
                app_settings.add_custom_language(code, name.strip())

        dialog = ManageListDialog(
            "Add/Remove Languages",
            load_defaults_fn, load_custom_fn, add_dialog_fn,
            remove_custom_fn=app_settings.remove_custom_language,
            hide_default_fn=app_settings.hide_default_language,
            restore_defaults_fn=app_settings.restore_default_languages,
            parent=self,
        )
        dialog.exec()

    # ------------------------------------------------------------------
    # Help menu
    # ------------------------------------------------------------------

    def open_about_dialog(self) -> None:
        dialog = AboutDialog(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            release_label=RELEASE_LABEL,
            icon_path=resource_path("assets", "icon.ico"),
            about_path=resource_path("ABOUT.md"),
            component_versions={"redactor_common": REDACTOR_COMMON_VERSION},
            repo_url=APP_REPO_URL,
            component_repo_urls={"redactor_common": REDACTOR_COMMON_REPO_URL},
            parent=self,
        )
        dialog.exec()

    def open_changelog_dialog(self) -> None:
        ChangelogDialog(resource_path("CHANGELOG.md"), parent=self).exec()

    def open_credits_dialog(self) -> None:
        CreditsDialog(resource_path("CREDITS.md"), parent=self).exec()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        changed = sum(1 for book in self.books if book.dirty)
        self.statusBar().showMessage(f"{len(self.books)} file(s) loaded, {changed} with unsaved changes")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        self._commit_current_edits()
        changed = [book for book in self.books if book.dirty]
        if changed:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"{len(changed)} file(s) have unsaved changes. Close anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        # Column widths are only persisted here (not live on every
        # resize-drag tick) -- order and visibility persist immediately
        # since a header-menu toggle should be reflected right away,
        # but a width is only worth writing once, when it's settled.
        header = self.table.horizontalHeader()
        widths = {key: header.sectionSize(self._col_index[key]) for key in self._column_keys}
        app_settings.save_column_widths(widths)

        event.accept()
