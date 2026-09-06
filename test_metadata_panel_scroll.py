"""Tests for the scroll-safe Classification combo/spin boxes in
gui/metadata_panel.py (_ScrollSafeComboBox, _ScrollSafeDoubleSpinBox).

Without this fix, using the mouse wheel to scroll the field list (see
ComicInfoPanel._build_fields_scroll_area()) silently changes whichever
combo/spin box the cursor happens to be hovering over instead of
scrolling past it -- a well-known Qt gotcha for any QScrollArea
containing one of these widgets, and the actual cause behind "no
scrolling to get different content" in the field panel: the QScrollArea
itself was never broken (see test_panel_column_visibility.py's sibling
investigation), the wheel events just never reached it."""

import sys

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

from gui.metadata_panel import ComicInfoPanel, _ScrollSafeComboBox, _ScrollSafeDoubleSpinBox

_app = QApplication.instance() or QApplication(sys.argv)


def _make_wheel_event() -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(0, 120), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )


def test_combo_ignores_wheel_when_not_focused():
    combo = _ScrollSafeComboBox()
    combo.addItems(["A", "B", "C"])
    combo.clearFocus()

    event = _make_wheel_event()
    combo.wheelEvent(event)

    assert event.isAccepted() is False  # bubbles up to the parent QScrollArea instead


def test_combo_handles_wheel_normally_when_focused():
    combo = _ScrollSafeComboBox()
    combo.addItems(["A", "B", "C"])
    combo.show()
    combo.setFocus()

    event = _make_wheel_event()
    combo.wheelEvent(event)

    assert event.isAccepted() is True  # deliberately scrolling THIS widget, not the panel


def test_spinbox_ignores_wheel_when_not_focused():
    spin = _ScrollSafeDoubleSpinBox()
    spin.setRange(0.0, 5.0)
    spin.clearFocus()

    event = _make_wheel_event()
    spin.wheelEvent(event)

    assert event.isAccepted() is False


def test_spinbox_handles_wheel_normally_when_focused():
    spin = _ScrollSafeDoubleSpinBox()
    spin.setRange(0.0, 5.0)
    spin.show()
    spin.setFocus()

    event = _make_wheel_event()
    spin.wheelEvent(event)

    assert event.isAccepted() is True


def test_panel_classification_widgets_are_scroll_safe():
    """Regression guard: a future edit shouldn't quietly swap these back
    to plain QComboBox/QDoubleSpinBox."""
    panel = ComicInfoPanel()
    assert isinstance(panel.age_rating_combo, _ScrollSafeComboBox)
    assert isinstance(panel.manga_combo, _ScrollSafeComboBox)
    assert isinstance(panel.black_and_white_combo, _ScrollSafeComboBox)
    assert isinstance(panel.community_rating_spin, _ScrollSafeDoubleSpinBox)
