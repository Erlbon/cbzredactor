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

import copy
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
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from redactor_common.core.table_settings import is_column_visible, merge_column_order, sanitize_hidden_fields
from redactor_common.core.undo import UndoManager
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.gui.action_factory import make_action
from redactor_common.gui.case_conversion_dialog import CaseConversionDialog
from redactor_common.gui.auto_numbering_dialog import AutoNumberingDialog
from redactor_common.gui.quick_series_number import prompt_and_generate_series_numbers
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.colors import DIRTY_COLOR, ERROR_COLOR, HIGHLIGHT_TEXT_COLOR, TABLE_SELECTION_STYLESHEET
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.gui.manage_list_dialog import ManageListDialog
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.parse_filename_dialog import ParseFilenameDialog
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog
from redactor_common.gui.rename_single_file import rename_single_file as prompt_rename_single_file
from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY, SearchReplaceDialog
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

from core.cbr_convert import CbrConversionError, convert_cbr_to_cbz
from core.cbz_file import CbzBook, CbzError
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui import app_settings
from gui.bedetheque_lookup_dialog import BedethequeLookupDialog
from gui.comicvine_lookup_dialog import ComicVineLookupDialog
from gui.gcd_lookup_dialog import GcdLookupDialog
from gui.overwrite_review_dialog import OverwriteReviewDialog, build_overwrite_review_rows
from gui.resize_dialog import ResizeImagesDialog
from gui.metadata_panel import (
    CREDIT_FIELDS,
    IDENTITY_FIELDS,
    PUBLICATION_FIELDS,
    STORY_FIELDS,
    ComicInfoPanel,
)

# attr -> human label, for the "this would overwrite existing data"
# conflict prompt (see MainWindow._resolve_overwrite_conflicts) --
# shared by every metadata-writing path (lookups, bulk edit, Parse
# Filename) -- AND for building the full column list just below, so
# every field the form can edit is also available as a table column
# (hidden by default -- see DEFAULT_HIDDEN_COLUMNS), matching
# ComicRack's own "everything is an optional column" convention. Built
# from the same (label, attr) pairs the metadata form itself uses, plus
# the handful of fields those groups don't cover, so this never drifts
# out of sync with what the form actually calls each field.
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


# Fields genuinely numeric per the ComicInfo schema itself -- get
# Auto-Numbering's direct-write treatment. Everything else in
# _FIELD_LABELS is still offered, just prefixed onto its existing
# value instead (same conservative default video's own NUMERIC_FIELDS
# used: not every field that CAN hold a number should be overwritten
# by one).
_AUTO_NUMBER_NUMERIC_FIELDS: frozenset[str] = frozenset(
    {"number", "count", "volume", "alternate_number", "alternate_count", "story_arc_number"}
)


# Table columns, field-key based -- see redactor_common.core.table_settings's
# own docstring for why (a persisted index-based preference silently
# breaks the moment a column is added/removed/reordered in code).
# "filename"/"pages"/"status" are synthetic (derived, not a literal
# ComicInfo field); everything else is every field _FIELD_LABELS knows
# about, i.e. every field the side panel can edit.
COLUMN_SPECS: list[tuple[str, str]] = (
    [("filename", "Filename")]
    + list(_FIELD_LABELS.items())
    + [("pages", "Pages"), ("status", "Status")]
)
_COLUMN_LABELS: dict[str, str] = dict(COLUMN_SPECS)
_ALL_COLUMN_KEYS: list[str] = [key for key, _ in COLUMN_SPECS]
PROTECTED_COLUMNS = frozenset({"filename"})  # the one column you always need to tell rows apart

# What a brand-new install shows by default -- everything else (every
# other ComicInfo field) is available but starts hidden, same
# "exhaustive but mostly tucked away" shape as ComicRack's own column
# chooser. Only applied on a genuinely first run -- see
# gui/app_settings.py's has_hidden_columns_preference(); once the user
# has touched column visibility at all (via the header menu or Settings
# > Add/Remove Columns...), their own saved choice always wins, even if
# that choice is "show everything".
_DEFAULT_VISIBLE_COLUMNS = frozenset({"filename", "title", "series", "number", "pages", "status"})
DEFAULT_HIDDEN_COLUMNS: frozenset[str] = frozenset(_ALL_COLUMN_KEYS) - _DEFAULT_VISIBLE_COLUMNS

# Metadata fields offered as %placeholder% tokens in Rename/Export and
# Parse Filename -- every field the side panel can edit, same as
# COLUMN_SPECS just above, built from the same _FIELD_LABELS dict so
# all three (columns, panel fields, filename placeholders) never drift
# out of sync with each other. Used to be a curated 7-field subset
# (Series/Number/Title/Volume/Year/Publisher/Writer only) until the
# user pointed out a field with real metadata -- Genre, Story Arc,
# whatever -- couldn't be represented in a filename pattern just
# because it wasn't on that original short list.
FILENAME_PLACEHOLDERS: list[tuple[str, str]] = list(_FIELD_LABELS.items())
# Fields Parse Filename should extract/coerce as numbers (see
# ParseFilenameDialog) rather than leaving as free-text strings. Note:
# PageCount isn't here (or in _FIELD_LABELS at all) -- it's never
# hand-edited, always recomputed from the archive's actual image count
# at save time (see ComicInfoPanel.apply_to_metadata()'s docstring).
NUMERIC_FILENAME_FIELDS = {
    "number", "count", "volume", "alternate_number", "alternate_count",
    "year", "month", "day", "community_rating",
}
DEFAULT_RENAME_PATTERN = "%series% %number% - %title%"

LOAD_PROGRESS_THRESHOLD = 3
SAVE_PROGRESS_THRESHOLD = 3
# 1, not 3 like the others -- resizing actually decodes/re-encodes
# every oversized page, so even a single large file is worth a
# cancellable progress dialog, unlike a routine metadata save.
RESIZE_PROGRESS_THRESHOLD = 1
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
        self.undo_manager: UndoManager[CbzBook] = UndoManager()
        # Click-to-sort state (see _on_header_clicked) -- not persisted
        # across restarts, same as every other app in the family not
        # remembering a sort order (only column order/widths/visibility
        # are). Reset (not restored) any time the list is rebuilt from
        # a different source (Load/Refresh/Clear).
        self._sort_key: str | None = None
        self._sort_ascending: bool = True

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
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setStyleSheet(TABLE_SELECTION_STYLESHEET)  # current-cell focus outline
        self._setup_column_persistence()

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._show_header_context_menu)

        self.panel = ComicInfoPanel()
        self.panel.set_enabled(False)
        self.panel.fieldsChanged.connect(self._on_fields_changed)
        self.panel.collapseToggleRequested.connect(self._toggle_panel)
        self._sync_panel_visible_fields()

        self.zoom = TableZoomController(self.table, parent=self)

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
                MenuAction("remove_files", "Remo&ve Files", self.remove_selected, shortcut="Delete"),
                Separator(),
                MenuAction("refresh_list", "Re&fresh List", self.refresh_list, shortcuts=["F5", "Ctrl+R"]),
                MenuAction("clear_list", "&Clear List", self.clear_list),
                Separator(),
                MenuAction("exit", "E&xit", self.close, shortcut="Ctrl+Q"),
            ],
            "Import": [
                MenuAction("parse_filename", "&Parse Filename...", self.open_parse_filename_dialog, shortcut="F3"),
                MenuAction("convert_cbr", "Convert &CBR to CBZ...", self.convert_cbr_dialog),
                Separator(),
                MenuAction("comicvine_lookup", "Look Up via Comic &Vine...", self.open_comicvine_lookup_dialog),
                MenuAction("gcd_lookup", "Look Up via &Grand Comics Database...", self.open_gcd_lookup_dialog),
                MenuAction("bedetheque_lookup", "Look Up via &Bedetheque...", self.open_bedetheque_lookup_dialog),
            ],
            "Operations": [
                # Shared with the toolbar (see _build_toolbar) -- one
                # QAction instance, so its dynamic "Apply to N selected
                # file(s)" text and enabled state never drift out of
                # sync between the two places it appears.
                MenuAction("apply_bulk_edit", "&Apply to 0 Selected File(s)", self._apply_bulk_edit),
                MenuAction("search_replace", "&Search/Replace...", self.open_search_replace_dialog),
                MenuAction("case_conversion", "&Case Conversion...", self.open_case_conversion_dialog),
                MenuAction("auto_numbering", "Auto-&Numbering...", self.open_auto_numbering_dialog),
                Separator(),
                MenuAction("resize_images", "Resi&ze Images...", self.open_resize_images_dialog),
                Separator(),
                MenuAction("save_all", "Save &All Changed", self.save_all_changed, shortcut="Ctrl+Shift+A"),
                Separator(),
                MenuAction("undo", "&Undo", self.undo_last_action, shortcut="Ctrl+Z"),
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
        self.actions_["apply_bulk_edit"].setEnabled(False)
        self.actions_["undo"].setEnabled(False)

    def _build_toolbar(self) -> None:
        """Quick-access buttons for the most common actions -- reuses
        the exact QAction objects the menu bar already built (per
        menu_builder.py's own docstring: "actions['save'] is the QAction,
        reusable on a toolbar"), so enabled state/shortcuts stay in sync
        with the menu automatically rather than needing a second copy.
        Same shape as epubredactor's own toolbar: the frequent actions
        on the left, a Panel toggle + zoom control pushed to the far
        right by an expanding spacer."""
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)

        toolbar.addAction(self.actions_["load_files"])
        toolbar.addAction(self.actions_["load_folder"])
        toolbar.addSeparator()
        toolbar.addAction(self.actions_["save"])
        toolbar.addSeparator()
        toolbar.addAction(self.actions_["apply_bulk_edit"])
        toolbar.addSeparator()
        toolbar.addAction(self.actions_["undo"])
        toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        toggle_panel_act = make_action(self, "Panel", self._toggle_panel)
        toggle_panel_act.setToolTip("Minimize or restore the metadata panel")
        toolbar.addAction(toggle_panel_act)
        toolbar.addSeparator()

        toolbar.addAction(self.zoom.zoom_out_action)
        toolbar.addWidget(self.zoom.label)
        toolbar.addAction(self.zoom.zoom_in_action)

    # ------------------------------------------------------------------
    # Columns: order/visibility/widths, persisted by field key
    # ------------------------------------------------------------------

    def _setup_column_persistence(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)  # drag headers to reorder columns
        header.sectionMoved.connect(self._on_columns_reordered)
        # Deliberately NOT QTableWidget.setSortingEnabled(True): that
        # would have Qt do its own item-based re-sort, physically moving
        # QTableWidgetItems between rows -- which would silently break
        # every "row N is self.books[N]" assumption elsewhere in this
        # file (save, remove, apply-bulk-edit, lookups, and more all
        # index into self.books by table row). Instead, a header click
        # sorts self.books itself and rebuilds the table from it (see
        # _on_header_clicked), so that invariant never breaks -- the
        # exact same "rebuild from self.books" pattern _rebuild_table()
        # already uses for Refresh/Clear/Remove. setSortIndicatorShown()
        # is deliberately NOT turned on here -- Qt defaults its section/
        # order to column 0 ascending the moment it's shown, which would
        # display a sort arrow implying the list is already sorted by
        # Filename when it's actually still in plain load order.
        # _on_header_clicked() turns it on the first time a real sort
        # happens instead.
        header.sectionClicked.connect(self._on_header_clicked)

        # A genuinely first run (the user has never touched column
        # visibility at all) gets DEFAULT_HIDDEN_COLUMNS -- otherwise
        # every field would show as a column immediately, which is
        # exhaustive but overwhelming for a brand-new install. Once
        # they've saved ANY choice, even "show everything" (an empty
        # hidden set), that saved choice always wins -- see
        # app_settings.has_hidden_columns_preference()'s own docstring.
        if app_settings.has_hidden_columns_preference():
            raw_hidden = app_settings.load_hidden_columns()
        else:
            raw_hidden = set(DEFAULT_HIDDEN_COLUMNS)
        hidden = sanitize_hidden_fields(raw_hidden, PROTECTED_COLUMNS)
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
        self._sync_panel_visible_fields()

    def _sync_panel_visible_fields(self) -> None:
        """Keeps the side panel's visible edit rows in lock-step with
        which columns are currently shown in the table -- hiding a
        column (header right-click, or Settings > Add/Remove
        Columns...) also stops cluttering the panel with a field you
        said you don't care about, and un-hiding a column brings its
        row straight back. The field's data is untouched either way
        (see ComicInfoPanel.set_visible_fields()'s own docstring) --
        this only ever changes what's drawn on screen."""
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        self.panel.set_visible_fields(set(_FIELD_LABELS) - hidden)

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
        self._sync_panel_visible_fields()

    def _show_table_context_menu(self, pos) -> None:
        # Selection-fix, and the generic Open Containing Folder/Copy
        # Path actions, are handled by the shared helper.
        def extra_items(_books: list[CbzBook]) -> list:
            # Deliberately checks self._selected_rows directly, not the
            # `_books` param (get_selected_items=self._target_books,
            # which falls back to "every loaded book" when nothing's
            # selected) -- both actions below only make sense against a
            # genuine selection, not "there happens to be only N books
            # loaded total".
            items: list = []
            # Renaming several files to the same name doesn't make
            # sense, so this is only offered for exactly one selected
            # book. Distinct from "Rename / Export Files..." (File
            # menu): that's the pattern-based batch tool; this is the
            # quick, direct fix for one typo at a time -- also
            # reachable by double-clicking the Filename cell (see
            # _on_cell_double_clicked()).
            if len(self._selected_rows) == 1:
                book = self.books[self._selected_rows[0]]
                if not book.load_error:
                    items.append(MenuAction(
                        "rename_file", "Rename File...", lambda: self.rename_single_file(book)
                    ))
            if self._selected_rows:
                selected_books = [self.books[r] for r in self._selected_rows]
                items.append(MenuAction(
                    "number_issues", "Number Issues...", lambda: self._quick_number_issues(selected_books)
                ))
            if items:
                items.insert(0, Separator())
            return items

        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._target_books,
            get_path=lambda book: book.path,
            extra_items=extra_items,
        )

    # ------------------------------------------------------------------
    # Loading files
    # ------------------------------------------------------------------

    def load_files_dialog(self) -> None:
        start_dir = app_settings.load_last_directory()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load CBZ Files", start_dir, "Comic Book Archives (*.cbz *.cbr);;All Files (*)"
        )
        if paths:
            app_settings.save_last_directory(paths[0])
            self._load_paths(paths)

    def load_folder_dialog(self) -> None:
        start_dir = app_settings.load_last_directory()
        folder = QFileDialog.getExistingDirectory(self, "Load Folder", start_dir)
        if not folder:
            return
        app_settings.save_last_directory(folder)
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

        # Newly loaded files were just appended to the end of the table
        # above -- if a column sort is currently active, keep it applied
        # rather than letting new arrivals silently break it (this also
        # covers Refresh List and Convert CBR/Resize Images' "load the
        # result back in" calls, all of which route through here).
        if self._sort_key:
            self.books.sort(
                key=lambda book: self._sort_key_for(book, self._sort_key), reverse=not self._sort_ascending
            )
            self._rebuild_table()

        self._update_status()

    def _add_table_row(self, book: CbzBook) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._refresh_table_row(row, book)

    def _refresh_table_row(self, row: int, book: CbzBook) -> None:
        status = book.load_error or ("Modified" if book.dirty else "OK")
        if book.page_count_mismatch and not book.load_error:
            status = "Page count mismatch"

        self.table.setItem(row, self._col_index["filename"], QTableWidgetItem(os.path.basename(book.path)))
        self.table.setItem(row, self._col_index["pages"], QTableWidgetItem(str(book.actual_page_count)))
        self.table.setItem(row, self._col_index["status"], QTableWidgetItem(status))
        # Every other column is a plain ComicInfo field -- one shared
        # loop covers all of them (title/series/number plus every field
        # only reachable as a column once you turn it on; see
        # DEFAULT_HIDDEN_COLUMNS) instead of hand-listing each one.
        for attr in _FIELD_LABELS:
            value = getattr(book.metadata, attr, "")
            self.table.setItem(row, self._col_index[attr], QTableWidgetItem(value))

        self._apply_row_status_color(row, book)

    def _apply_row_status_color(self, row: int, book: CbzBook) -> None:
        """Tints every cell in the row so a problem file (failed to
        load) or an unsaved change (dirty, or a page-count mismatch
        that'll be corrected on save) is visible at a glance across the
        whole row -- matching epub/mp3/video's own row-tinting via
        redactor_common.gui.colors, which this app never had at all
        until now (its Status column was plain text only)."""
        if book.load_error:
            color = ERROR_COLOR
        elif book.dirty or book.page_count_mismatch:
            color = DIRTY_COLOR
        else:
            color = None

        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is None:
                continue
            if color is not None:
                item.setBackground(color)
                item.setForeground(HIGHLIGHT_TEXT_COLOR)
            else:
                # Clear any override entirely (pass None, not an empty
                # QBrush -- QBrush()'s default color is black, which
                # would silently force black text regardless of theme).
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
                item.setData(Qt.ItemDataRole.ForegroundRole, None)

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
        self._update_apply_bulk_edit_action()

    def _update_apply_bulk_edit_action(self) -> None:
        count = len(self._selected_rows) if self.panel.bulk_mode else 0
        self.actions_["apply_bulk_edit"].setText(f"&Apply to {count} Selected File(s)")
        self.actions_["apply_bulk_edit"].setEnabled(self.panel.bulk_mode)

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
        if not self.panel.bulk_mode:
            return
        changed_fields = self.panel.bulk_changed_fields()
        if not changed_fields:
            QMessageBox.information(self, "Nothing to Apply", "No fields were filled in.")
            return

        target_books = [self.books[row] for row in self._selected_rows]
        metadata_changes = {i: dict(changed_fields) for i in range(len(target_books))}
        metadata_changes = self._resolve_overwrite_conflicts(target_books, metadata_changes)
        if metadata_changes is None:
            return

        self._push_undo("Bulk edit", target_books)
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
    # Undo -- in-memory metadata/dirty-flag edits only (bulk edits,
    # lookups, Parse Filename, Search/Replace, Case Conversion).
    # Deliberately excludes physical file operations (Rename/Export,
    # Save, filename-field Search/Replace) -- see
    # redactor_common.core.undo's own module docstring for why.
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_book(book: CbzBook) -> dict:
        return {"metadata": copy.deepcopy(book.metadata), "dirty": book.dirty}

    @staticmethod
    def _restore_book(book: CbzBook, snapshot: dict) -> None:
        book.metadata = snapshot["metadata"]
        book.dirty = snapshot["dirty"]

    def _push_undo(self, label: str, books: list[CbzBook]) -> None:
        """Call BEFORE mutating `books`, to capture their pre-change
        state."""
        self.undo_manager.push(label, books, self._snapshot_book)
        self._update_undo_action()

    def _update_undo_action(self) -> None:
        can_undo = self.undo_manager.can_undo()
        self.actions_["undo"].setEnabled(can_undo)
        label = self.undo_manager.peek_label()
        self.actions_["undo"].setText(f"&Undo {label}" if label else "&Undo")

    def undo_last_action(self) -> None:
        affected = self.undo_manager.undo(self._restore_book)
        for book in affected:
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                page_count_text = f"{book.actual_page_count} page(s)"
                self.panel.load_metadata(book.metadata, book.read_first_page_bytes(), page_count_text)
        self._update_undo_action()
        self._update_status()

    # ------------------------------------------------------------------
    # List management: remove / clear / refresh
    # ------------------------------------------------------------------

    def _rebuild_table(self) -> None:
        self.table.setRowCount(0)
        for book in self.books:
            self._add_table_row(book)

    # ------------------------------------------------------------------
    # Sorting -- click a column header
    # ------------------------------------------------------------------

    def _on_header_clicked(self, logical_index: int) -> None:
        """Clicking the same header again reverses direction; clicking a
        different one starts a fresh ascending sort on it, same
        convention as a spreadsheet or Explorer's Details view.

        Sorts self.books itself (then rebuilds the table from it, same
        as _rebuild_table()'s other callers) rather than reordering the
        table's own QTableWidgetItems in place -- see
        _setup_column_persistence()'s comment on why that matters."""
        if logical_index not in self._col_index.values():
            return
        key = self._column_keys[logical_index]

        self._commit_current_edits()
        if key == self._sort_key:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_key = key
            self._sort_ascending = True

        self.books.sort(key=lambda book: self._sort_key_for(book, key), reverse=not self._sort_ascending)
        self._rebuild_table()

        order = Qt.SortOrder.AscendingOrder if self._sort_ascending else Qt.SortOrder.DescendingOrder
        header = self.table.horizontalHeader()
        header.setSortIndicator(logical_index, order)
        header.setSortIndicatorShown(True)

        # Rebuilding moved every row -- there's no single sensible row
        # left "selected" (the files themselves are still all there,
        # just in a new order), so clear rather than leave a stale
        # highlight sitting on whatever file happens to now occupy that
        # row number.
        self.table.clearSelection()
        self._selected_rows = []
        self.panel.set_enabled(False)

    def _sort_key_for(self, book: CbzBook, key: str) -> tuple[int, float] | str:
        """(0, value) for a field that parses as a number on THIS row
        (sorts numerically -- "2" before "10", not after) or (1, 0.0)
        for one that doesn't (sorts after every numeric value, in
        whichever direction); a plain casefolded string otherwise, for
        ordinary alphabetical (case-insensitive) sorting."""
        if key == "filename":
            raw = os.path.basename(book.path)
        elif key == "pages":
            raw = str(book.actual_page_count)
        elif key == "status":
            raw = book.load_error or ("Modified" if book.dirty else "OK")
        else:
            raw = getattr(book.metadata, key, "")

        if key in NUMERIC_FILENAME_FIELDS or key == "pages":
            try:
                return (0, float(raw))
            except (TypeError, ValueError):
                return (1, 0.0)
        return raw.strip().casefold()

    def _count_dirty(self) -> int:
        return sum(1 for book in self.books if book.dirty)

    def _confirm_discard(self, action_description: str) -> bool:
        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            f"This will {action_description}. Unsaved changes will be lost. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def remove_selected(self) -> None:
        """Removes the selected files from this list only -- never
        touches anything on disk (see Delete Files, if this app grows
        one, for that; not offered here yet). Excludes by row index,
        not object identity -- CbzBook is a plain @dataclass, so it has
        a value-based __eq__ (and is therefore unhashable), making a
        set-of-books membership check both wrong (two files with
        identical in-memory state would compare equal) and impossible
        (TypeError: unhashable) rather than just imprecise."""
        if not self._selected_rows:
            return
        to_remove = set(self._selected_rows)
        self.books = [book for i, book in enumerate(self.books) if i not in to_remove]
        self._selected_rows = []
        self.undo_manager.clear()  # its entries would reference book objects just discarded
        self._update_undo_action()
        self._rebuild_table()
        self.panel.set_enabled(False)
        self._update_status()

    def clear_list(self) -> None:
        if not self.books:
            return
        if self._count_dirty() and not self._confirm_discard("clear the entire list"):
            return
        self.books = []
        self._selected_rows = []
        self.undo_manager.clear()
        self._update_undo_action()
        self._rebuild_table()
        self.panel.set_enabled(False)
        self._update_status()

    def refresh_list(self) -> None:
        """Re-scans the folders your currently-loaded files live in
        (picking up new .cbz/.cbr files added there since you loaded),
        then re-reads every file still present from disk. Doesn't
        discover a brand-new subfolder you haven't loaded anything
        from yet (only folders already represented in your current
        list get scanned, non-recursively) -- use Load Folder for that.
        Discards unsaved in-memory edits (with confirmation first) and
        clears the undo stack, since its entries would reference book
        objects this replaces."""
        if not self.books:
            return
        if self._count_dirty() and not self._confirm_discard("refresh the list (discarding unsaved changes)"):
            return

        existing_paths = [os.path.normpath(book.path) for book in self.books]
        seen = set(existing_paths)
        folders = {os.path.dirname(p) for p in existing_paths}
        all_paths = list(existing_paths)
        for folder in sorted(folders):
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                if not name.lower().endswith((".cbz", ".cbr")):
                    continue
                full = os.path.normpath(os.path.join(folder, name))
                if full not in seen:
                    seen.add(full)
                    all_paths.append(full)

        self.books = []
        self._selected_rows = []
        self.undo_manager.clear()
        self._update_undo_action()
        self.table.setRowCount(0)
        self.panel.set_enabled(False)
        self._load_paths(all_paths)

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

        self._push_undo("Parse Filename", target_books)
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

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        if col != self._col_index["filename"]:
            return
        if not (0 <= row < len(self.books)):
            return
        book = self.books[row]
        if not book.load_error:
            self.rename_single_file(book)

    def rename_single_file(self, book: CbzBook) -> None:
        """Quick, direct rename of a single file on disk -- for fixing a
        typo or small mistake in the filename without going through the
        pattern-based Rename/Export tool (open_rename_dialog()). Acts on
        disk immediately, not staged until Save -- same as that tool's
        own "rename in place" mode -- and, like that, isn't pushed onto
        the undo stack, which only ever covers in-memory metadata edits,
        never physical file operations. Triggered by double-clicking a
        Filename cell, or via the table's right-click menu.

        The prompt/validate/rename/error-report flow itself lives in
        redactor_common.gui.rename_single_file (imported above as
        prompt_rename_single_file to avoid shadowing this method's own
        name)."""
        if prompt_rename_single_file(self, book.path, lambda p: setattr(book, "path", p)):
            self._refresh_table_row(self.books.index(book), book)

    def open_search_replace_dialog(self) -> None:
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to search).")
            return

        def get_value(book: CbzBook, field_key: str) -> str:
            if field_key == FILENAME_FIELD_KEY:
                return os.path.splitext(os.path.basename(book.path))[0]
            return getattr(book.metadata, field_key, "")

        dialog = SearchReplaceDialog(
            target_books,
            list(_FIELD_LABELS.items()),
            get_value,
            lambda book: os.path.basename(book.path),
            include_filename=True,
            item_noun="file",
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        field_key = dialog.result_field_key()
        changes = dialog.accepted_changes()  # index into target_books -> new value (one field)
        if not changes:
            return

        self._push_undo("Search & Replace", target_books)
        errors: list[str] = []
        for index, new_value in changes.items():
            book = target_books[index]
            if field_key == FILENAME_FIELD_KEY:
                old_path = book.path
                ext = os.path.splitext(old_path)[1]
                new_path = os.path.join(os.path.dirname(old_path), new_value + ext)
                try:
                    os.rename(old_path, new_path)
                    book.path = new_path
                except OSError as exc:
                    errors.append(f"{os.path.basename(old_path)}: {exc}")
            else:
                setattr(book.metadata, field_key, new_value)
                book.dirty = True
            self._refresh_table_row(self.books.index(book), book)

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed", summarize_errors(errors))
        self._update_status()

    def open_case_conversion_dialog(self) -> None:
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to convert).")
            return

        def get_value(book: CbzBook, field_key: str) -> str:
            return getattr(book.metadata, field_key, "")

        dialog = CaseConversionDialog(
            target_books, list(_FIELD_LABELS.items()), get_value,
            lambda book: os.path.basename(book.path),
            item_noun="file", parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        field_key = dialog.result_field_key()
        changes = dialog.accepted_changes()  # index into target_books -> new value (one field)
        if not changes:
            return

        self._push_undo("Case Conversion", target_books)
        for index, new_value in changes.items():
            book = target_books[index]
            setattr(book.metadata, field_key, new_value)
            book.dirty = True
            self._refresh_table_row(self.books.index(book), book)
        self._update_status()

    def open_auto_numbering_dialog(self) -> None:
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to number).")
            return

        def get_value(book: CbzBook, field_key: str) -> str:
            return getattr(book.metadata, field_key, "")

        fields = [(key, label, key in _AUTO_NUMBER_NUMERIC_FIELDS) for key, label in _FIELD_LABELS.items()]
        dialog = AutoNumberingDialog(
            target_books, fields, get_value,
            lambda book: os.path.basename(book.path),
            item_noun="file", parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        field_key = dialog.result_field_key()
        changes = dialog.accepted_changes()  # index into target_books -> new value (one field)
        if not changes:
            return

        self._push_undo("Auto-Numbering", target_books)
        for index, new_value in changes.items():
            book = target_books[index]
            setattr(book.metadata, field_key, new_value)
            book.dirty = True
            self._refresh_table_row(self.books.index(book), book)
        self._update_status()

    def _quick_number_issues(self, books: list[CbzBook]) -> None:
        """The table right-click's quick version of Auto-Numbering:
        just prompts for a starting issue Number (no field picker, no
        step, no preview) and numbers the given books +1 per row from
        there, in their current table order. Decimal-capable (a
        special issue at "3.5" is a real, common case for a comic
        Number field). For anything beyond the plain "start here, count
        up by one" case on Number specifically -- a different field, a
        different step, or a look at what's changing before it does --
        use Operations -> Auto-Numbering... instead."""
        values = prompt_and_generate_series_numbers(self, len(books), field_label="Starting Number")
        if values is None:
            return
        self._push_undo("Number Issues", books)
        for book, new_value in zip(books, values):
            book.metadata.number = new_value
            book.dirty = True
            self._refresh_table_row(self.books.index(book), book)
        self._update_status()

    def open_resize_images_dialog(self) -> None:
        """Shrinks oversized page images down to a target max width
        (double-page spreads get double that -- see core/image_resize.py).
        Not routed through undo_manager/_push_undo like every other
        Operations entry: those all restore in-memory ComicInfoMetadata,
        but this rewrites actual pixel bytes to disk, which there's
        nothing in memory left to restore from (see CbzBook.
        resize_images()'s own docstring)."""
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to resize).")
            return

        dialog = ResizeImagesDialog(
            len(target_books),
            app_settings.load_resize_max_width(),
            app_settings.load_resize_jpeg_quality(),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        max_width = dialog.max_width()
        jpeg_quality = dialog.jpeg_quality()
        app_settings.save_resize_max_width(max_width)
        app_settings.save_resize_jpeg_quality(jpeg_quality)
        export_mode = dialog.is_export_mode()

        errors: list[str] = []
        exported_paths: list[str] = []
        totals = {"resized": 0, "skipped": 0, "failed": 0, "original_bytes": 0, "new_bytes": 0}

        def _step(book: CbzBook, _index: int) -> None:
            output_path = dialog.output_path_for(book.path)
            try:
                summary = book.resize_images(max_width, jpeg_quality, output_path)
            except CbzError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
                return

            totals["resized"] += summary.pages_resized
            totals["skipped"] += summary.pages_skipped
            totals["failed"] += summary.pages_failed
            totals["original_bytes"] += summary.original_bytes
            totals["new_bytes"] += summary.new_bytes

            if output_path:
                exported_paths.append(output_path)
            else:
                row = self.books.index(book)
                self._refresh_table_row(row, book)
                if self._selected_rows == [row]:
                    page_count_text = f"{book.actual_page_count} page(s)"
                    self.panel.load_metadata(book.metadata, book.read_first_page_bytes(), page_count_text)

        run_with_progress(
            self, target_books, _step, "Resizing images...", threshold=RESIZE_PROGRESS_THRESHOLD
        )

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Resize", summarize_errors(errors))

        processed = totals["resized"] + totals["skipped"] + totals["failed"]
        if processed:
            saved_mb = (totals["original_bytes"] - totals["new_bytes"]) / (1024 * 1024)
            QMessageBox.information(
                self, "Resize Complete",
                f"{totals['resized']} page(s) resized, {totals['skipped']} already small enough, "
                f"{totals['failed']} couldn't be read.\n\n"
                f"Total size: {totals['original_bytes'] / (1024 * 1024):.1f} MB → "
                f"{totals['new_bytes'] / (1024 * 1024):.1f} MB ({saved_mb:+.1f} MB).",
            )

        if export_mode and exported_paths:
            self._load_paths(exported_paths)
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

    def _run_lookup_dialog(self, dialog_class, label: str) -> None:
        """Shared flow for every online lookup dialog (Comic Vine, GCD,
        ...): they all take (target_books, parent) and expose the same
        accepted_metadata() -> {index: {field: value}} shape (see
        gui/comicvine_lookup_dialog.py / gui/gcd_lookup_dialog.py), so
        opening one, applying its results, and refreshing the affected
        rows is identical regardless of which source it is. `label`
        names the source for the undo-stack entry (e.g. "Comic Vine
        lookup")."""
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

        self._push_undo(label, target_books)
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
        any field that already has a non-blank, different value, and if
        so opens a per-file, per-field review before anything is
        written -- applies uniformly no matter which path produced the
        changes (a lookup, bulk edit, or Parse Filename all funnel
        through this same method). This is the standard confirmation
        step for every metadata-writing path that could clobber
        existing data, not an opt-in extra: "we can be sure what is the
        real data" means seeing the actual old/new comparison, not
        trusting one batch-wide Overwrite-All/Keep-Existing choice.

        A totally clean batch (nothing would be overwritten anywhere)
        skips the dialog entirely -- there's nothing to review. The
        moment ANYTHING in the batch conflicts, every field the whole
        batch would touch is shown (not just the conflicting ones), so
        a file with several changed fields is reviewed as a whole, with
        a safe fill (blank -> value) ticked by default and a genuine
        overwrite (differing non-blank -> value) requiring a deliberate
        per-field opt-in -- see gui/overwrite_review_dialog.py.

        Returns the changes to actually apply (every field whose
        checkbox is still ticked when Apply is clicked), or None if the
        user cancelled outright."""
        has_conflict = any(
            (getattr(target_books[index].metadata, attr, "") or "").strip() not in ("", new_value)
            for index, fields in metadata_changes.items()
            for attr, new_value in fields.items()
        )
        if not has_conflict:
            return metadata_changes

        rows = build_overwrite_review_rows(target_books, metadata_changes, _field_label)
        dialog = OverwriteReviewDialog(rows, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None

        filtered: dict[int, dict[str, str]] = {}
        for (index, attr), value in dialog.accepted_changes().items():
            filtered.setdefault(index, {})[attr] = value
        return filtered

    def open_comicvine_lookup_dialog(self) -> None:
        self._run_lookup_dialog(ComicVineLookupDialog, "Comic Vine lookup")

    def open_gcd_lookup_dialog(self) -> None:
        self._run_lookup_dialog(GcdLookupDialog, "Grand Comics Database lookup")

    def open_bedetheque_lookup_dialog(self) -> None:
        """Checked here, before ever constructing BedethequeLookupDialog
        (which needs cloudscraper to get past Bedetheque's Cloudflare
        protection -- see core/bedetheque_lookup.py) -- one clear
        message and a clean return, rather than letting the dialog's
        own constructor raise ImportError up into the generic crash
        handler."""
        try:
            import cloudscraper  # noqa: F401 -- import-only check; actual use is in core/bedetheque_lookup.py
        except ImportError:
            QMessageBox.critical(
                self,
                "Missing Dependency",
                'Looking up via Bedetheque needs the "cloudscraper" package, which isn\'t '
                "installed.\n\nInstall it with:\n\npip install cloudscraper",
            )
            return
        self._run_lookup_dialog(BedethequeLookupDialog, "Bedetheque lookup")

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
