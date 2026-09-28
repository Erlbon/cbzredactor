"""
gui/credit_pages_dialogs.py

The three windows for scanner credit pages (core/credit_pages.py):

- CreditPagesDialog: one book's first/last pages as thumbnails, each
  with a "credit page" checkbox. Ticking a page and clicking Remove
  learns it (so the same tag page is recognised in every other book
  from then on -- like epubredactor's Junk Cover flag) and removes it.
- RemoveCreditPagesDialog: the batch review -- every known credit page
  found in the chosen books, thumbnails side by side, a checkbox each.
- KnownCreditPagesDialog: Settings > Known Credit Pages... -- what's been
  learned, with Forget for a mistake.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.credit_pages import KnownCreditPages, PageCandidate

THUMB_HEIGHT = 144


def _thumbnail_label(png: bytes, text_if_missing: str = "(no preview)") -> QLabel:
    label = QLabel()
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setMinimumHeight(THUMB_HEIGHT)
    pixmap = QPixmap()
    if png and pixmap.loadFromData(png):
        label.setPixmap(pixmap)
    else:
        label.setText(text_if_missing)
    return label


class CreditPagesDialog(QDialog):
    """One book: tick the credit pages, then Remove. Pre-ticks pages
    matching a known credit page; hints are shown but never pre-ticked."""

    def __init__(self, book_label: str, candidates: list[PageCandidate], known: KnownCreditPages, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Credit Pages -- {book_label}")
        self.candidates = candidates
        self._checks: dict[int, QCheckBox] = {}
        outer = QVBoxLayout(self)

        intro = QLabel(
            "The first and last pages of this book. Tick the scanner credit pages "
            "and click Remove: they're removed from the file (the original goes to "
            "the Recycle Bin) and remembered, so the same page is found in every "
            "other book from now on."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        for column, candidate in enumerate(candidates):
            cell = QVBoxLayout()
            cell.addWidget(_thumbnail_label(candidate.thumbnail))
            check = QCheckBox(f"Page {candidate.index + 1}")
            check.setToolTip(candidate.name)
            known_match = candidate.hash is not None and known.match(candidate.hash) is not None
            check.setChecked(known_match)
            self._checks[candidate.index] = check
            cell.addWidget(check)
            notes = []
            if known_match:
                notes.append("<b>Known credit page</b>")
            if candidate.plain:
                notes.append("Blank/plain -- can be removed, but won't be remembered")
            notes += candidate.hints
            note = QLabel("<br>".join(notes))
            note.setWordWrap(True)
            note.setTextFormat(Qt.TextFormat.RichText)
            note.setStyleSheet("font-size: 11px;")
            cell.addWidget(note)
            cell.addStretch(1)
            grid.addLayout(cell, 0, column)
        scroll = QScrollArea()
        scroll.setWidget(grid_host)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.remove_button = buttons.addButton("Remove Ticked Pages", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(min(170 * max(len(candidates), 3) + 60, 1200), 420)

    def ticked(self) -> list[PageCandidate]:
        return [c for c in self.candidates if self._checks[c.index].isChecked()]


class RemoveCreditPagesDialog(QDialog):
    """Batch review: one row per found credit page -- the book, the page,
    its thumbnail, and an Apply checkbox (ticked)."""

    def __init__(self, found: list[tuple[object, str, PageCandidate]], skipped_note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove Credit Pages")
        self.found = found
        outer = QVBoxLayout(self)
        books = len({id(book) for book, _label, _c in found})
        intro = QLabel(
            f"Found {len(found)} known credit page(s) in {books} file(s). Untick any that "
            "aren't credit pages, then Remove. Each changed file's original goes to the "
            "Recycle Bin."
            + (f"\n\n{skipped_note}" if skipped_note else "")
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.table = QTableWidget(len(found), 3)
        self.table.setHorizontalHeaderLabels(["Remove", "Page", "File"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(THUMB_HEIGHT + 8)
        self._checks: list[QCheckBox] = []
        for row, (_book, label, candidate) in enumerate(found):
            check = QCheckBox()
            check.setChecked(True)
            self._checks.append(check)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(8, 0, 0, 0)
            layout.addWidget(check)
            self.table.setCellWidget(row, 0, holder)
            self.table.setCellWidget(row, 1, _thumbnail_label(candidate.thumbnail))
            self.table.setItem(row, 2, QTableWidgetItem(f"{label}\npage {candidate.index + 1} ({candidate.name})"))
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 120)
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Remove Ticked Pages", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(760, 560)

    def ticked(self) -> list[tuple[object, str, PageCandidate]]:
        return [entry for entry, check in zip(self.found, self._checks) if check.isChecked()]


class KnownCreditPagesDialog(QDialog):
    """Settings > Known Credit Pages...: what has been learned, with Forget."""

    def __init__(self, known: KnownCreditPages, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Known Credit Pages")
        self.known = known
        outer = QVBoxLayout(self)
        intro = QLabel(
            "Pages learned as scanner credit pages (right-click a file > Credit Pages...). "
            "Any page that looks like one of these is found in every file. Forget one "
            "that was marked by mistake."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QPixmap(96, THUMB_HEIGHT).size())
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        outer.addWidget(self.list, 1)
        self._populate()

        row = QHBoxLayout()
        forget = QPushButton("Forget Selected")
        forget.clicked.connect(self._forget)
        row.addWidget(forget)
        row.addStretch(1)
        outer.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(640, 480)

    def _populate(self) -> None:
        self.list.clear()
        for page in self.known.pages:
            pixmap = QPixmap()
            pixmap.loadFromData(page.thumbnail)
            item = QListWidgetItem(QIcon(pixmap), f"{page.source}\n{page.added}")
            item.setData(Qt.ItemDataRole.UserRole, page.hash)
            self.list.addItem(item)

    def _forget(self) -> None:
        for item in self.list.selectedItems():
            self.known.forget(item.data(Qt.ItemDataRole.UserRole))
        self._populate()
