"""File > Preview...: a separate, non-modal, READ-ONLY page reader for one comic.

Check a comic in place -- are the pages in order, is the last page intact, does
the cover match -- without extracting anything. Only the page on screen (plus
the next one, as a prefetch) is read from the archive, one entry at a time via
CbzBook.read_page_bytes(), and decoded on worker threads through
redactor_common's AsyncPreviewLoader / decode_scaled, so the window never
freezes on a 3000x4500 px scan. Nothing is ever written to the archive, and
the window remembers nothing (no settings).
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
)
from redactor_common.gui.async_preview import AsyncPreviewLoader

from core.cbz_file import CbzBook
from core.page_cache import LruCache
from core.page_reader import PageNavigator

CACHE_SIZE = 4  # decoded pages kept, so stepping back is instant
FIT_WIDTH_TALL = 20000  # decode height limit in Fit width mode (effectively "no limit")


class PageReaderDialog(QDialog):
    def __init__(self, book: CbzBook, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._book = book
        self.nav = PageNavigator([] if book.load_error else book.page_names)
        self.setWindowTitle(f"Preview - {os.path.basename(book.path)}")
        self.resize(900, 1000)

        self._image: QImage | None = None
        self._image_target = QSize()
        self._message = ""
        self._cache: LruCache = LruCache(CACHE_SIZE)  # index -> (QImage, target QSize)

        self._loader = AsyncPreviewLoader(debounce_ms=0, parent=self, max_threads=1)
        self._loader.image_ready.connect(self._on_image_ready)
        self._prefetch = AsyncPreviewLoader(debounce_ms=0, parent=self, max_threads=1)
        self._prefetch.image_ready.connect(self._on_prefetched)

        self.name_label = QLabel()
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(110)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setWordWrap(True)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.image_label)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrow keys belong to the reader
        self.scroll.setStyleSheet("QScrollArea { background: #202020; border: none; } QLabel { color: #ddd; }")

        self.first_button = QPushButton("|<")
        self.prev_button = QPushButton("<")
        self.next_button = QPushButton(">")
        self.last_button = QPushButton(">|")
        for button, tip, slot in (
            (self.first_button, "First page (Home)", self.go_first),
            (self.prev_button, "Previous page (Left)", self.go_previous),
            (self.next_button, "Next page (Right)", self.go_next),
            (self.last_button, "Last page (End)", self.go_last),
        ):
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
        self.fit_width = QCheckBox("Fit width")
        self.fit_width.setToolTip("Scale to the window width and scroll down the page, instead of fitting the whole page")
        self.fit_width.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fit_width.toggled.connect(lambda _on: self._refit(reload_if_needed=True))

        bar = QHBoxLayout()
        for widget in (self.first_button, self.prev_button, self.page_label, self.next_button, self.last_button):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.fit_width)
        layout = QVBoxLayout(self)
        layout.addWidget(self.name_label)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(bar)

        self._show_current()

    # ------------------------------------------------------------ navigation

    def go_first(self) -> None:
        self._moved(self.nav.first())

    def go_previous(self) -> None:
        self._moved(self.nav.previous())

    def go_next(self) -> None:
        self._moved(self.nav.next())

    def go_last(self) -> None:
        self._moved(self.nav.last())

    def _moved(self, changed: bool) -> None:
        if changed:
            self._show_current()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.go_previous()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.go_next()
        elif key == Qt.Key.Key_Home:
            self.go_first()
        elif key == Qt.Key.Key_End:
            self.go_last()
        else:
            super().keyPressEvent(event)

    # --------------------------------------------------------------- showing

    def _viewport_size(self) -> QSize:
        return self.scroll.viewport().size()

    def _target(self) -> QSize:
        """What the current page should be decoded to fit."""
        view = self._viewport_size()
        view = QSize(max(view.width(), 50), max(view.height(), 50))
        if self.fit_width.isChecked():
            return QSize(view.width(), FIT_WIDTH_TALL)
        return view

    def _update_chrome(self) -> None:
        self.page_label.setText(self.nav.label())
        self.name_label.setText(self.nav.current_name)
        self.first_button.setEnabled(not self.nav.at_first)
        self.prev_button.setEnabled(not self.nav.at_first)
        self.next_button.setEnabled(not self.nav.at_last)
        self.last_button.setEnabled(not self.nav.at_last)

    def _show_message(self, text: str) -> None:
        self._image = None
        self._message = text
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(text)
        self.image_label.resize(self._viewport_size())

    def _show_current(self) -> None:
        self._update_chrome()
        if not self.nav.has_pages:
            self._loader.cancel()
            self._prefetch.cancel()
            self._show_message(
                self._book.load_error and f"This file could not be read:\n{self._book.load_error}"
                or "This archive contains no images."
            )
            return
        index = self.nav.index
        target = self._target()
        cached = self._cache.get(index)
        if cached is not None and self._covers(cached[1], target):
            self._loader.cancel()
            self._image, self._image_target = cached
            self._message = ""
            self._refit()
            self._prefetch_next()
            return
        self._message = "Loading..."
        self._image = None
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Loading...")
        self.image_label.resize(self._viewport_size())
        book = self._book
        self._loader.request((index, target), lambda: book.read_page_bytes(index), target)

    @staticmethod
    def _covers(have: QSize, want: QSize) -> bool:
        """A page decoded for `have` is sharp enough for `want`."""
        return have.width() >= want.width() and have.height() >= want.height()

    def _on_image_ready(self, token, image: QImage) -> None:
        index, target = token
        if index != self.nav.index:
            return
        if image.isNull():
            name = self.nav.names[index]
            self._show_message(f"Page {index + 1} ({name}) could not be read: it is damaged or not an image.")
            self._prefetch_next()
            return
        self._cache.put(index, (image, target))
        self._image, self._image_target = image, target
        self._message = ""
        self._refit()
        self._prefetch_next()

    def _prefetch_next(self) -> None:
        nxt = self.nav.index + 1
        if nxt >= self.nav.count or nxt in self._cache:
            return
        book, target = self._book, self._target()
        self._prefetch.request((nxt, target), lambda: book.read_page_bytes(nxt), target)

    def _on_prefetched(self, token, image: QImage) -> None:
        index, target = token
        if not image.isNull():
            self._cache.put(index, (image, target))

    def _refit(self, reload_if_needed: bool = False) -> None:
        """Scales the current image to the window (called on every resize)."""
        if self._image is None or self._image.isNull():
            if self._message:
                self.image_label.resize(self._viewport_size())
            return
        view = self._viewport_size()
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn if self.fit_width.isChecked() else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        view = self._viewport_size()
        if self.fit_width.isChecked():
            size = QSize(view.width(), max(1, round(view.width() * self._image.height() / max(1, self._image.width()))))
        else:
            size = self._image.size().scaled(view, Qt.AspectRatioMode.KeepAspectRatio)
        self.image_label.setText("")
        self.image_label.setPixmap(QPixmap.fromImage(self._image.scaled(
            size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        self.image_label.resize(max(size.width(), view.width()), size.height() if self.fit_width.isChecked() else view.height())
        # Grown window (or mode switch): the stored decode may now be too small -- fetch a sharper one.
        if self.nav.has_pages and not self._covers(self._image_target, self._target()) and (
            reload_if_needed or self._image.size().width() < size.width() or self._image.size().height() < size.height()
        ):
            book, index, target = self._book, self.nav.index, self._target()
            self._loader.request((index, target), lambda: book.read_page_bytes(index), target)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # The first page was requested before the window had its final size.
        self._refit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._loader.cancel()
        self._prefetch.cancel()
        super().closeEvent(event)
