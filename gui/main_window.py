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
import posixpath
import zipfile
import shutil
import threading

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImage
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QVBoxLayout,
    QWidget,
)

from redactor_common.core.table_settings import is_column_visible, merge_column_order, sanitize_hidden_fields
from core.app_paths import asset_path
from redactor_common.gui.async_icon_cache import AsyncIconCache, IdentityWeakDict
from redactor_common.gui.async_preview import AsyncPreviewLoader
from redactor_common.gui.background_call import call_in_background
from redactor_common.gui.visible_rows import VisibleRowsWatcher
from redactor_common.core.folder_refresh import find_new_files_in_loaded_folders
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
from redactor_common.gui.menu_builder import MenuAction, Separator, Submenu, build_menu_bar
from redactor_common.gui.overwrite_review_dialog import resolve_overwrite_conflicts
from redactor_common.gui.parse_filename_dialog import ParseFilenameDialog
from redactor_common.gui.progress import ProgressReporter, run_with_progress
from redactor_common.core.rename_log import RenameLog
from redactor_common.gui.rename_undo import undo_last_rename
from core.app_paths import base_dir
from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog
from redactor_common.gui.rename_single_file import rename_single_file as prompt_rename_single_file
from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY, SearchReplaceDialog
from redactor_common.gui import standard_shortcuts as shortcuts
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

from core.foreign_archive_convert import (
    FOREIGN_ARCHIVE_EXTENSIONS,
    ForeignArchiveConversionError,
    convert_to_cbz,
)
from core.archive_sniff import extension_label
from core.cbz_file import CbzBook, CbzError, ResizeCancelled, path_needs_conversion
from core.foreign_archive_convert import relabel_mislabeled_cbz
from redactor_common.core.trash import TrashError, move_to_trash
from core.page_dimensions import SIZE_LOW, SIZE_OK, SIZE_OVERSIZED, PageSizeStats
from core.scan_quality_tag import LOW_RES_TAG, add_tag, has_tag, remove_tag
from core.scene_name import parse_filename, proposed_fields
from core.credit_pages import KnownCreditPages, credit_matches, scan_book
from core.duplicates import BookFacts, find_duplicates, fingerprint_book
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui import app_settings
from gui.bedetheque_lookup_dialog import BedethequeLookupDialog
from gui.comicvine_lookup_dialog import ComicVineLookupDialog
from gui.conversion_settings_dialog import ConversionSettingsDialog
from gui.gcd_lookup_dialog import GcdLookupDialog
from gui.page_size_scanner import PageSizeScanner
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



def _rename_log() -> RenameLog:
    """The persistent log behind File > Undo Last Rename (redactor_common's
    core/rename_log.py), next to this app's settings."""
    return RenameLog(os.path.join(str(base_dir()), "cbzredactor_rename_log.json"))

def _collection_scan_path() -> str:
    """Collection > Scan Collection Folder...'s zipped CSV, next to the
    settings (core/collection_scan.py)."""
    from core.collection_scan import SCAN_FILE_NAME
    return os.path.join(str(base_dir()), SCAN_FILE_NAME)


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
    [("filename", "Filename"), ("ext", "Ext")]
    + list(_FIELD_LABELS.items())
    + [("pages", "Pages"), ("size", "Size"), ("credit", "Credit Pages"), ("filesize", "File Size"), ("status", "Status")]
)
# Measured from the archive itself, never ComicInfo fields -- sort
# numerically, never by their display text.
_NUMERIC_SYNTHETIC_COLUMNS = frozenset({"pages", "size", "credit", "filesize"})

# Size column cell colors, one per core.page_dimensions band. Solid
# light tints with dark text (HIGHLIGHT_TEXT_COLOR) so they read the
# same in light and dark themes; deliberately stronger than
# DIRTY_COLOR's soft amber so low-res yellow isn't mistaken for
# "unsaved change".
SIZE_CATEGORY_COLORS = {
    SIZE_LOW: QColor("#fde047"),  # yellow
    SIZE_OK: QColor("#86efac"),  # green
    SIZE_OVERSIZED: QColor("#fdba74"),  # orange
}

# Text color for a row that's listed but not editable until converted
# (see CbzBook.needs_conversion) -- mid grey reads as "inactive" on both
# light and dark backgrounds.
UNCONVERTED_TEXT_COLOR = QColor("#8a8a8a")
# Credit Pages column: a book with a known scanner credit page.
CREDIT_PAGE_COLOR = QColor("#fca5a5")
NEEDS_CONVERSION_STATUS = "Needs conversion"

# What to do with files needing conversion when they're loaded -- see
# MainWindow._prompt_convert_foreign_archives().
FOREIGN_CONVERT = "convert"
FOREIGN_UNCONVERTED = "unconverted"
FOREIGN_SKIP = "skip"
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
_DEFAULT_VISIBLE_COLUMNS = frozenset({"filename", "ext", "title", "series", "number", "pages", "size", "credit", "status"})
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

# Table cover thumbnails (page 1, in the Filename cell) -- same size as
# epubredactor's table covers.
COVER_ICON_SIZE = QSize(24, 32)


def resource_path(*parts: str) -> str:
    """Resolves a bundled read-only resource (icon, README, ...) whether
    running from source or frozen -- see core.app_paths.asset_path()."""
    return asset_path(*parts)

def comic_files_in_folder(folder: str) -> list[str]:
    """Every archive this app opens (.cbz, plus CBR/CBT/CB7 offered for
    conversion) directly inside `folder`, sorted, non-recursive. Shared
    by Load Folder and Refresh List so the two can't disagree."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [
        os.path.join(folder, name)
        for name in names
        if name.lower().endswith((".cbz",) + FOREIGN_ARCHIVE_EXTENSIONS)
    ]


def _file_size_bytes(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _format_file_size(path: str) -> str:
    size = _file_size_bytes(path)
    if size is None:
        return ""
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024:.0f} KB"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)

        self.books: list[CbzBook] = []
        self._selected_rows: list[int] = []
        self.undo_manager: UndoManager[CbzBook] = UndoManager()
        # Selected file's cover: read + decoded off the GUI thread, see
        # _show_book_in_panel(). Decoded no larger than this (2x a wide
        # side panel, for high-DPI screens).
        self._cover_preview = AsyncPreviewLoader(QSize(900, 1350), parent=self)
        self._cover_preview.image_ready.connect(self._on_cover_preview_ready)
        # Table cover thumbnails, loaded lazily: only rows actually on
        # screen (plus a small buffer) ever read or decode a cover, off
        # the GUI thread -- redactor_common's VisibleRowsWatcher +
        # AsyncIconCache, the mechanism that took epub's 15k-book table
        # rebuild from ~126s to ~2.6s. A cover's version is (path,
        # mtime), stat'ed only for visible rows; _cover_source remembers
        # the last one requested so a rebuild can reuse cached icons
        # without touching the disk.
        self._cover_icons = AsyncIconCache(COVER_ICON_SIZE, parent=self)
        self._cover_icons.icon_ready.connect(self._on_cover_icon_ready)
        self._cover_source = IdentityWeakDict()
        # Size column: page dimensions measured in the background for
        # visible rows, same lazy scheme as the covers above (and the
        # same (path, mtime) version key, kept in _size_source).
        self._page_sizes = PageSizeScanner(parent=self)
        self._page_sizes.stats_ready.connect(self._on_page_sizes_ready)
        self._size_source = IdentityWeakDict()
        # Credit Pages column: the first/last pages fingerprinted in the
        # background by the same lazy machinery (core/credit_pages.py),
        # compared against the learned pages at display time -- so
        # learning or forgetting a page never needs a rescan.
        self._known_credits = KnownCreditPages(app_settings.credit_pages_path())
        self._credit_scanner = PageSizeScanner(parent=self, scan=scan_book)
        self._credit_scanner.stats_ready.connect(self._on_credit_scan_ready)
        # Find Duplicates: cover + story-page fingerprints per book
        # (core/duplicates.py), cached the same way, so a second run over
        # the same files is instant.
        self._dupe_fingerprints = PageSizeScanner(parent=self, scan=fingerprint_book)
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
        self.table.setIconSize(COVER_ICON_SIZE)
        self._visible_rows = VisibleRowsWatcher(self.table, self._load_lazy_cells_for_rows)
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
                MenuAction("load_files", "&Load Files...", self.load_files_dialog, shortcut=shortcuts.LOAD_FILES),
                MenuAction(
                    "load_folder", "Load &Folder...", self.load_folder_dialog, shortcut=shortcuts.LOAD_FOLDER
                ),
                Separator(),
                MenuAction("save", "&Save", self.save_current, shortcut=shortcuts.SAVE),
                MenuAction("save_as", "Save &As...", self.save_current_as, shortcut=shortcuts.SAVE_AS),
                Separator(),
                # Quick, direct rename of the one selected file -- matches
                # Explorer's F2 exactly. Distinct from "rename_files"
                # below (the pattern-based batch tool, moved off F2 to
                # make room for this): see rename_selected_file().
                MenuAction(
                    "rename_file", "&Rename File...", self.rename_selected_file,
                    shortcut=shortcuts.RENAME_SINGLE_FILE,
                ),
                MenuAction("undo_rename", "&Undo Last Rename...", self.undo_last_rename),
                MenuAction(
                    "rename_files", "Rename / &Export Files...", self.open_rename_dialog,
                    shortcut=shortcuts.RENAME_EXPORT_BY_PATTERN,
                ),
                Separator(),
                MenuAction("remove_files", "Remo&ve Files", self.remove_selected, shortcut=shortcuts.REMOVE_FROM_LIST),
                Separator(),
                MenuAction("refresh_list", "Re&fresh List", self.refresh_list, shortcuts=shortcuts.REFRESH_LIST),
                MenuAction("clear_list", "&Clear List", self.clear_list),
                Separator(),
                # No explicit shortcut -- Alt+F4 already closes this (or
                # any) plain QMainWindow at the OS level, verified
                # directly (launch, send Alt+F4, confirm the process
                # exits), independent of anything bound here.
                MenuAction("exit", "E&xit", self.close),
            ],
            "Import": [
                MenuAction(
                    "parse_filename", "&Parse Filename...", self.open_parse_filename_dialog,
                    shortcut=shortcuts.PARSE_FILENAME_TO_METADATA,
                ),
                MenuAction("read_filename_tags", "Read Filename &Tags", self.read_filename_tags),
                MenuAction("convert_foreign", "Convert to CB&Z...", self.convert_foreign_archives_dialog),
                Separator(),
                MenuAction("comicvine_lookup", "Look Up via Comic &Vine...", self.open_comicvine_lookup_dialog),
                MenuAction("gcd_lookup", "Look Up via &Grand Comics Database...", self.open_gcd_lookup_dialog),
                MenuAction(
                    "gcd_local_lookup", "Look Up via GCD (&Local Database)...", self.open_gcd_local_lookup_dialog
                ),
                MenuAction(
                    "comicrack_lookup", "Look Up via Comic&Rack Library...", self.open_comicrack_lookup_dialog
                ),
                MenuAction("compare_with_gcd", "Compare ComicRack Library &with GCD...", self.compare_library_with_gcd),
                MenuAction("bedetheque_lookup", "Look Up via &Bedetheque...", self.open_bedetheque_lookup_dialog),
            ],
            "Operations": [
                # Shared with the toolbar (see _build_toolbar) -- one
                # QAction instance, so its dynamic "Apply to N selected
                # file(s)" text and enabled state never drift out of
                # sync between the two places it appears.
                MenuAction("apply_bulk_edit", "&Apply to 0 Selected File(s)", self._apply_bulk_edit),
                MenuAction(
                    "search_replace", "&Search/Replace...", self.open_search_replace_dialog,
                    shortcut=shortcuts.SEARCH_REPLACE,
                ),
                MenuAction("case_conversion", "&Case Conversion...", self.open_case_conversion_dialog),
                MenuAction("auto_numbering", "Auto-&Numbering...", self.open_auto_numbering_dialog),
                MenuAction("validate", "&Validate / Fix Issues...", self.open_validate_fix_dialog),
                Separator(),
                MenuAction("resize_images", "Resi&ze Images...", self.open_resize_images_dialog),
                MenuAction("tag_low_res", "&Tag Low-Res Scans", self.tag_low_res_scans),
                MenuAction("remove_credit_pages", "Remove Credit &Pages...", self.open_remove_credit_pages_dialog),
                MenuAction("clean_contents", "Clean Up Archive C&ontents...", self.open_clean_contents_dialog),
                MenuAction("find_duplicates", "Find &Duplicates...", self.open_find_duplicates_dialog),
                Separator(),
                MenuAction("save_all", "Save &All Changed", self.save_all_changed, shortcut=shortcuts.SAVE_ALL),
                Separator(),
                MenuAction("undo", "&Undo", self.undo_last_action, shortcut=shortcuts.UNDO),
                MenuAction("redo", "&Redo", self.redo_last_action, shortcut=shortcuts.REDO),
            ],
            "Settings": [
                MenuAction("comicvine_api_key", "Comic Vine API &Key...", self.change_comicvine_api_key),
                MenuAction("known_credit_pages", "Known C&redit Pages...", self.open_known_credit_pages_dialog),
                MenuAction("gcd_account", "&GCD Account...", self.open_gcd_account_dialog),
                MenuAction("gcd_local_settings", "GCD &Local Database...", self.open_gcd_local_settings_dialog),
                MenuAction(
                    "comicrack_settings", "Comic&Rack Library Database...", self.open_comicrack_settings_dialog
                ),
                MenuAction("conversion_settings", "Converting to CB&Z...", self.open_conversion_settings_dialog),
                Separator(),
                MenuAction("column_settings", "Add/Remove &Columns...", self.open_column_settings_dialog),
                MenuAction("genre_settings", "Add/Remove &Genres...", self.open_genre_settings_dialog),
                MenuAction("language_settings", "Add/Remove &Languages...", self.open_language_settings_dialog),
            ],
            "Help": [
                MenuAction("about", f"&About {APP_NAME}", self.open_about_dialog, shortcut=shortcuts.HELP),
                MenuAction("changelog", "View &Changelog", self.open_changelog_dialog),
                MenuAction("credits", "View C&redits", self.open_credits_dialog),
            ],
        }
        # Not a key in `specs`: build_menu_bar() only builds the five
        # standard menus from it, and before redactor_common 2026-09-29#05
        # silently dropped any other key -- which hid this whole menu.
        collection = [
            MenuAction("scan_collection", "&Scan Collection Folder...", self.scan_collection_folder),
            MenuAction("collection_report", "Collection &Report...", self.open_collection_report),
        ]
        self.actions_ = build_menu_bar(self, specs, extra_menus=[("Collection", 3, collection)])
        self.actions_["apply_bulk_edit"].setEnabled(False)
        self.actions_["undo"].setEnabled(False)
        self.actions_["redo"].setEnabled(False)

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
        toolbar.addAction(self.actions_["redo"])
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
            selected_unconverted = [
                self.books[r] for r in self._selected_rows if self.books[r].needs_conversion
            ]
            if selected_unconverted:
                items.append(MenuAction(
                    "convert_selected", f"Convert {len(selected_unconverted)} File(s) to CBZ",
                    lambda: self.convert_books_to_cbz(selected_unconverted),
                ))
            # Reuses the actual File-menu QAction (F2) rather than
            # building a fresh one -- same object, so this shows the
            # real shortcut hint and can never drift out of sync with
            # it. Its own enabled guard (exactly one book selected, no
            # load error) is rename_selected_file()'s job, not this
            # menu's -- see there for why this is distinct from
            # "Rename / Export Files..." (the pattern-based batch tool).
            if len(self._selected_rows) == 1 and not self.books[self._selected_rows[0]].load_error:
                items.append(self.actions_["rename_file"])
            if len(self._selected_rows) == 1 and self._editable_selected_rows():
                one = self.books[self._selected_rows[0]]
                items.append(MenuAction("credit_pages", "Credit Pages...", lambda: self.open_credit_pages_dialog(one)))
            if self._editable_selected_rows():
                selected_books = [self.books[r] for r in self._editable_selected_rows()]
                items.append(MenuAction(
                    "number_issues", "Number Issues...", lambda: self._quick_number_issues(selected_books)
                ))
            # Every per-file lookup from the Tools menu, so none of them
            # needs a trip to the menu bar. Reuses the real QActions
            # (same as rename_file above) so text and enabled state
            # can't drift. "Compare ComicRack Library with GCD" is a
            # whole-library report, not a lookup on the selection, so
            # it stays in the menu bar only.
            items.append(Submenu("Look Up", [
                self.actions_[key] for key in (
                    "comicvine_lookup", "gcd_lookup", "gcd_local_lookup",
                    "comicrack_lookup", "bedetheque_lookup",
                )
            ]))
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
            self, "Load CBZ Files", start_dir,
            "Comic Book Archives (*.cbz *.cbr *.cbt *.cb7);;All Files (*)",
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
        paths = comic_files_in_folder(folder)
        if not paths:
            QMessageBox.information(
                self, "No Files Found", "No .cbz, .cbr, .cbt, or .cb7 files were found in that folder."
            )
            return
        self._load_paths(paths)

    def _prompt_convert_foreign_archives(self, paths: list[str]) -> tuple[str, bool]:
        """If `paths` contains any file needing conversion (a CBR/CBT/
        CB7, or a mislabeled archive -- see CbzBook.needs_conversion),
        asks once for the whole batch what to do with them: convert now,
        add them to the list unconverted (read-only rows, converted
        later from the table), or skip them. Returns (choice, delete),
        choice one of FOREIGN_CONVERT / FOREIGN_UNCONVERTED /
        FOREIGN_SKIP, delete = move originals to the Recycle Bin after a
        verified conversion. (FOREIGN_CONVERT, False) when nothing in
        the batch needs converting, since there's nothing to ask."""
        foreign = [p for p in paths if path_needs_conversion(p)]
        if not foreign:
            return FOREIGN_CONVERT, False

        shown = "\n".join(f"  {os.path.basename(p)}" for p in foreign[:10])
        if len(foreign) > 10:
            shown += f"\n  ...and {len(foreign) - 10} more"

        box = QMessageBox(self)
        box.setWindowTitle("Convert to CBZ?")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"{len(foreign)} file(s) aren't real CBZ files -- this app only "
            f"edits CBZ (ZIP) archives:\n\n{shown}\n\n"
            "Convert them now, or add them to the list unconverted and "
            "convert later from the table (right-click > Convert to CBZ)?"
        )
        # Two options, so not QMessageBox.setCheckBox() (which takes one):
        # the checkboxes go into the box's own grid, under the text.
        options = QWidget()
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        delete_checkbox = QCheckBox("Move the originals to the Recycle Bin after a successful conversion")
        delete_checkbox.setChecked(app_settings.load_recycle_originals())
        remember_checkbox = QCheckBox("Remember my choice (change it in Settings > Converting to CBZ...)")
        remember_checkbox.setToolTip("Remembers Convert Now or Add Unconverted; Skip is never remembered.")
        options_layout.addWidget(delete_checkbox)
        options_layout.addWidget(remember_checkbox)
        box.layout().addWidget(options, box.layout().rowCount(), 0, 1, box.layout().columnCount())

        convert_btn = box.addButton("Convert Now", QMessageBox.ButtonRole.AcceptRole)
        unconverted_btn = box.addButton("Add Unconverted", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(unconverted_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is convert_btn:
            choice = FOREIGN_CONVERT
        elif clicked is unconverted_btn:
            choice = FOREIGN_UNCONVERTED
        else:
            return FOREIGN_SKIP, False

        if remember_checkbox.isChecked():
            app_settings.save_foreign_load_behavior(
                app_settings.FOREIGN_LOAD_CONVERT if choice == FOREIGN_CONVERT else app_settings.FOREIGN_LOAD_UNCONVERTED
            )
            app_settings.save_recycle_originals(delete_checkbox.isChecked())
        return choice, choice == FOREIGN_CONVERT and delete_checkbox.isChecked()

    def _resolve_foreign_choice(self, paths: list[str]) -> tuple[str, bool]:
        """What _load_paths() does with files needing conversion, per
        Settings > Converting to CBZ...: list them unconverted (the
        default), convert them without asking, or ask."""
        behavior = app_settings.load_foreign_load_behavior()
        if behavior == app_settings.FOREIGN_LOAD_ASK:
            return self._prompt_convert_foreign_archives(paths)
        if behavior == app_settings.FOREIGN_LOAD_CONVERT:
            return FOREIGN_CONVERT, app_settings.load_recycle_originals()
        return FOREIGN_UNCONVERTED, False

    def _convert_path(self, path: str, delete_original: bool, errors: list[str]) -> str | None:
        """Converts one file to a real .cbz (see core/foreign_archive_convert.py
        -- the method follows the file's real content, and the result is
        verified before this returns). A ".cbz" that's really a RAR/7z/tar
        is first renamed to its true extension, so the converted file can
        take the .cbz name. With `delete_original`, the source goes to the
        Recycle Bin afterwards -- never permanently deleted, and never if
        anything failed. Returns the new path, or None (with the reason
        appended to `errors`)."""
        name = os.path.basename(path)
        source = path
        try:
            if os.path.splitext(path)[1].lower() == ".cbz":
                source = relabel_mislabeled_cbz(path)
            new_path = convert_to_cbz(source)
        except ForeignArchiveConversionError as exc:
            errors.append(f"{name}: {exc}")
            if source != path:
                # Undo the relabel, or the file stays under a name the
                # table row (still holding `path`) doesn't know.
                try:
                    os.rename(source, path)
                except OSError as rename_exc:
                    errors.append(f"{name}: could not restore its name from {os.path.basename(source)}: {rename_exc}")
            return None
        if delete_original:
            try:
                move_to_trash(source)
            except TrashError as exc:
                errors.append(f"{name}: converted to CBZ successfully, but {exc}")
        return new_path

    def _load_paths(self, paths: list[str]) -> None:
        errors: list[str] = []
        choice, should_delete = self._resolve_foreign_choice(paths)

        def _step(path: str, _index: int) -> None:
            resolved_path = path
            if path_needs_conversion(path):
                if choice == FOREIGN_SKIP:
                    return  # declined for this whole batch -- skip, don't load
                if choice == FOREIGN_CONVERT:
                    resolved_path = self._convert_path(path, should_delete, errors)
                    if resolved_path is None:
                        return
                # FOREIGN_UNCONVERTED: listed as-is, read-only
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
        # covers Refresh List and Convert to CBZ/Resize Images' "load
        # the result back in" calls, all of which route through here).
        if self._sort_key:
            if self._sort_key == "size":
                self._ensure_page_sizes(self.books)
            self.books.sort(
                key=lambda book: self._sort_key_for(book, self._sort_key), reverse=not self._sort_ascending
            )
            self._rebuild_table()

        self._update_status()

    def _add_table_row(self, book: CbzBook) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._refresh_table_row(row, book)

    @staticmethod
    def _status_text(book: CbzBook) -> str:
        if book.load_error:
            return book.load_error
        if book.needs_conversion:
            return NEEDS_CONVERSION_STATUS
        if book.page_count_mismatch:
            return "Page count mismatch"
        return "Modified" if book.dirty else "OK"

    def _refresh_edited_row(self, book: CbzBook) -> None:
        """After an edit that changed `book`'s metadata behind the panel's
        back (Search/Replace, Case Conversion, numbering, Validate & Fix):
        refresh its row and, if it's the one file selected, reload the
        panel -- otherwise the panel keeps the old values and the next
        row change writes them back over the edit (_commit_current_edits
        writes every field). Found 2026-09-29: a single-file
        Search/Replace was silently undone that way."""
        row = self.books.index(book)
        self._refresh_table_row(row, book)
        if self._selected_rows == [row]:
            self._show_book_in_panel(book, f"{book.actual_page_count} page(s)")

    def _refresh_table_row(self, row: int, book: CbzBook) -> None:
        status = self._status_text(book)

        name_item = QTableWidgetItem(os.path.basename(book.path))
        # Only an already-decoded icon here, never a decode request --
        # see _load_cover_icons_for_rows().
        cached = self._cover_icons.get_cached_icon(book, self._cover_source.get(book))
        name_item.setIcon(cached if cached is not None else QIcon())
        self.table.setItem(row, self._col_index["filename"], name_item)
        ext_item = QTableWidgetItem(extension_label(book.path, book.container))
        if book.needs_conversion:
            ext_item.setToolTip(
                "Listed read-only: this app only edits real CBZ (ZIP) files. "
                "Use Convert to CBZ (right-click, or the Import menu)."
            )
        self.table.setItem(row, self._col_index["ext"], ext_item)
        pages_text = "" if book.needs_conversion and not book.page_names else str(book.actual_page_count)
        self.table.setItem(row, self._col_index["pages"], QTableWidgetItem(pages_text))
        self.table.setItem(row, self._col_index["filesize"], QTableWidgetItem(_format_file_size(book.path)))
        self.table.setItem(row, self._col_index["status"], QTableWidgetItem(status))
        # Only already-measured sizes here, never a scan -- see
        # _load_lazy_cells_for_rows().
        self.table.setItem(row, self._col_index["size"], QTableWidgetItem())
        self.table.setItem(row, self._col_index["credit"], QTableWidgetItem())
        stats = self._page_sizes.get_cached(book, self._size_source.get(book))
        credit_scan = self._credit_scanner.get_cached(book, self._size_source.get(book))
        # Every other column is a plain ComicInfo field -- one shared
        # loop covers all of them (title/series/number plus every field
        # only reachable as a column once you turn it on; see
        # DEFAULT_HIDDEN_COLUMNS) instead of hand-listing each one.
        for attr in _FIELD_LABELS:
            value = getattr(book.metadata, attr, "")
            self.table.setItem(row, self._col_index[attr], QTableWidgetItem(value))

        self._apply_row_status_color(row, book)
        if stats is not None:
            self._set_size_cell(row, book, stats)
        if credit_scan is not None:
            self._set_credit_cell(row, book, credit_scan)

    def _credit_matches_for(self, book: CbzBook) -> list | None:
        scan = self._credit_scanner.get_cached(book, self._size_source.get(book))
        return None if scan is None else credit_matches(scan, self._known_credits)

    def _set_credit_cell(self, row: int, book: CbzBook, scan) -> None:
        """"last page" / "page 1" / "2 pages" in red when the book's
        first or last pages match a learned credit page; blank otherwise."""
        item = self.table.item(row, self._col_index["credit"])
        if item is None:
            return
        matches = credit_matches(scan, self._known_credits)
        if not matches:
            item.setText("")
            item.setToolTip("")
            item.setData(Qt.ItemDataRole.BackgroundRole, None)
            if not book.needs_conversion:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
            return
        last = len(book.page_names) - 1
        if len(matches) == 1:
            item.setText("last page" if matches[0].index == last else f"page {matches[0].index + 1}")
        else:
            item.setText(f"{len(matches)} pages")
        item.setToolTip("Known scanner credit page(s):\n" + "\n".join(
            f"page {m.index + 1}: {m.name}" for m in matches
        ) + "\n\nOperations > Remove Credit Pages... removes them.")
        item.setBackground(CREDIT_PAGE_COLOR)
        item.setForeground(HIGHLIGHT_TEXT_COLOR)

    def _on_credit_scan_ready(self, book: CbzBook, scan) -> None:
        try:
            row = self.books.index(book)
        except ValueError:
            return
        if self._credit_scanner.get_cached(book, self._size_source.get(book)) is scan:
            self._set_credit_cell(row, book, scan)

    def _refresh_all_credit_cells(self) -> None:
        """After learning/forgetting a page: re-check every row from the
        cached scans -- no rescan needed (see __init__)."""
        for row, book in enumerate(self.books):
            scan = self._credit_scanner.get_cached(book, self._size_source.get(book))
            if scan is not None:
                self._set_credit_cell(row, book, scan)

    def _set_size_cell(self, row: int, book: CbzBook, stats: PageSizeStats) -> None:
        """Fills the Size cell: typical single-page width, colored by
        band (see core/page_dimensions.py), with the full breakdown as a
        tooltip. Applied after the row tint so the band color stays
        visible on a modified row -- but not on a failed one, where the
        red "this file is broken" tint matters more."""
        item = self.table.item(row, self._col_index["size"])
        if item is None:
            return
        width = stats.representative_width
        item.setText(f"{width}px" if width is not None else "?")
        item.setToolTip(stats.describe())
        category = stats.category
        if category is not None and not book.load_error:
            item.setBackground(SIZE_CATEGORY_COLORS[category])
            item.setForeground(HIGHLIGHT_TEXT_COLOR)

    def _apply_row_status_color(self, row: int, book: CbzBook) -> None:
        """Tints every cell in the row so a problem file (failed to
        load) or an unsaved change (dirty, or a page-count mismatch
        that'll be corrected on save) is visible at a glance across the
        whole row -- matching epub/mp3/video's own row-tinting via
        redactor_common.gui.colors, which this app never had at all
        until now (its Status column was plain text only)."""
        if book.load_error:
            color = ERROR_COLOR
        elif book.needs_conversion:
            color = None  # untinted, but greyed text -- see below
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
            elif book.needs_conversion and not book.load_error:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
                item.setForeground(UNCONVERTED_TEXT_COLOR)
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
        editable_rows = self._editable_selected_rows()

        if not editable_rows:
            # Nothing selected, or only rows waiting for Convert to CBZ
            # (read-only -- see CbzBook.needs_conversion).
            self.panel.set_enabled(False)
            self._update_apply_bulk_edit_action()
            if self._selected_rows:
                self.statusBar().showMessage(
                    "Selected file(s) need converting before they can be edited -- "
                    "right-click > Convert to CBZ."
                )
            return

        self.panel.set_enabled(True)
        if len(self._selected_rows) == 1:
            book = self.books[self._selected_rows[0]]
            self.panel.set_bulk_mode(0)
            page_count_text = f"{book.actual_page_count} page(s)"
            if book.page_count_mismatch:
                page_count_text += f" -- ComicInfo.xml says {book.metadata.page_count}, will be corrected on save"
            self._show_book_in_panel(book, page_count_text)
        else:
            self.panel.set_bulk_mode(len(editable_rows))
        self._update_apply_bulk_edit_action()

    def _editable_selected_rows(self) -> list[int]:
        """Selected rows minus any still waiting for Convert to CBZ --
        what every metadata edit/save acts on. (Remove Files, Rename
        File and Convert to CBZ itself use the full selection.)"""
        return [
            row for row in self._selected_rows
            if row < len(self.books) and not self.books[row].needs_conversion
        ]

    def _update_apply_bulk_edit_action(self) -> None:
        count = len(self._editable_selected_rows()) if self.panel.bulk_mode else 0
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
        if row >= len(self.books) or self.books[row].needs_conversion:
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

        target_books = [self.books[row] for row in self._editable_selected_rows()]
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

        self.panel.set_bulk_mode(len(target_books))  # clears the fields, ready for another round
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
        self._update_redo_action()  # push() clears any pending redo

    def _update_undo_action(self) -> None:
        can_undo = self.undo_manager.can_undo()
        self.actions_["undo"].setEnabled(can_undo)
        label = self.undo_manager.peek_label()
        self.actions_["undo"].setText(f"&Undo {label}" if label else "&Undo")

    def _update_redo_action(self) -> None:
        can_redo = self.undo_manager.can_redo()
        self.actions_["redo"].setEnabled(can_redo)
        label = self.undo_manager.peek_redo_label()
        self.actions_["redo"].setText(f"&Redo {label}" if label else "&Redo")

    def _apply_undo_affected(self, affected: list[CbzBook]) -> None:
        """Shared tail of undo_last_action()/redo_last_action() -- both
        restore a list of books the same way, they just pull from
        opposite stacks."""
        for book in affected:
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                page_count_text = f"{book.actual_page_count} page(s)"
                self._show_book_in_panel(book, page_count_text)
        self._update_undo_action()
        self._update_redo_action()
        self._update_status()

    def undo_last_action(self) -> None:
        # snapshot_fn passed too (not just restore_fn) so the state
        # being overwritten is captured onto the redo stack first --
        # see redactor_common.core.undo's own docstring.
        affected = self.undo_manager.undo(self._restore_book, self._snapshot_book)
        self._apply_undo_affected(affected)

    def redo_last_action(self) -> None:
        affected = self.undo_manager.redo(self._restore_book, self._snapshot_book)
        self._apply_undo_affected(affected)

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

        if key == "size":
            self._ensure_page_sizes(self.books)
        elif key == "credit":
            self._ensure_credit_scans(self.books)
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
        elif key == "size":
            stats = self._page_sizes.get_cached(book, self._size_source.get(book))
            raw = stats.representative_width if stats is not None else None
        elif key == "credit":
            matches = self._credit_matches_for(book)
            raw = len(matches) if matches is not None else None
        elif key == "filesize":
            raw = _file_size_bytes(book.path)
        elif key == "status":
            raw = self._status_text(book)
        elif key == "ext":
            raw = extension_label(book.path, book.container)
        else:
            raw = getattr(book.metadata, key, "")

        if key in NUMERIC_FILENAME_FIELDS or key in _NUMERIC_SYNTHETIC_COLUMNS:
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
        self._update_redo_action()
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
        self._update_redo_action()
        self._rebuild_table()
        self.panel.set_enabled(False)
        self._update_status()

    def _load_lazy_cells_for_rows(self, rows: list[int]) -> None:
        """VisibleRowsWatcher callback: request covers and page sizes
        for these (on screen) rows only. Rows map to self.books by
        position -- this app's own sort reorders self.books itself (see
        _sort_key_for)."""
        for row in rows:
            if row >= len(self.books):
                continue
            book = self.books[row]
            if book.load_error or not book.first_page_name:
                continue
            try:
                source = (book.path, os.path.getmtime(book.path))
            except OSError:
                continue
            self._request_page_sizes(row, book, source)
            self._cover_source[book] = source
            cached = self._cover_icons.get_cached_icon(book, source)
            if cached is not None:
                item = self.table.item(row, self._col_index["filename"])
                if item is not None:
                    item.setIcon(cached)
                continue
            self._cover_icons.request(book, source, loader=book.read_first_page_bytes)

    def _request_page_sizes(self, row: int, book: CbzBook, source) -> None:
        self._size_source[book] = source
        credit_scan = self._credit_scanner.get_cached(book, source)
        if credit_scan is None:
            self._credit_scanner.request(book, source)
        else:
            self._set_credit_cell(row, book, credit_scan)
        cached = self._page_sizes.get_cached(book, source)
        if cached is not None:
            item = self.table.item(row, self._col_index["size"])
            if item is not None and not item.text():
                self._set_size_cell(row, book, cached)
            return
        self._page_sizes.request(book, source)

    def _on_page_sizes_ready(self, book: CbzBook, stats: PageSizeStats) -> None:
        try:
            row = self.books.index(book)
        except ValueError:
            return  # removed from the list meanwhile
        if self._page_sizes.get_cached(book, self._size_source.get(book)) is stats:
            self._set_size_cell(row, book, stats)

    def _ensure_page_sizes(self, books: list[CbzBook]) -> dict[int, PageSizeStats]:
        """Measures every book in `books` that isn't measured yet (under
        a progress dialog -- a sort or the Resize dialog needs all of
        them, not only the visible rows) and returns id(book) -> stats.
        Books that can't be measured at all are left out."""
        result: dict[int, PageSizeStats] = {}

        def _step(book: CbzBook, _index: int) -> None:
            if book.load_error or not book.first_page_name:
                return
            try:
                source = (book.path, os.path.getmtime(book.path))
            except OSError:
                return
            self._size_source[book] = source
            result[id(book)] = self._page_sizes.scan_now(book, source)

        pending = [
            book for book in books
            if self._page_sizes.get_cached(book, self._size_source.get(book)) is None
        ]
        run_with_progress(
            self, pending, _step, "Measuring page sizes...", threshold=LOAD_PROGRESS_THRESHOLD,
            cancellable=False,
            label_for=lambda book: f"Measuring: {os.path.basename(book.path)}",
        )
        for book in books:
            if id(book) not in result:
                cached = self._page_sizes.get_cached(book, self._size_source.get(book))
                if cached is not None:
                    result[id(book)] = cached
        return result

    def _on_cover_icon_ready(self, book: CbzBook, icon: QIcon) -> None:
        """A background cover decode finished: set that one cell's icon,
        wherever the book is now (the list may have been re-sorted or
        rebuilt while it decoded)."""
        try:
            row = self.books.index(book)
        except ValueError:
            return  # removed from the list meanwhile
        item = self.table.item(row, self._col_index["filename"])
        if item is not None:
            item.setIcon(icon)

    def _show_book_in_panel(self, book: CbzBook, page_count_text: str) -> None:
        """Loads `book` into the side panel. The fields appear at once;
        the cover (page 1) is read out of the archive and decoded on a
        worker thread by redactor_common's AsyncPreviewLoader, downscaled
        while decoding. This used to be a full-resolution
        QPixmap.loadFromData() on the GUI thread on every selection
        change -- comic pages are often 3000x4500 px, so arrowing
        through a library stuttered on every row."""
        self.panel.load_metadata(book.metadata, None, page_count_text)
        if book.first_page_name:
            self.panel.set_cover_loading()
            self._cover_preview.request(book, book.read_first_page_bytes)
        else:
            self._cover_preview.cancel()

    def _on_cover_preview_ready(self, book: CbzBook, image: QImage) -> None:
        if len(self._selected_rows) == 1 and self.books[self._selected_rows[0]] is book:
            self.panel.set_cover_image(image)

    def refresh_list(self) -> None:
        """Re-scans the folders your currently-loaded files live in
        (picking up new .cbz/.cbr/.cbt/.cb7 files added there since you loaded),
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

        # The shared folder_refresh logic, fed the same folder scan Load
        # Folder uses -- this used to be its own copy that only looked
        # for .cbz/.cbr, so a CBT/CB7 dropped into a loaded folder never
        # showed up on refresh.
        existing_paths = [os.path.normpath(book.path) for book in self.books]
        all_paths = existing_paths + find_new_files_in_loaded_folders(
            existing_paths, comic_files_in_folder,
        )

        self.books = []
        self._selected_rows = []
        self.undo_manager.clear()
        self._update_undo_action()
        self._update_redo_action()
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
        rows = self._editable_selected_rows()
        if not rows:
            return
        if len(rows) == 1:
            self._save_book(rows[0])
            return

        errors: list[str] = []

        def _step(row: int, _index: int) -> None:
            book = self.books[row]
            try:
                book.save()
            except CbzError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
            self._refresh_table_row(row, book)

        run_with_progress(
            self, rows, _step, "Saving files...", threshold=SAVE_PROGRESS_THRESHOLD, cancellable=True,
            label_for=lambda row: f"Saving: {os.path.basename(self.books[row].path)}",
        )
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

        run_with_progress(
            self, changed_rows, _step, "Saving files...", threshold=SAVE_PROGRESS_THRESHOLD,
            label_for=lambda row: f"Saving: {os.path.basename(self.books[row].path)}",
        )

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
            always_pad_fields={"month": 2},
            ascii_only=app_settings.load_ascii_filenames(),
            on_ascii_only_changed=app_settings.save_ascii_filenames,
            zero_pad_initial=app_settings.load_rename_zero_pad(),
            on_zero_pad_changed=app_settings.save_rename_zero_pad,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        app_settings.save_pattern_used(dialog.pattern_edit.text())
        export_mode = dialog.is_export_mode()
        errors: list[str] = []
        renamed: list[tuple[str, str]] = []

        def _step(planned, _index: int) -> None:
            book, old_path, new_path = planned
            try:
                if export_mode:
                    shutil.copy2(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    book.path = new_path
                    renamed.append((old_path, new_path))
            except OSError as exc:
                errors.append(f"{os.path.basename(old_path)}: {exc}")

        run_with_progress(
            self, dialog.planned_renames(), _step,
            "Exporting files..." if export_mode else "Renaming files...",
            threshold=SAVE_PROGRESS_THRESHOLD, cancellable=True,
            label_for=lambda planned: f"{'Exporting' if export_mode else 'Renaming'}: {os.path.basename(planned[1])}",
        )
        _rename_log().record("Rename by Pattern", renamed)

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
                self._show_book_in_panel(book, page_count_text)
        self._update_status()

    def read_filename_tags(self) -> None:
        """Import > Read Filename Tags: fills ComicInfo from what a
        scene-style filename says (core/scene_name.py) -- series,
        number, count, volume, title, year, plus the bracketed tags:
        scan group and source into ScanInformation, edition into
        Format, completeness notes appended to Notes. No pattern to
        type, unlike Parse Filename. Goes through the usual per-field
        overwrite review; undoable, written on Save."""
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to read).")
            return

        changes: dict[int, dict[str, str]] = {}
        unknown: dict[str, int] = {}
        for index, book in enumerate(target_books):
            parsed = parse_filename(book.path)
            fields = proposed_fields(parsed, book.metadata.notes)
            fields = {k: v for k, v in fields.items() if getattr(book.metadata, k, "") != v}
            if fields:
                changes[index] = fields
            for phrase in parsed.unknown:
                unknown[phrase] = unknown.get(phrase, 0) + 1

        if not changes:
            QMessageBox.information(self, "Read Filename Tags", "Nothing new to take from these filenames.")
            return
        changes = self._resolve_overwrite_conflicts(target_books, changes)
        if not changes:
            return

        self._push_undo("Read Filename Tags", target_books)
        for index, fields in changes.items():
            book = target_books[index]
            for attr, value in fields.items():
                setattr(book.metadata, attr, value)
            book.dirty = True
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                self._show_book_in_panel(book, f"{book.actual_page_count} page(s)")
        self._update_status()

        if unknown:
            shown = sorted(unknown, key=lambda p: (-unknown[p], p.casefold()))
            listing = "\n".join(f"  ({p})" + (f"  x{unknown[p]}" if unknown[p] > 1 else "") for p in shown[:15])
            if len(shown) > 15:
                listing += f"\n  ...and {len(shown) - 15} more"
            QMessageBox.information(
                self, "Unrecognised Tags",
                "These bracketed tags weren't recognised, so they were left out of "
                f"ScanInformation, Format and Notes:\n\n{listing}",
            )

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
        if prompt_rename_single_file(self, book.path, lambda p: setattr(book, "path", p), log=_rename_log()):
            self._refresh_table_row(self.books.index(book), book)

    def undo_last_rename(self) -> None:
        """File > Undo Last Rename...: renames the newest logged rename back
        (redactor_common's rename log -- renames aren't on the Undo stack,
        which covers metadata edits only)."""
        def restored(new_path: str, old_path: str) -> None:
            wanted = os.path.normcase(os.path.abspath(new_path))
            for item in self.books:
                if os.path.normcase(os.path.abspath(str(item.path))) == wanted:
                    item.path = str(old_path)

        if undo_last_rename(self, _rename_log(), restored):
            self._rebuild_table()
            self._update_status()

    def rename_selected_file(self) -> None:
        """F2 entry point (Explorer convention: select one item, press
        F2, rename it directly) -- same guard the right-click "Rename
        File..." item uses (exactly one row selected, no load error),
        since F2 and that menu item are the same action reached two
        ways."""
        if len(self._selected_rows) == 1:
            book = self.books[self._selected_rows[0]]
            if not book.load_error:
                self.rename_single_file(book)

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
        renamed: list[tuple[str, str]] = []
        for index, new_value in changes.items():
            book = target_books[index]
            if field_key == FILENAME_FIELD_KEY:
                old_path = book.path
                ext = os.path.splitext(old_path)[1]
                new_path = os.path.join(os.path.dirname(old_path), new_value + ext)
                try:
                    os.rename(old_path, new_path)
                    book.path = new_path
                    renamed.append((old_path, new_path))
                except OSError as exc:
                    errors.append(f"{os.path.basename(old_path)}: {exc}")
            else:
                setattr(book.metadata, field_key, new_value)
                book.dirty = True
            self._refresh_edited_row(book)
        _rename_log().record("Search/Replace (filename)", renamed)

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
            item_noun="file",
            padding=app_settings.load_auto_number_padding(),
            on_padding_changed=app_settings.save_auto_number_padding,
            parent=self,
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
            self._refresh_edited_row(book)
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
            self._refresh_edited_row(book)
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
            self._refresh_edited_row(book)
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

        sizes = self._ensure_page_sizes(target_books)
        oversized_books = [
            book for book in target_books
            if id(book) in sizes and sizes[id(book)].category == SIZE_OVERSIZED
        ]

        dialog = ResizeImagesDialog(
            len(target_books),
            app_settings.load_resize_max_width(),
            app_settings.load_resize_jpeg_quality(),
            parent=self,
            oversized_count=len(oversized_books),
            default_max_height=app_settings.load_resize_max_height(),
            default_output_format=app_settings.load_resize_output_format() or None,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        max_width = dialog.max_width()
        max_height = dialog.max_height()
        output_format = dialog.output_format()
        jpeg_quality = dialog.jpeg_quality()
        app_settings.save_resize_max_width(max_width)
        app_settings.save_resize_max_height(max_height or 0)
        app_settings.save_resize_output_format(output_format or "")
        app_settings.save_resize_jpeg_quality(jpeg_quality)
        export_mode = dialog.is_export_mode()
        if dialog.oversized_only():
            target_books = oversized_books

        errors: list[str] = []
        exported_paths: list[str] = []
        totals = {"resized": 0, "skipped": 0, "failed": 0, "original_bytes": 0, "new_bytes": 0}

        total_pages = sum(max(len(b.page_names), 1) for b in target_books)
        cancelled = threading.Event()
        # Written by the worker thread, read by the GUI-thread timer below.
        live = {"pages_before": 0, "done": 0, "total": 0}

        def _apply(book: CbzBook, output_path: str | None, summary) -> None:
            totals["resized"] += summary.pages_resized
            totals["skipped"] += summary.pages_skipped
            totals["failed"] += summary.pages_failed
            totals["original_bytes"] += summary.original_bytes
            totals["new_bytes"] += summary.new_bytes

            if output_path:
                exported_paths.append(output_path)
            else:
                # Pixels changed on disk: forget the old measurement so
                # the Size cell is re-measured (see the schedule() below).
                self._size_source.pop(book)
                row = self.books.index(book)
                self._refresh_table_row(row, book)
                if self._selected_rows == [row]:
                    page_count_text = f"{book.actual_page_count} page(s)"
                    self._show_book_in_panel(book, page_count_text)

        # Each book is re-encoded on a worker thread (call_in_background)
        # so the window keeps repainting; a timer on this thread turns the
        # worker's per-page counter into the progress dialog and status bar.
        with ProgressReporter(
            self, total_pages, "Resizing images...", threshold=RESIZE_PROGRESS_THRESHOLD
        ) as reporter:
            reporter.connect_cancel(cancelled.set)
            current = {"name": ""}

            def _tick() -> None:
                done = live["pages_before"] + live["done"]
                reporter.set_label(f"Resizing: {current['name']} (page {live['done']} of {live['total']})")
                reporter.set_value(done, pump=False)
                self.statusBar().showMessage(f"Resizing {current['name']}: {done} of {total_pages} page(s)")

            timer = QTimer(self)
            timer.timeout.connect(_tick)
            timer.start(100)
            try:
                for book in target_books:
                    if cancelled.is_set():
                        break
                    output_path = dialog.output_path_for(book.path)
                    current["name"] = os.path.basename(book.path)
                    live["done"], live["total"] = 0, max(len(book.page_names), 1)

                    def _on_page(done: int, total: int) -> None:
                        live["done"], live["total"] = done, total

                    try:
                        summary = call_in_background(
                            book.resize_images,
                            max_width, jpeg_quality, output_path,
                            max_height=max_height, output_format=output_format,
                            progress=_on_page, should_cancel=cancelled.is_set,
                        )
                    except ResizeCancelled:
                        break
                    except CbzError as exc:
                        errors.append(f"{os.path.basename(book.path)}: {exc}")
                    else:
                        _apply(book, output_path, summary)
                    live["pages_before"] += live["total"]
            finally:
                timer.stop()
        self.statusBar().clearMessage()

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Resize", summarize_errors(errors))

        processed = totals["resized"] + totals["skipped"] + totals["failed"]
        if processed:
            mb = 1024 * 1024
            old_mb = totals["original_bytes"] / mb
            new_mb = totals["new_bytes"] / mb
            diff_mb = abs(old_mb - new_mb)
            if round(diff_mb, 1) == 0:
                change = "no change in size"
            elif new_mb < old_mb:
                change = f"{diff_mb:.1f} MB smaller"
            else:
                change = f"{diff_mb:.1f} MB larger"
            QMessageBox.information(
                self, "Resize Complete",
                f"{totals['resized']} page(s) resized or converted, {totals['skipped']} already small enough, "
                f"{totals['failed']} couldn't be read.\n\n"
                f"Total size went from {old_mb:.1f} MB to {new_mb:.1f} MB ({change}).",
            )

        if export_mode and exported_paths:
            self._load_paths(exported_paths)
        self._visible_rows.schedule()
        self._update_status()

    def tag_low_res_scans(self) -> None:
        """Adds the "Low-res scan" tag (core/scan_quality_tag.py) to
        every targeted book whose Size is low-res (yellow), and removes
        it from any that carry it but no longer are -- e.g. a copy since
        replaced by a better scan -- so the tag stays accurate when run
        again. A normal metadata edit: undoable, written on Save."""
        self._commit_current_edits()
        target_books = self._target_books()
        if not target_books:
            QMessageBox.information(self, "No Files", "Load some files first (or select the ones to check).")
            return

        sizes = self._ensure_page_sizes(target_books)
        to_add, to_remove = [], []
        for book in target_books:
            stats = sizes.get(id(book))
            if stats is None or stats.category is None:
                continue  # couldn't measure -- leave its tags alone
            tagged = has_tag(book.metadata.tags)
            if stats.category == SIZE_LOW and not tagged:
                to_add.append(book)
            elif stats.category != SIZE_LOW and tagged:
                to_remove.append(book)

        if not to_add and not to_remove:
            QMessageBox.information(
                self, "Tag Low-Res Scans",
                f'Nothing to change: every low-res file already has the "{LOW_RES_TAG}" tag.',
            )
            return

        lines = []
        if to_add:
            lines.append(f'Add "{LOW_RES_TAG}" to {len(to_add)} low-res file(s) (under 1000px wide).')
        if to_remove:
            lines.append(f"Remove it from {len(to_remove)} file(s) that are no longer low-res.")
        reply = QMessageBox.question(
            self, "Tag Low-Res Scans",
            "\n".join(lines) + "\n\nOther tags are kept. Written on Save; can be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._push_undo("Tag Low-Res Scans", to_add + to_remove)
        for book in to_add:
            book.metadata.tags = add_tag(book.metadata.tags)
        for book in to_remove:
            book.metadata.tags = remove_tag(book.metadata.tags)
        for book in to_add + to_remove:
            book.dirty = True
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                self._show_book_in_panel(book, f"{book.actual_page_count} page(s)")
        self._update_status()

    # ------------------------------------------------------------------
    # Scanner credit pages (core/credit_pages.py)
    # ------------------------------------------------------------------

    def _ensure_credit_scans(self, books: list[CbzBook]) -> None:
        """Fingerprints every book in `books` not scanned yet, under a
        progress dialog (sorting by the column, or the batch removal,
        needs all of them -- not only the visible rows)."""
        def _step(book: CbzBook, _index: int) -> None:
            if book.load_error or book.needs_conversion or not book.first_page_name:
                return
            try:
                source = (book.path, os.path.getmtime(book.path))
            except OSError:
                return
            self._size_source[book] = source
            self._credit_scanner.scan_now(book, source)

        pending = [b for b in books if self._credit_scanner.get_cached(b, self._size_source.get(b)) is None]
        run_with_progress(
            self, pending, _step, "Checking for credit pages...", threshold=LOAD_PROGRESS_THRESHOLD,
            cancellable=False, label_for=lambda book: f"Checking: {os.path.basename(book.path)}",
        )

    def open_credit_pages_dialog(self, book: CbzBook) -> None:
        """Right-click > Credit Pages...: this book's first/last pages as
        thumbnails; ticked ones are learned and removed."""
        from gui.credit_pages_dialogs import CreditPagesDialog

        self._commit_current_edits()
        if book.dirty:
            QMessageBox.information(
                self, "Credit Pages", "This file has unsaved changes -- save it first, then remove pages."
            )
            return
        candidates = call_in_background(scan_book, book.path, list(book.page_names), True)
        dialog = CreditPagesDialog(os.path.basename(book.path), candidates, self._known_credits, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        ticked = dialog.ticked()
        if not ticked:
            return
        for candidate in ticked:
            if candidate.hash is not None:
                self._known_credits.add(candidate.hash, candidate.thumbnail, candidate.name)
        self._remove_pages([(book, [c.name for c in ticked])])
        self._refresh_all_credit_cells()

    def open_remove_credit_pages_dialog(self) -> None:
        """Operations > Remove Credit Pages...: finds every learned credit
        page in the selected files (or all), for review, then removes the
        ticked ones."""
        from gui.credit_pages_dialogs import RemoveCreditPagesDialog

        self._commit_current_edits()
        if not self._known_credits.pages:
            QMessageBox.information(
                self, "Remove Credit Pages",
                "No credit pages have been learned yet. Right-click a file whose credit page "
                "you can see, choose Credit Pages..., tick it and remove it -- from then on the "
                "same page is found in every file.",
            )
            return
        target_books = self._target_books()
        unsaved = [b for b in target_books if b.dirty]
        target_books = [b for b in target_books if not b.dirty]
        self._ensure_credit_scans(target_books)

        with_matches = [b for b in target_books if self._credit_matches_for(b)]
        found = []

        def _step(book: CbzBook, _index: int) -> None:
            # Thumbnails only for the books that need showing.
            for candidate in credit_matches(scan_book(book.path, list(book.page_names), True), self._known_credits):
                found.append((book, os.path.basename(book.path), candidate))

        run_with_progress(self, with_matches, _step, "Preparing previews...", threshold=LOAD_PROGRESS_THRESHOLD, cancellable=False)
        skipped = f"{len(unsaved)} file(s) with unsaved changes were skipped -- save them first." if unsaved else ""
        if not found:
            QMessageBox.information(
                self, "Remove Credit Pages",
                f"No known credit pages found in {len(target_books)} file(s)." + (f"\n\n{skipped}" if skipped else ""),
            )
            return
        dialog = RemoveCreditPagesDialog(found, skipped, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        by_book: dict[int, tuple[CbzBook, list[str]]] = {}
        for book, _label, candidate in dialog.ticked():
            by_book.setdefault(id(book), (book, []))[1].append(candidate.name)
        self._remove_pages(list(by_book.values()))

    def _remove_pages(self, work: list[tuple[CbzBook, list[str]]]) -> None:
        """Removes pages from each book (originals to the Recycle Bin)
        and refreshes their rows."""
        errors: list[str] = []
        removed_total = 0
        size_before = size_after = 0

        def _step(entry, _index: int) -> None:
            nonlocal removed_total, size_before, size_after
            book, names = entry
            before = _file_size_bytes(book.path) or 0
            try:
                removed_total += book.remove_pages(names, dispose_original=move_to_trash)
            except (CbzError, TrashError) as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
                return
            size_before += before
            size_after += _file_size_bytes(book.path) or 0
            self._size_source.pop(book)
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                self._show_book_in_panel(book, f"{book.actual_page_count} page(s)")

        run_with_progress(
            self, work, _step, "Removing credit pages...", threshold=1,
            label_for=lambda entry: f"Rewriting: {os.path.basename(entry[0].path)}",
        )
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed", summarize_errors(errors))
        self._visible_rows.schedule()
        self._update_status()
        if removed_total:
            saved_mb = (size_before - size_after) / (1024 * 1024)
            self.statusBar().showMessage(
                f"Removed {removed_total} credit page(s) from {len(work) - len(errors)} file(s), "
                f"{saved_mb:.1f} MB smaller. Originals are in the Recycle Bin.", 10000,
            )

    # ------------------------------------------------------------------
    # Duplicates (core/duplicates.py)
    # ------------------------------------------------------------------

    def _ensure_fingerprints(self, books: list[CbzBook]) -> None:
        """Fingerprints books not done yet, several at once on a thread
        pool (decoding a few pages per book is the slow part -- about a
        second for a book of large WebP pages), under a progress dialog."""
        from concurrent.futures import ThreadPoolExecutor

        pending = []
        for book in books:
            try:
                source = (book.path, os.path.getmtime(book.path))
            except OSError:
                continue
            self._size_source[book] = source
            if self._dupe_fingerprints.get_cached(book, source) is None:
                pending.append((book, source))
        if not pending:
            return
        scan = self._dupe_fingerprints.scan_function
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
            futures = [(book, source, pool.submit(scan, book.path, list(book.page_names))) for book, source in pending]

            def _step(entry, _index: int) -> None:
                book, source, future = entry
                self._dupe_fingerprints.store(book, source, future.result())

            finished = run_with_progress(
                self, futures, _step, "Fingerprinting pages...", threshold=1,
                label_for=lambda entry: f"Fingerprinting: {os.path.basename(entry[0].path)}",
            )
            if finished is False:
                for _book, _source, future in futures:
                    future.cancel()

    def open_validate_fix_dialog(self) -> None:
        """Operations > Validate / Fix Issues...: ComicInfo mistakes in the
        selected files (or all) -- see core/comicinfo_check.py -- reviewed,
        then applied as ordinary edits: one Undo step, written on Save."""
        from core.comicinfo_check import check_book
        from gui.validate_fix_dialog import ValidateFixDialog

        self._commit_current_edits()
        target_books = [b for b in self._target_books() if not b.load_error and not b.needs_conversion]
        found: list = []

        def _check(book: CbzBook, _index: int) -> None:
            found.extend((book, finding) for finding in check_book(book))

        run_with_progress(self, target_books, _check, "Checking metadata...",
                          threshold=LOAD_PROGRESS_THRESHOLD, update_every=20)
        if not found:
            QMessageBox.information(self, "Validate / Fix Issues", f"No issues found in {len(target_books)} file(s).")
            return
        dialog = ValidateFixDialog(found, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        chosen = dialog.ticked()
        if not chosen:
            return
        books = list({id(book): book for book, _finding in chosen}.values())
        self._push_undo("Validate & Fix", books)
        for book, finding in chosen:
            setattr(book.metadata, finding.field, finding.fix)
            book.dirty = True
        for book in books:
            self._refresh_edited_row(book)
        self._update_status()
        self.statusBar().showMessage(
            f"Applied {len(chosen)} fix(es) to {len(books)} file(s) -- Save to write them.", 10000,
        )

    def open_clean_contents_dialog(self) -> None:
        """Operations > Clean Up Archive Contents...: plain numbered page
        names, no page folders, no junk files inside the selected
        archives (or all) -- see core/archive_contents.py -- reviewed
        first, originals to the Recycle Bin."""
        from gui.archive_cleanup_dialog import ArchiveCleanupDialog

        self._commit_current_edits()
        target_books = [b for b in self._target_books() if not b.load_error and not b.needs_conversion]
        unsaved = [b for b in target_books if b.dirty]
        target_books = [b for b in target_books if not b.dirty]
        work, errors = [], []

        def _plan(book: CbzBook, _index: int) -> None:
            try:
                plan = book.cleanup_plan()
            except (zipfile.BadZipFile, OSError) as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
                return
            if plan.needed:
                work.append((book, plan))

        run_with_progress(self, target_books, _plan, "Checking archive contents...",
                          threshold=LOAD_PROGRESS_THRESHOLD,
                          label_for=lambda b: f"Checking: {os.path.basename(b.path)}")
        skipped = f"{len(unsaved)} file(s) with unsaved changes were skipped -- save them first." if unsaved else ""
        if not work:
            QMessageBox.information(
                self, "Clean Up Archive Contents",
                f"Nothing to clean up in {len(target_books)} file(s)." + (f"\n\n{skipped}" if skipped else ""),
            )
            return
        dialog = ArchiveCleanupDialog(work, skipped, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        chosen = dialog.ticked()
        cleaned = 0

        def _clean(book: CbzBook, _index: int) -> None:
            nonlocal cleaned
            try:
                book.clean_contents(dispose_original=move_to_trash)
            except (CbzError, TrashError) as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
                return
            cleaned += 1
            self._size_source.pop(book)  # page names changed: rescan Size/Credit Pages
            row = self.books.index(book)
            self._refresh_table_row(row, book)
            if self._selected_rows == [row]:
                self._show_book_in_panel(book, f"{book.actual_page_count} page(s)")

        run_with_progress(self, chosen, _clean, "Cleaning up archives...", threshold=1,
                          label_for=lambda b: f"Rewriting: {os.path.basename(b.path)}")
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed", summarize_errors(errors))
        self._visible_rows.schedule()
        self._update_status()
        if cleaned:
            self.statusBar().showMessage(
                f"Cleaned up {cleaned} archive(s). Originals are in the Recycle Bin.", 10000,
            )

    def scan_collection_folder(self) -> None:
        """Collection > Scan Collection Folder...: reads every comic under
        the chosen folder into collection_scan.zip next to the settings
        (core/collection_scan.py) -- only when asked, nothing is watched.
        Files unchanged since the last scan of the same folder aren't read
        again; a stopped scan keeps what it read."""
        from PyQt6.QtWidgets import QApplication, QProgressDialog
        from core import collection_scan as scan

        zip_path = _collection_scan_path()
        previous_info, previous_rows = None, []
        if os.path.exists(zip_path):
            try:
                previous_info, previous_rows = scan.read_scan(zip_path)
            except scan.ScanFileError:
                pass
        start = previous_info.root if previous_info and os.path.isdir(previous_info.root) else ""
        root = QFileDialog.getExistingDirectory(self, "Scan Collection Folder", start)
        if not root:
            return
        root = os.path.abspath(root)
        same_root = previous_info is not None and os.path.normcase(previous_info.root) == os.path.normcase(root)
        previous = {row.path: row for row in previous_rows} if same_root else {}
        stashed = scan.stash_path(zip_path, root)
        if not same_root and os.path.exists(stashed):
            # This folder was scanned before, then another one replaced it as the current scan.
            try:
                _old_info, old_rows = scan.read_scan(stashed)
                previous = {row.path: row for row in old_rows}
                previous_rows = old_rows
            except scan.ScanFileError:
                pass
        mode = self._ask_cover_mode(any(row.cover for row in previous.values()))
        if mode is None:
            return
        covers, stamp = mode != "none", mode == "store"

        listing = QProgressDialog("Listing comics…", "Cancel", 0, 0, self)
        listing.setWindowTitle("Scan Collection Folder")
        listing.setWindowModality(Qt.WindowModality.WindowModal)
        listing.setMinimumDuration(0)
        listing.show()
        listed = []
        for item in scan.list_comics(root, listing.wasCanceled):
            listed.append(item)
            if len(listed) % 200 == 0:
                listing.setLabelText(f"Listing comics… {len(listed):,} found")
                QApplication.processEvents()
        cancelled = listing.wasCanceled()
        listing.close()
        if cancelled:
            return
        if not listed:
            QMessageBox.information(self, "Scan Collection Folder", f"No comics found in:\n{root}")
            return

        rows: dict[str, scan.ScanRow] = {}
        to_read = []
        for item in listed:
            old = scan.reusable(previous, item, covers)
            if old is not None:
                rows[item.path] = old
            else:
                to_read.append(item)

        known_covers = {r.cover_key: r.cover for r in previous_rows if r.cover_key and r.cover} if covers else None
        with ProgressReporter(self, len(to_read), "Reading comics…", threshold=1,
                              title="Scan Collection Folder") as reporter:
            def _progress(done: int, total: int) -> None:
                reporter.set_label(f"Reading comics… {done:,} of {total:,}")
                reporter.set_value(done)

            read, complete = scan.read_comics(root, to_read, _progress, reporter.should_cancel, cover=covers,
                                             known_covers=known_covers, stamp=stamp)
        rows.update(read)
        read_count = len(read)
        if not complete:
            for item in to_read:  # not reached: keep what the last scan knew
                if item.path not in rows and item.path in previous:
                    rows[item.path] = previous[item.path]
        try:
            if previous_info is not None and not same_root and os.path.exists(zip_path):
                scan.stash_current(zip_path, previous_info)  # the other folder's scan is kept, not overwritten
            scan.write_scan(zip_path, scan.new_info(root, complete, APP_VERSION), list(rows.values()))
            if os.path.exists(stashed):
                os.remove(stashed)  # now the current scan
        except OSError as exc:
            QMessageBox.warning(self, "Scan Collection Folder", f"Couldn't save the scan:\n{exc}")
            return
        summary = (f"{len(listed):,} comic(s) found, {read_count:,} read"
                   f"{' (the rest unchanged since the last scan)' if read_count < len(listed) else ''}.")
        if not complete:
            summary = ("Scan stopped. " + summary + " What was read is saved; scan again to carry on "
                       "from where it stopped.")
        answer = QMessageBox.question(
            self, "Scan Collection Folder",
            f"{summary}\n\nSaved to {zip_path}\n\nOpen the Collection Report now?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_collection_report()

    def _build_report_in_background(self, build_report, info, rows):
        """build_report() on a worker thread, with a progress dialog, so the
        window stays alive while a collection of 100,000+ comics is analysed."""
        import threading
        from PyQt6.QtWidgets import QApplication, QProgressDialog

        result: dict = {}

        def work() -> None:
            try:
                result["report"] = build_report(info, rows)
            except BaseException as exc:  # re-raised on the GUI thread below
                result["error"] = exc

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        dialog = None
        if len(rows) > 500:
            dialog = QProgressDialog(f"Analysing {len(rows):,} comics…", None, 0, 0, self)
            dialog.setWindowTitle("Collection Report")
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.show()
        while thread.is_alive():
            QApplication.processEvents()
            thread.join(0.05)
        if dialog is not None:
            dialog.close()
        if "error" in result:
            raise result["error"]
        return result["report"]

    def _ask_cover_mode(self, had_covers: bool) -> str | None:
        """What a scan does about covers: "none", "scan" (fingerprint each
        cover into the scan file) or "store" (also write it into the CBZ,
        as its ZIP comment -- core/cover_stamp.py). None: cancelled."""
        from PyQt6.QtWidgets import QInputDialog

        choices = {
            "Don't fingerprint covers": "none",
            "Fingerprint covers (kept in the scan file)": "scan",
            "Fingerprint covers and store them in the CBZ files (ZIP comment)": "store",
        }
        labels = list(choices)
        choice, ok = QInputDialog.getItem(
            self, "Scan Collection Folder",
            "Fingerprinting decodes each comic's first page, so a first scan takes longer. The Collection "
            "Report uses it to confirm duplicates by their covers and to find copies of one issue filed "
            "under different names.\n\nStoring it in the CBZ changes each file (only the end of the archive; "
            "no file is added) so the fingerprint stays with the comic if it is moved or renamed.",
            labels, 1 if had_covers else 0, False,
        )
        return choices[choice] if ok else None

    def open_collection_report(self) -> None:
        """Collection > Collection Report...: patterns and irregularities
        in the last scan (core/collection_report.py). Ticked moves and
        renames are applied only where the scanned folder exists, and go
        in the rename log, so File > Undo Last Rename takes them back."""
        from PyQt6.QtWidgets import QApplication
        from core import collection_scan as scan
        from core.collection_report import build_report
        from gui.collection_report_dialog import CollectionReportDialog

        zip_path = _collection_scan_path()
        others = scan.stashed_scans(zip_path)
        if others and os.path.exists(zip_path):
            from PyQt6.QtWidgets import QInputDialog
            try:
                current_info = scan.read_scan(zip_path)[0]
            except scan.ScanFileError:
                current_info = None
            options = {}
            if current_info is not None:
                options[f"{current_info.root}  (last scanned {current_info.scanned})"] = zip_path
            for path, info in others:
                options[f"{info.root}  (scanned {info.scanned})"] = path
            choice, ok = QInputDialog.getItem(self, "Collection Report", "Which folder's scan?",
                                              list(options), 0, False)
            if not ok:
                return
            zip_path = options[choice]
        if not os.path.exists(zip_path):
            QMessageBox.information(
                self, "Collection Report",
                "No collection scan yet. Use Collection > Scan Collection Folder... first -- or copy a "
                f"{scan.SCAN_FILE_NAME} made on another computer to:\n{os.path.dirname(zip_path)}",
            )
            return
        try:
            info, rows = scan.read_scan(zip_path)
        except scan.ScanFileError as exc:
            QMessageBox.warning(self, "Collection Report", str(exc))
            return
        report = self._build_report_in_background(build_report, info, rows)
        dialog = CollectionReportDialog(info, len(rows), report, os.path.isdir(info.root), self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        pairs, fixes = dialog.ticked(), dialog.ticked_fixes()
        if pairs or fixes:
            self._apply_collection_changes(zip_path, info, rows, pairs, fixes)

    def _apply_collection_changes(self, zip_path: str, info, rows: list, pairs: list[tuple[str, str]],
                                  fixes: list) -> None:
        """Ticked fixes first (ComicInfo edits inside the archives, one
        rewrite per file), then every rename -- the report's moves and
        renames and the fixes that rename -- through the rename log."""
        from core import collection_scan as scan
        from core.collection_fix import apply_edits, drop_conflicts, group_edits

        fixes = drop_conflicts(fixes)
        edits = group_edits(fixes)
        conversions = [f for f in fixes if f.convert]
        summary: list[str] = []
        failed: set[str] = set()
        if edits:
            answer = QMessageBox.question(
                self, "Apply Fixes",
                f"{len(edits)} archive(s) will be rewritten with the ComicInfo changes ticked in the report "
                "(each written to a temporary file first). Undo Last Rename can't take these back.\n\nContinue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                edits, fixes = {}, [f for f in fixes if f.is_rename or f.convert]
                if not (pairs or fixes):
                    return
        if edits:
            errors: list[str] = []
            by_path = {row.path: index for index, row in enumerate(rows)}

            def _edit(item: tuple[str, dict], _index: int) -> None:
                path, fields = item
                try:
                    apply_edits(info.root, path, fields)
                    full = scan.long_path(scan.full_path(info.root, path))
                    stat = os.stat(full)
                    listed = scan.Listed(path, stat.st_size, scan._stamp(stat.st_mtime))
                    if path in by_path:
                        rows[by_path[path]] = scan.read_comic(info.root, listed)  # the scan stays in step
                except (CbzError, OSError) as exc:
                    failed.add(path)
                    errors.append(f"{path.rsplit('/', 1)[-1]}: {exc}")

            finished = run_with_progress(self, list(edits.items()), _edit, "Fixing ComicInfo...",
                                         label_for=lambda item: f"Fixing: {item[0].rsplit('/', 1)[-1]}")
            summary.append(f"{len(edits) - len(failed)} archive(s) rewritten."
                           + ("" if finished else " Stopped before the rest."))
            if errors:
                summary.append(f"{len(errors)} failed:\n" + "\n".join(errors[:10]))
            try:
                scan.write_scan(zip_path, info, rows)
            except OSError:
                pass
        if conversions:
            self._convert_collection_files(zip_path, info, rows, conversions, summary, failed)
        renames, seen = [], set()
        for old, new in pairs + [(f.path, f.new_path) for f in fixes if f.is_rename]:
            if old not in seen and old not in failed:
                seen.add(old)
                renames.append((old, new))
        self._apply_collection_moves(zip_path, info, rows, renames, "\n\n".join(summary))

    def _convert_collection_files(self, zip_path: str, info, rows: list, conversions: list, summary: list[str],
                                  failed: set[str]) -> None:
        """Convert to CBZ for the ticked CBR/CB7/CBT rows: converted and
        verified by _convert_path() (a mislabeled .cbz is relabeled first),
        original to the Recycle Bin, and the scan row swapped for the new
        file's. A file whose .cbz name is taken is skipped and reported."""
        from core import collection_scan as scan

        errors: list[str] = []
        converted = 0
        index = {row.path: i for i, row in enumerate(rows)}

        def _convert(fix, _index: int) -> None:
            nonlocal converted
            source = scan.full_path(info.root, fix.path)
            new_path = self._convert_path(scan.long_path(source), True, errors)
            if new_path is None:
                failed.add(fix.path)
                return
            converted += 1
            new_rel = posixpath.splitext(fix.path)[0] + ".cbz"
            stat = os.stat(new_path)
            row = scan.read_comic(info.root, scan.Listed(new_rel, stat.st_size, scan._stamp(stat.st_mtime)))
            if fix.path in index:
                rows[index[fix.path]] = row
                index[new_rel] = index.pop(fix.path)

        finished = run_with_progress(self, conversions, _convert, "Converting to CBZ...",
                                     label_for=lambda fix: f"Converting: {fix.path.rsplit('/', 1)[-1]}")
        summary.append(f"{converted} file(s) converted to CBZ; originals are in the Recycle Bin."
                       + ("" if finished else " Stopped before the rest."))
        if errors:
            summary.append(f"{len(errors)} not converted:\n" + "\n".join(errors[:10]))
        try:
            scan.write_scan(zip_path, info, rows)
        except OSError:
            pass

    def _apply_collection_moves(self, zip_path: str, info, rows: list, pairs: list[tuple[str, str]],
                                intro: str = "") -> None:
        from core import collection_scan as scan

        done: list[tuple[str, str]] = []
        problems: list[str] = []
        for old_rel, new_rel in pairs:
            old = scan.full_path(info.root, old_rel)
            new = scan.full_path(info.root, new_rel)
            name = old_rel.rsplit("/", 1)[-1]
            if not os.path.exists(scan.long_path(old)):
                problems.append(f"{name}: no longer there")
            elif os.path.exists(scan.long_path(new)):
                problems.append(f"{name}: {new_rel} already exists")
            elif not os.path.isdir(scan.long_path(os.path.dirname(new))):
                problems.append(f"{name}: the folder {os.path.dirname(new_rel)} is gone")
            else:
                try:
                    os.rename(scan.long_path(old), scan.long_path(new))
                except OSError as exc:
                    problems.append(f"{name}: {exc}")
                    continue
                # The rename log keeps long paths in their "\?\" form, or
                # Undo Last Rename couldn't reach them.
                if max(len(old), len(new)) >= scan.WINDOWS_PATH_LIMIT - 10:
                    done.append((scan.long_path(old), scan.long_path(new)))
                else:
                    done.append((old, new))
                for row in rows:
                    if row.path == old_rel:
                        row.path = new_rel
        if done:
            _rename_log().record("Collection Report", done)
            moved = {os.path.normcase(os.path.abspath(o)): n for o, n in done}
            for book in self.books:
                target = moved.get(os.path.normcase(os.path.abspath(str(book.path))))
                if target:
                    book.path = target
            self._rebuild_table()
            try:
                scan.write_scan(zip_path, info, rows)  # keep the report in step with the files
            except OSError:
                pass
        text = f"{len(done)} file(s) moved or renamed. File > Undo Last Rename takes them back." if pairs else ""
        if intro:
            text = f"{intro}\n\n{text}" if text else intro
        if problems:
            text += f"\n\n{len(problems)} skipped:\n" + "\n".join(problems[:15])
            if len(problems) > 15:
                text += f"\n… and {len(problems) - 15} more"
        QMessageBox.information(self, "Collection Report", text)

    def open_find_duplicates_dialog(self) -> None:
        """Operations > Find Duplicates...: the same comic loaded more than
        once (another release, resolution or format), with the best copy
        suggested -- see core/duplicates.py. Ticked copies go to the
        Recycle Bin and leave the list."""
        from gui.duplicates_dialog import DuplicatesDialog

        self._commit_current_edits()
        books = [b for b in self._target_books() if not b.load_error and b.page_names]
        if len(books) < 2:
            QMessageBox.information(self, "Find Duplicates", "Load (or select) at least two files to compare.")
            return
        sizes = self._ensure_page_sizes(books)
        self._ensure_fingerprints(books)

        kept_books, facts = [], []
        for book in books:
            fingerprint = self._dupe_fingerprints.get_cached(book, self._size_source.get(book))
            if fingerprint is None:
                continue  # fingerprinting was cancelled for this one
            stats = sizes.get(id(book))
            credit = self._credit_matches_for(book) or []
            meta = book.metadata
            filled = sum(
                1 for name in vars(meta)
                if name not in ("extra_elements", "page_count") and str(getattr(meta, name) or "").strip()
            )
            kept_books.append(book)
            facts.append(BookFacts(
                fingerprint=fingerprint,
                width=(stats.representative_width or 0) if stats else 0,
                pages=book.actual_page_count - len(credit),
                metadata_fields=filled,
                file_size=_file_size_bytes(book.path) or 0,
                web=meta.web or "",
            ))

        groups = find_duplicates(facts)
        if not groups:
            QMessageBox.information(self, "Find Duplicates", f"No duplicates among {len(kept_books)} file(s).")
            return
        labels = [os.path.basename(b.path) for b in kept_books]
        covers = [None] * len(kept_books)
        for group in groups:
            for index in group.members:
                covers[index] = kept_books[index].read_first_page_bytes()
        dialog = DuplicatesDialog(groups, facts, labels, covers, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        doomed = [kept_books[i] for i in dialog.ticked()]
        if not doomed:
            return
        unsaved = [b for b in doomed if b.dirty]
        if unsaved and not self._confirm_discard(f"move {len(unsaved)} file(s) with unsaved changes to the Recycle Bin"):
            return

        errors: list[str] = []
        gone: list[CbzBook] = []
        for book in doomed:
            try:
                move_to_trash(book.path)
                gone.append(book)
            except TrashError as exc:
                errors.append(f"{os.path.basename(book.path)}: {exc}")
        if gone:
            gone_ids = {id(b) for b in gone}
            self.books = [b for b in self.books if id(b) not in gone_ids]
            self._selected_rows = []
            self.undo_manager.clear()  # its entries could reference removed books
            self._update_undo_action()
            self._update_redo_action()
            self._rebuild_table()
            self.panel.set_enabled(False)
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Couldn't Be Moved", summarize_errors(errors))
        self._update_status()
        if gone:
            self.statusBar().showMessage(f"Moved {len(gone)} duplicate(s) to the Recycle Bin.", 10000)

    def open_known_credit_pages_dialog(self) -> None:
        from gui.credit_pages_dialogs import KnownCreditPagesDialog

        KnownCreditPagesDialog(self._known_credits, self).exec()
        self._refresh_all_credit_cells()

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def convert_foreign_archives_dialog(self) -> None:
        """Import > Convert to CBZ: converts the list's unconverted rows
        (the selected ones, or all of them if none are selected) in
        place. With nothing unconverted in the list, falls back to
        picking files from disk, as before."""
        selected = [self.books[r] for r in self._selected_rows if self.books[r].needs_conversion]
        in_list = selected or [book for book in self.books if book.needs_conversion]
        if in_list:
            self.convert_books_to_cbz(in_list)
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self, "Convert to CBZ", "", "Comic Archives (*.cbr *.cbt *.cb7 *.cbz)"
        )
        if not paths:
            return
        paths = [p for p in paths if path_needs_conversion(p)]
        if not paths:
            QMessageBox.information(self, "Nothing to Convert", "Those files are already real CBZ files.")
            return
        # Picking files here already says "convert these" -- no prompt;
        # the Recycle Bin choice comes from Settings like every other
        # conversion.
        should_delete = app_settings.load_recycle_originals()

        errors: list[str] = []
        converted: list[str] = []

        def _step(path: str, _index: int) -> None:
            new_path = self._convert_path(path, should_delete, errors)
            if new_path:
                converted.append(new_path)

        run_with_progress(self, paths, _step, "Converting to CBZ...", threshold=LOAD_PROGRESS_THRESHOLD)

        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Convert", summarize_errors(errors))
        if converted:
            QMessageBox.information(
                self, "Conversion Complete", f"Converted {len(converted)} file(s) to .cbz."
            )
            self._load_paths(converted)

    def convert_books_to_cbz(self, books: list[CbzBook]) -> None:
        """Converts listed-but-unconverted rows in place: each row is
        replaced by the converted .cbz, keeping its position. Asks
        whether to move the originals to the Recycle Bin."""
        books = [book for book in books if book.needs_conversion]
        if not books:
            return
        recycle = app_settings.load_recycle_originals()
        originals = (
            "The originals will be moved to the Recycle Bin afterwards."
            if recycle else "The originals will be kept next to the new .cbz files."
        )
        reply = QMessageBox.question(
            self, "Convert to CBZ",
            f"Convert {len(books)} file(s) to CBZ?\n\n"
            "Each converted file is checked (it opens, and has as many pages "
            f"as the original) before anything else happens. {originals}\n\n"
            "(Change this in Settings > Converting to CBZ...)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._convert_books(books, recycle)

    def open_conversion_settings_dialog(self) -> None:
        ConversionSettingsDialog(self).exec()

    def open_gcd_local_settings_dialog(self) -> None:
        from gui.gcd_local_settings_dialog import GcdLocalSettingsDialog

        GcdLocalSettingsDialog(self).exec()

    def open_comicrack_settings_dialog(self) -> None:
        from gui.comicrack_settings_dialog import ComicRackSettingsDialog

        ComicRackSettingsDialog(self).exec()

    def open_gcd_local_lookup_dialog(self) -> None:
        self._open_local_lookup(
            "GCD Local Database", app_settings.load_gcd_local_database, self.open_gcd_local_settings_dialog,
            "No local GCD database is set up yet. It's a free download from the "
            "Grand Comics Database (you need a comics.org account).\n\n"
            "Open Settings > GCD Local Database... for instructions?",
        )

    def compare_library_with_gcd(self) -> None:
        from gui.gcd_compare_flow import compare_library_with_gcd

        compare_library_with_gcd(self)

    def open_comicrack_lookup_dialog(self) -> None:
        self._open_local_lookup(
            "ComicRack Library Database", app_settings.load_comicrack_database, self.open_comicrack_settings_dialog,
            "No ComicRack library has been converted yet -- it's built from ComicRack's "
            "ComicDb.xml file.\n\nOpen Settings > ComicRack Library Database... to build it?",
        )

    def _open_local_lookup(self, name: str, load_path, open_settings, not_set_up: str) -> None:
        """Look Up via a local database (GCD's dump or a converted ComicRack
        library): needs its file set in Settings first -- if it isn't,
        explain and offer to open that dialog rather than just failing."""
        from core.gcd_local import GcdLocalError, open_database
        from gui.gcd_local_lookup_dialog import GcdLocalLookupDialog

        path = load_path()
        if not path:
            reply = QMessageBox.question(
                self, name, not_set_up,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                open_settings()
            path = load_path()
            if not path:
                return
        try:
            database = open_database(path)
        except GcdLocalError as exc:
            QMessageBox.warning(self, name, f"{exc}\n\nCheck Settings > {name}...")
            return
        self._run_lookup_dialog(
            GcdLocalLookupDialog, f"{name} lookup",
            factory=lambda books: GcdLocalLookupDialog(books, database, self),
        )

    def open_gcd_account_dialog(self) -> None:
        from gui.gcd_account_dialog import GcdAccountDialog

        GcdAccountDialog(self).exec()

    def _convert_books(self, books: list[CbzBook], delete_originals: bool) -> None:
        errors: list[str] = []
        converted = 0

        def _step(book: CbzBook, _index: int) -> None:
            nonlocal converted
            new_path = self._convert_path(book.path, delete_originals, errors)
            if new_path is None:
                return
            try:
                row = self.books.index(book)
            except ValueError:
                return
            new_book = CbzBook(new_path)
            if new_book.load_error:
                errors.append(f"{os.path.basename(new_path)}: {new_book.load_error}")
            self.books[row] = new_book
            self._refresh_table_row(row, new_book)
            converted += 1

        run_with_progress(
            self, books, _step, "Converting to CBZ...", threshold=1,
            label_for=lambda book: f"Converting: {os.path.basename(book.path)}",
        )
        if errors:
            from redactor_common.core.error_summary import summarize_errors
            QMessageBox.warning(self, "Some Files Failed to Convert", summarize_errors(errors))
        self._visible_rows.schedule()
        self._on_selection_changed()
        self._update_status()
        self.statusBar().showMessage(f"Converted {converted} of {len(books)} file(s) to CBZ.", 8000)

    def _target_books(self) -> list[CbzBook]:
        """The selected book(s), or every loaded book if none is
        selected -- same fallback epubredactor's own lookup dialogs
        use, so "look up this one file", "look up these N I selected",
        and "look up everything I loaded" all work without a separate
        mode switch."""
        if self._selected_rows:
            return [self.books[row] for row in self._editable_selected_rows()]
        return [book for book in self.books if not book.needs_conversion]

    def _run_lookup_dialog(self, dialog_class, label: str, factory=None) -> None:
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

        dialog = factory(target_books) if factory else dialog_class(target_books, self)
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
                self._show_book_in_panel(book, page_count_text)
        self._update_status()

    def _resolve_overwrite_conflicts(
        self, target_books: list[CbzBook], metadata_changes: dict[int, dict[str, str]]
    ) -> dict[int, dict[str, str]] | None:
        """Thin wrapper around the shared redactor_common.gui.
        overwrite_review_dialog.resolve_overwrite_conflicts() -- applies
        uniformly no matter which path produced the changes (a lookup,
        bulk edit, or Parse Filename all funnel through this same
        method). This is the standard confirmation step for every
        metadata-writing path that could clobber existing data, not an
        opt-in extra: "we can be sure what is the real data" means
        seeing the actual old/new comparison, not trusting one
        batch-wide Overwrite-All/Keep-Existing choice. See that
        function's own docstring for the skip-when-clean / per-field
        ticked-vs-unticked behavior; kept as a thin per-project wrapper
        (rather than calling the shared function directly at each of
        this file's three call sites) so `_field_label` doesn't need
        threading through each one separately.

        Returns the changes to actually apply (every field whose
        checkbox is still ticked when Apply is clicked), or None if the
        user cancelled outright."""
        return resolve_overwrite_conflicts(self, target_books, metadata_changes, _field_label)

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
        from gui.comicvine_key_dialog import ComicVineKeyDialog

        ComicVineKeyDialog(self).exec()

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
        message = f"{len(self.books)} file(s) loaded, {changed} with unsaved changes"
        unconverted = sum(1 for book in self.books if book.needs_conversion)
        if unconverted:
            message += f", {unconverted} need converting to CBZ"
        self.statusBar().showMessage(message)

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
