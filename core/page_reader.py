"""Page navigation for the read-only Preview window (gui/page_reader_dialog.py).

Pure Python, no Qt: the list of page names in reading order plus a current
position that can never leave the list. Nothing here touches the archive.
"""

from __future__ import annotations

from typing import Sequence


def clamp_index(index: int, count: int) -> int:
    """`index` limited to 0..count-1; 0 for an empty list."""
    if count <= 0:
        return 0
    return max(0, min(index, count - 1))


class PageNavigator:
    """A position in a page list. `names` is taken as given (CbzBook.page_names
    is already in reading order) and copied, so a later change to the book
    never moves the Preview under the reader."""

    def __init__(self, names: Sequence[str], start: int = 0):
        self.names: list[str] = list(names)
        self.index = clamp_index(start, len(self.names))

    @property
    def count(self) -> int:
        return len(self.names)

    @property
    def has_pages(self) -> bool:
        return bool(self.names)

    @property
    def current_name(self) -> str:
        return self.names[self.index] if self.names else ""

    @property
    def at_first(self) -> bool:
        return self.index <= 0

    @property
    def at_last(self) -> bool:
        return self.index >= self.count - 1

    def go_to(self, index: int) -> bool:
        """Moves to `index` (clamped); True if the position changed."""
        new = clamp_index(index, self.count)
        changed = new != self.index
        self.index = new
        return changed

    def first(self) -> bool:
        return self.go_to(0)

    def last(self) -> bool:
        return self.go_to(self.count - 1)

    def previous(self) -> bool:
        return self.go_to(self.index - 1)

    def next(self) -> bool:
        return self.go_to(self.index + 1)

    def label(self) -> str:
        """'Page 3 / 24', or 'No pages' for an archive without images."""
        if not self.names:
            return "No pages"
        return f"Page {self.index + 1} / {self.count}"
