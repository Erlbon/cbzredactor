"""Tests for the column <-> side-panel field visibility linkage (see
gui/metadata_panel.py's ComicInfoPanel.set_visible_fields() and
gui/main_window.py's MainWindow._sync_panel_visible_fields()).

Covers the three things the user's 2026-09-06 "sanity check" flagged:
(1) hiding a column hides that field's row in the panel; (2) a field
hidden from view still round-trips its data (a lookup/Parse Filename/
apply_to_metadata can still read and write it); (3) every field with
real metadata is representable as a %placeholder% for Rename/Export
and Parse Filename, not just the old curated 7-field subset."""

import sys

from PyQt6.QtWidgets import QApplication

from core.comicinfo import ComicInfoMetadata
from gui.main_window import FILENAME_PLACEHOLDERS, _FIELD_LABELS
from gui.metadata_panel import ComicInfoPanel

_app = QApplication.instance() or QApplication(sys.argv)


def test_hiding_a_field_hides_its_row_but_not_others():
    # isHidden() (the widget's own explicit flag) rather than
    # isVisible() (which also depends on the panel actually being
    # shown on screen, which it never is in this test) -- see
    # https://doc.qt.io/qt-6/qwidget.html#isHidden.
    panel = ComicInfoPanel()
    all_attrs = set(_FIELD_LABELS)
    panel.set_visible_fields(all_attrs - {"writer"})

    assert panel._row_widgets["writer"].isHidden() is True
    assert panel._row_widgets["series"].isHidden() is False


def test_hidden_field_still_loads_and_applies_data():
    """The core data-safety guarantee: a field hidden from view is not
    a field excluded from load_metadata()/apply_to_metadata() -- it
    must still round-trip whatever a lookup or Parse Filename writes
    into it, exactly as if it were shown."""
    panel = ComicInfoPanel()
    panel.set_visible_fields(set(_FIELD_LABELS) - {"writer"})

    metadata = ComicInfoMetadata(writer="Alan Moore")
    panel.load_metadata(metadata, None, "")
    assert panel._line_edits["writer"].text() == "Alan Moore"

    panel._line_edits["writer"].setText("Neil Gaiman")
    out = ComicInfoMetadata()
    panel.apply_to_metadata(out)
    assert out.writer == "Neil Gaiman"


def test_showing_all_fields_restores_every_row():
    panel = ComicInfoPanel()
    panel.set_visible_fields(set(_FIELD_LABELS) - {"writer", "genre"})
    panel.set_visible_fields(set(_FIELD_LABELS))

    for attr, widget in panel._row_widgets.items():
        assert not widget.isHidden(), f"{attr} should be visible again"


def test_group_box_hides_when_every_field_in_it_is_hidden():
    """Summary/Notes/Review are single-field 'groups' -- their own
    QGroupBox IS the row widget, so hiding the field must hide the
    whole box (title included), not leave an empty titled box behind."""
    panel = ComicInfoPanel()
    panel.set_visible_fields(set(_FIELD_LABELS) - {"summary"})
    assert panel._row_widgets["summary"].isHidden() is True


def test_filename_placeholders_cover_every_editable_field():
    """Used to be a curated 7-field subset (series/number/title/volume/
    year/publisher/writer) -- now every field the panel can edit must
    be offered as a %placeholder%, so a field with real metadata (e.g.
    Genre, Story Arc) can actually be exported into a filename."""
    placeholder_keys = {key for key, _label in FILENAME_PLACEHOLDERS}
    assert placeholder_keys == set(_FIELD_LABELS)
    # Spot-check a field that was NOT in the old curated subset.
    assert "genre" in placeholder_keys
    assert "story_arc" in placeholder_keys
