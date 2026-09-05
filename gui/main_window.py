"""
gui/main_window.py

The main window: a table of loaded CBZ files on the left, and the
collapsible metadata side panel (gui/metadata_panel.py, cover
thumbnail + ComicInfo.xml form) on the right -- same 2-pane layout
convention as the sibling Redactor tools, built on
redactor_common.gui.collapsible_splitter.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
)

from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.progress import run_with_progress
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

from core.cbr_convert import CbrConversionError, convert_cbr_to_cbz
from core.cbz_file import CbzBook, CbzError
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui.metadata_panel import ComicInfoPanel

COLUMNS = ["Filename", "Title", "Series", "Number", "Pages", "Status"]
LOAD_PROGRESS_THRESHOLD = 3
SAVE_PROGRESS_THRESHOLD = 3


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
        self._current_row = -1

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.panel = ComicInfoPanel()
        self.panel.set_enabled(False)
        self.panel.fieldsChanged.connect(self._on_fields_changed)
        self.panel.collapseToggleRequested.connect(self._toggle_panel)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.panel)
        self.splitter.setSizes([760, 340])
        self._panel_collapser = SplitterPaneCollapser(
            self.splitter, pane_index=1, collapsed_width=32, default_width=340
        )
        self.setCentralWidget(self.splitter)

        self.setStatusBar(QStatusBar())
        self._build_menu()
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
                MenuAction("exit", "E&xit", self.close, shortcut="Ctrl+Q"),
            ],
            "Import": [
                MenuAction("convert_cbr", "Convert &CBR to CBZ...", self.convert_cbr_dialog),
            ],
            "Operations": [
                MenuAction("save_all", "Save &All Changed", self.save_all_changed, shortcut="Ctrl+Shift+A"),
            ],
            "Settings": [],
            "Help": [
                MenuAction("about", f"&About {APP_NAME}", self.open_about_dialog),
                MenuAction("changelog", "View &Changelog", self.open_changelog_dialog),
                MenuAction("credits", "View C&redits", self.open_credits_dialog),
            ],
        }
        self.actions_ = build_menu_bar(self, specs)

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
        values = [
            os.path.basename(book.path),
            book.metadata.title,
            book.metadata.series,
            book.metadata.number,
            str(book.actual_page_count),
            status,
        ]
        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(value))

    # ------------------------------------------------------------------
    # Selection / editing
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        self._commit_current_edits()
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._current_row = -1
            self.panel.set_enabled(False)
            return
        self._current_row = rows[0].row()
        book = self.books[self._current_row]
        self.panel.set_enabled(True)
        page_count_text = f"{book.actual_page_count} page(s)"
        if book.page_count_mismatch:
            page_count_text += f" -- ComicInfo.xml says {book.metadata.page_count}, will be corrected on save"
        self.panel.load_metadata(book.metadata, book.read_first_page_bytes(), page_count_text)

    def _commit_current_edits(self) -> None:
        """Writes the panel's current widget values back into whichever
        book was selected *before* the selection changes -- otherwise
        an in-progress edit is silently discarded the instant the user
        clicks a different row."""
        if self._current_row < 0 or self._current_row >= len(self.books):
            return
        book = self.books[self._current_row]
        self.panel.apply_to_metadata(book.metadata)

    def _on_fields_changed(self) -> None:
        if self._current_row < 0:
            return
        self.books[self._current_row].dirty = True
        self._refresh_table_row(self._current_row, self.books[self._current_row])

    def _toggle_panel(self) -> None:
        self._panel_collapser.toggle()
        self.panel.collapse_toggle_btn.set_collapsed(self._panel_collapser.is_collapsed())

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save_current(self) -> None:
        self._commit_current_edits()
        if self._current_row < 0:
            return
        self._save_book(self._current_row)

    def save_current_as(self) -> None:
        self._commit_current_edits()
        if self._current_row < 0:
            return
        book = self.books[self._current_row]
        path, _ = QFileDialog.getSaveFileName(self, "Save As", book.path, "Comic Book ZIP (*.cbz)")
        if path:
            self._save_book(self._current_row, output_path=path)

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
        event.accept()
