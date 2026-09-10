"""
gui/overwrite_review_dialog.py

Per-file, per-field review of an incoming batch of metadata changes
before they're applied -- the standard confirmation step for every
metadata-writing path that could overwrite existing data (a lookup
apply, Parse Filename, and any future local-database import). Replaces
the old all-or-nothing "Overwrite All / Keep Existing / Cancel" choice
(see git history for MainWindow._resolve_overwrite_conflicts()'s prior
QMessageBox-based version) with a real side-by-side comparison and a
checkbox per (file, field) pair: "There could be instances where we
want some fields, but not all."

Built on redactor_common.gui.preview_table.PreviewTableController,
grouped by File -- the exact same "before/after + per-row Apply
checkbox" shape already used for Search/Replace and Case Conversion,
just grouped since this reviews several fields per file at once
instead of one field across many files (that grouping support, and a
row's own default-checked state, were promoted there specifically for
this dialog -- see that module's own 2026-09-10 note).

A field whose existing value is blank (nothing to lose) starts ticked;
a field that would actually overwrite a different, non-blank existing
value starts UNTICKED, requiring a deliberate opt-in -- "so we can be
sure what is the real data" rather than trusting one batch-wide
decision. Every field the incoming change touches is listed, not just
the ones that conflict, once anything in the batch conflicts at all --
full visibility into what's about to happen to a file, not a partial
view. A batch with zero conflicts skips this dialog entirely (see
MainWindow._resolve_overwrite_conflicts()); there's nothing to review
when nothing would be overwritten.
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTableWidget, QVBoxLayout

from redactor_common.gui.preview_table import PreviewRow, PreviewTableController


class OverwriteReviewDialog(QDialog):
    def __init__(self, rows: list[PreviewRow], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review Changes")
        self.resize(900, 500)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Review every field this would change before applying. A field "
            "that would overwrite an existing value starts unticked -- tick "
            "it to accept the new value, or leave it to keep what's already "
            "there. A field that's currently blank starts ticked, since "
            "there's nothing to lose."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        table = QTableWidget()
        self._controller = PreviewTableController(table, item_column_label="Field", group_column_label="File")
        self._controller.set_rows(rows)
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accepted_changes(self) -> dict:
        """(file_index, attr) -> new value, for every row whose checkbox
        is still ticked."""
        return self._controller.accepted_changes()


def build_overwrite_review_rows(
    target_books: list, metadata_changes: dict[int, dict[str, str]], field_label_fn
) -> list[PreviewRow]:
    """Turns a plain {book_index: {attr: value}} change set into the
    (file, field) rows this dialog shows -- one row per field, grouped
    by the file's own basename, with the safe-fill-vs-real-overwrite
    default-checked split described in this module's own docstring.
    Pulled out as a standalone function (rather than inlined in
    MainWindow) so it's testable without constructing the dialog/a
    real QApplication."""
    rows: list[PreviewRow] = []
    for index, fields in metadata_changes.items():
        book = target_books[index]
        display_name = os.path.basename(book.path)
        for attr, new_value in fields.items():
            current_value = (getattr(book.metadata, attr, "") or "").strip()
            rows.append(
                PreviewRow(
                    item_index=(index, attr),
                    display_name=field_label_fn(attr),
                    old_value=current_value,
                    new_value=new_value,
                    group=display_name,
                    default_checked=not current_value or current_value == new_value,
                )
            )
    return rows
