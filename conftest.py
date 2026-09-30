"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_rename_log(monkeypatch, tmp_path):
    """File > Undo Last Rename's log (redactor_common's RenameLog) lives
    next to the settings -- the project folder when running from source.
    Tests that rename files point it at a temporary folder instead."""
    import gui.main_window as main_window
    from redactor_common.core.rename_log import RenameLog

    log = RenameLog(str(tmp_path / "rename_log.json"))
    monkeypatch.setattr(main_window, "_rename_log", lambda: log)


@pytest.fixture(autouse=True)
def _isolated_collection_scan(monkeypatch, tmp_path):
    """Collection > Scan Collection Folder... saves next to the settings;
    tests save into a temporary folder instead."""
    import gui.main_window as main_window

    monkeypatch.setattr(main_window, "_collection_scan_path", lambda: str(tmp_path / "collection_scan.zip"))


class FakeKeyring:
    """In-memory stand-in for keyring's get/set/delete_password API."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        self.items.pop((service, name), None)


@pytest.fixture(autouse=True)
def fake_keyring(tmp_path):
    """No test may touch the real Windows Credential Manager (or write a
    real fallback file): the secret store gets an in-memory backend and a
    temporary fallback folder, both reset afterwards."""
    from redactor_common.core import secret_store

    backend = FakeKeyring()
    secret_store.set_backend(backend)
    secret_store.set_fallback_dir(tmp_path / "secret_fallback")
    secret_store.set_allow_unencrypted_fallback(False)
    yield backend
    secret_store.set_backend(None)
    secret_store.set_fallback_dir(None)
    secret_store.set_allow_unencrypted_fallback(False)
