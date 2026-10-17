"""Drag-to-reorder of the side-panel fields (redactor_common's field_order), in the real MainWindow offscreen:
a real mouse drag on a grip moves the field (within its group box), the order is saved by attribute name, a second
window restores it, Export/Import carries it, Reset restores the default, and editing after a reorder still writes
the right values to the right fields (the dangerous part: values stay attached to the field, not the position)."""

import sys
import zipfile

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFormLayout, QMessageBox

from core.cbz_file import CbzBook
from gui import app_settings
from gui.main_window import MainWindow
from gui.settings_adapter import CbzSettingsAdapter
from redactor_common.core import settings_bundle as sb

_app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def temp_ini(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "settings.ini"))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def _cbz(path, title="T", series="S") -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", f"<ComicInfo><Title>{title}</Title><Series>{series}</Series></ComicInfo>")
        zf.writestr("001.jpg", b"x")
    return str(path)


def _window(tmp_path, names=("A.cbz",)):
    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    win._load_paths([_cbz(tmp_path / n) for n in names])
    _app.processEvents()
    return win


def _controller(window, attr):
    for c in window.panel._field_orders:
        if attr in c.visible_keys():
            return c
    raise AssertionError(attr)


def _shown(window, attr):
    """The attribute names of the group that holds `attr`, top to bottom as laid out."""
    _app.processEvents()  # geometry follows a layout change on the next event pass
    c = _controller(window, attr)
    return c.visible_keys()


def _drag(window, attr, to_attr, below=False):
    c = _controller(window, attr)
    grip = c.grip(attr)
    assert grip is not None and grip.isVisible()
    target = window.panel._row_widgets[to_attr]
    target_global = target.mapToGlobal(QPoint(5, target.height() - 1 if below else 1))
    start = QPoint(grip.width() // 2, grip.height() // 2)
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for point in (grip.mapToGlobal(start) + QPoint(0, 12), target_global):
        QApplication.sendEvent(grip, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(grip.mapFromGlobal(point)), QPointF(point),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, grip.mapFromGlobal(target_global))
    _app.processEvents()


def _layout_order(window, attr):
    form = window.panel._row_widgets[attr].parentWidget().layout()
    assert isinstance(form, QFormLayout)
    attrs_by_widget = {id(w): a for a, w in window.panel._row_widgets.items()}
    return [attrs_by_widget[id(form.itemAt(i, QFormLayout.ItemRole.FieldRole).widget())] for i in range(form.rowCount())]


def test_drag_moves_the_field_and_saves_by_name(tmp_path):
    window = _window(tmp_path)
    before = _shown(window, "title")
    assert before[:3] == ["title", "series", "number"]
    _drag(window, "number", "title")
    after = _shown(window, "title")
    assert after[:3] == ["number", "title", "series"]
    assert [a for a in _layout_order(window, "title") if a in after] == after
    assert app_settings.load_panel_field_order()[:3] == ["number", "title", "series"]
    window.close()


def test_a_second_window_restores_the_order_and_reset_restores_the_default(tmp_path):
    window = _window(tmp_path)
    default = _shown(window, "title")
    _drag(window, "number", "title")
    window.close()
    second = _window(tmp_path)
    assert _shown(second, "title")[:3] == ["number", "title", "series"]
    second.panel.reset_field_order()
    assert _shown(second, "title") == default and app_settings.load_panel_field_order() == []
    second.close()


def test_order_travels_through_export_import(tmp_path):
    window = _window(tmp_path)
    _drag(window, "number", "title")
    wanted = _shown(window, "title")
    adapter = CbzSettingsAdapter("x")
    keys = {s.key for s in adapter.sections() if s.portable}
    bundle = sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, keys)), "cbzredactor")
    window.panel.reset_field_order()
    assert _shown(window, "title") != wanted
    sb.apply_bundle(adapter, bundle, keys)
    window._on_settings_imported()
    assert _shown(window, "title") == wanted
    window.close()


def test_edit_after_a_reorder_writes_the_right_values_to_the_right_fields(tmp_path):
    window = _window(tmp_path, ("A.cbz", "B.cbz"))
    _drag(window, "number", "title")
    _drag(window, "series", "title")
    assert _shown(window, "title")[:3] == ["number", "series", "title"]
    for row in (0, 1):
        window.table.selectRow(row)
        _app.processEvents()
        window.panel._line_edits["title"].setText(f"New title {row}")
        window.panel._line_edits["series"].setText(f"New series {row}")
        window.panel._line_edits["number"].setText(str(row + 7))
        _app.processEvents()
    window.save_all_changed()
    for row, name in enumerate(("A.cbz", "B.cbz")):
        path = window.books[row].path
        md = CbzBook(path).metadata
        assert md.title == f"New title {row}" and md.series == f"New series {row}" and md.number == str(row + 7), name
    for book in window.books:
        book.dirty = False
    window.close()
