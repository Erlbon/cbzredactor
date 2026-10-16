"""2026-10-16 independent review, 'shared progress dialogs and threading', as it shows in cbzredactor: File > Open Folder
runs a slow scan under run_busy. Before, the window stayed clickable for ~4 s until the progress dialog showed (a second
Open Folder started a nested scan); now the window is disabled for the whole call and enabled again afterwards."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

_app = QApplication.instance() or QApplication([])


def test_a_second_open_folder_during_the_scan_is_blocked_and_the_window_comes_back(tmp_path, monkeypatch):
    import gui.main_window as mw

    window = mw.MainWindow()
    scans, during = [], []

    def slow_scan(folder, recursive):
        scans.append(1)
        time.sleep(0.6)
        return []

    monkeypatch.setattr(mw, "comic_files_in_folder", slow_scan)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    # a second Open Folder at 100 ms, delivered by the event loop the first call runs: the window must be disabled then
    QTimer.singleShot(100, lambda: (during.append(window.isEnabled()), window.isEnabled() and window.load_folder_dialog()))
    window.load_folder_dialog()
    assert scans == [1], f"{len(scans)} scans were started"
    assert during == [False]
    assert window.isEnabled()
    window.close()
