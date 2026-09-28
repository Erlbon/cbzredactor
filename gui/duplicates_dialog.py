"""
gui/duplicates_dialog.py

Operations > Find Duplicates... -- the review window for
core/duplicates.py's groups: one block per group, each copy with its
cover and the facts that decide which to keep (page resolution, pages,
how much ComicInfo is filled in, file size). The suggested copy to keep
is marked; the others start ticked for removal (to the Recycle Bin).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.duplicates import BookFacts, DuplicateGroup

COVER_HEIGHT = 120


def _size_text(size: int) -> str:
    return f"{size / 1024 ** 2:.1f} MB" if size >= 1024 ** 2 else f"{size / 1024:.0f} KB"


class DuplicatesDialog(QDialog):
    def __init__(
        self,
        groups: list[DuplicateGroup],
        facts: list[BookFacts],
        labels: list[str],
        covers: list[bytes | None],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Duplicates")
        self._checks: list[tuple[int, QCheckBox]] = []
        outer = QVBoxLayout(self)

        copies = sum(len(g.members) for g in groups)
        intro = QLabel(
            f"{len(groups)} comic(s) found more than once ({copies} files). The suggested "
            "copy to keep is marked -- highest page resolution, then most pages, then the "
            "most filled-in ComicInfo, then the biggest file. Ticked copies go to the "
            "Recycle Bin and leave the list."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        host = QWidget()
        host_layout = QVBoxLayout(host)
        for number, group in enumerate(groups, 1):
            if group.by_link:
                kind = "Same Comic Vine/GCD issue"
            elif group.different_cover:
                kind = "Same comic, different cover (a variant?)"
            else:
                kind = "Same comic"
            box = QGroupBox(f"{number}. {kind}")
            grid = QGridLayout(box)
            for column, index in enumerate(group.members):
                f = facts[index]
                cover = QLabel()
                cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pixmap = QPixmap()
                if covers[index] and pixmap.loadFromData(covers[index]):
                    cover.setPixmap(pixmap.scaledToHeight(COVER_HEIGHT, Qt.TransformationMode.SmoothTransformation))
                else:
                    cover.setText("(no cover)")
                grid.addWidget(cover, 0, column)
                keep = index == group.keep
                details = QLabel(
                    ("<b>Keep (best)</b><br>" if keep else "")
                    + f"{labels[index]}<br>"
                    + f"{f.width}px wide · {f.pages} pages<br>"
                    + f"{f.metadata_fields} ComicInfo fields · {_size_text(f.file_size)}"
                )
                details.setWordWrap(True)
                details.setTextFormat(Qt.TextFormat.RichText)
                details.setMaximumWidth(260)
                grid.addWidget(details, 1, column)
                check = QCheckBox("Remove")
                check.setChecked(not keep)
                grid.addWidget(check, 2, column)
                self._checks.append((index, check))
            host_layout.addWidget(box)
        host_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Move Ticked to Recycle Bin", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.resize(900, 640)

    def ticked(self) -> list[int]:
        return [index for index, check in self._checks if check.isChecked()]
