"""
gui/convert_resize_dialog.py

"Resize pages in the same step?" -- the one question a batch of CBR/CBT/CB7
conversions asks before it starts (on load, the table's Convert, Convert from
disk, the Collection report's Convert). Yes means the pages are shrunk while
the archive is packed (core/foreign_archive_convert.py: extract, resize each
page, pack, one check) instead of converting and then rewriting the whole file
a second time.

It shows the saved Resize defaults (the same box the Resize Images dialog has)
so they can be changed on the spot, and a "Remember my choice" box that turns
the question into a setting (Tools > Preferences > Conversion), so the
convert-on-load flow does not nag forever. Redact never shows this: it follows
the saved setting.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.image_resize import ResizeOptions
from gui.resize_dialog import ResizeOptionsForm


class ConvertResizeDialog(QDialog):
    def __init__(self, file_count: int, options: ResizeOptions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resize pages in the same step?")
        self.setMinimumWidth(440)
        self.explicit = False  # a button was clicked (closing the window with X is "not this time")
        self.resize_pages = False

        outer = QVBoxLayout(self)
        intro = QLabel(
            f"{file_count} file(s) are about to be converted to CBZ.\n\n"
            "Shrink their pages in the same step? The pages are resized as the new CBZ is packed, "
            "in one pass, so it costs far less than converting first and resizing afterwards. "
            "A page already within the limits is left as it is; double-page spreads get twice the width."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.form = ResizeOptionsForm(options, title="Resize with")
        outer.addWidget(self.form)

        self.remember_check = QCheckBox("Remember my choice (change it in Tools > Preferences > Conversion)")
        self.remember_check.setToolTip(
            "Remembers Yes or No for every later conversion, so this question is not asked again. "
            "Closing this window with the X is never remembered."
        )
        outer.addWidget(self.remember_check)

        buttons = QDialogButtonBox()
        self.yes_button = QPushButton("Resize While Converting")
        self.no_button = QPushButton("Convert Without Resizing")
        buttons.addButton(self.yes_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.no_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.yes_button.clicked.connect(self._yes)
        self.no_button.clicked.connect(self._no)
        self.no_button.setDefault(True)
        outer.addWidget(buttons)

    def _yes(self) -> None:
        self.explicit, self.resize_pages = True, True
        self.accept()

    def _no(self) -> None:
        self.explicit, self.resize_pages = True, False
        self.reject()

    def options(self) -> ResizeOptions:
        return self.form.options()

    def remember(self) -> bool:
        """Ticked, and a button (not the window's X) closed the dialog."""
        return self.explicit and self.remember_check.isChecked()
