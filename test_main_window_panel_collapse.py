"""Regression tests for the side panel's collapse/restore toggle --
"button to collapse left field does not include cover. Pushing the
button afterwards does nothing. leftbar is stuck."

Root cause: gui/metadata_panel.py's cover box sat directly in the
panel's splitter as a plain QGroupBox, not wrapped in a QScrollArea the
way the field groups already are (see _build_fields_scroll_area()). A
QGroupBox's minimumSizeHint() is inflated by its own title text width
(confirmed directly -- "Cover (First Page)" alone forces roughly a
254px floor, and neither setMinimumWidth() nor setMinimumSize() on the
box can override that, since minimumSizeHint() is a separate,
un-overridable computation QSplitter.setSizes() clamps against). Since
a QScrollArea's OWN minimumSizeHint stays small regardless of its
content's, wrapping the cover box in one (gui/metadata_panel.py's
_build_cover_box()) fixes it at the source: the panel's own
minimumSizeHint() must fit within collapsed_width (see
SplitterPaneCollapser.is_collapsed()) for the toggle to ever actually
reach it, rather than silently getting clamped back up to a much wider
floor -- which also explains "does nothing afterwards": once
is_collapsed() incorrectly reports False right after a "successful"
collapse, the next click tries to collapse again instead of
restoring."""

import sys

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow, PANEL_COLLAPSED_WIDTH

_app = QApplication.instance() or QApplication(sys.argv)


def test_panel_minimum_width_fits_within_the_collapsed_target():
    """The actual root-cause invariant: if this ever creeps back above
    PANEL_COLLAPSED_WIDTH + 10 (SplitterPaneCollapser's own "is this
    collapsed" tolerance), the panel can visually shrink partway but
    never actually reach the collapsed target -- exactly this bug."""
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    _app.processEvents()

    assert window.panel.minimumSizeHint().width() <= PANEL_COLLAPSED_WIDTH + 10


def test_toggle_collapses_then_restores():
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    _app.processEvents()

    original_width = window.splitter.sizes()[0]
    assert not window._panel_collapser.is_collapsed()

    window._toggle_panel()
    _app.processEvents()
    assert window._panel_collapser.is_collapsed(), "first click should collapse the panel"

    window._toggle_panel()
    _app.processEvents()
    assert not window._panel_collapser.is_collapsed(), "second click should restore it"
    assert window.splitter.sizes()[0] == original_width


def test_repeated_toggling_never_gets_stuck():
    """Pin down the reported symptom directly: click it several times
    in a row and confirm it keeps alternating, rather than collapsing
    once and then doing nothing on every click after."""
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    _app.processEvents()

    expected = True  # first click should collapse
    for _ in range(4):
        window._toggle_panel()
        _app.processEvents()
        assert window._panel_collapser.is_collapsed() is expected
        expected = not expected


def test_toggle_button_indicator_matches_actual_state():
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    _app.processEvents()

    window._toggle_panel()
    _app.processEvents()
    assert window.panel.collapse_toggle_btn.text() == "▶"  # "restore me" arrow

    window._toggle_panel()
    _app.processEvents()
    assert window.panel.collapse_toggle_btn.text() == "◀"  # "minimize me" arrow
