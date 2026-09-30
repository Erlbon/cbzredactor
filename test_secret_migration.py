"""Comic Vine key / GCD password live in redactor_common's secret store,
never in the settings ini; a pre-migration ini value is moved there once
and keeps working until then. The in-memory keyring comes from
conftest.py's autouse fake_keyring fixture (no real Credential Manager)."""

import base64
import sys

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import app_settings
from redactor_common.core import secret_store

_app = QApplication.instance() or QApplication(sys.argv)

APP = app_settings.SECRET_APP
CV = app_settings.COMICVINE_SECRET
GCD = app_settings.GCD_SECRET


@pytest.fixture(autouse=True)
def tmp_ini(monkeypatch, tmp_path):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: path)
    return path


def _scramble(text):
    key = app_settings._SCRAMBLE_KEY
    mixed = bytes(b ^ key[i % len(key)] for i, b in enumerate(text.encode("utf-8")))
    return "s1:" + base64.urlsafe_b64encode(mixed).decode("ascii")


def _seed_legacy():
    s = app_settings._settings()
    s.setValue("comicvine/api_key", "cv-old-key")
    s.setValue("gcd/username", "reader")
    s.setValue("gcd/password", _scramble("old-pass"))
    s.sync()


def _ini_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def unavailable(monkeypatch):
    """No secure store at all (like headless Linux)."""
    secret_store.set_backend(None)
    monkeypatch.setattr(secret_store, "_get_backend", lambda: None)


def test_migration_moves_both_values_and_clears_ini(fake_keyring, tmp_ini):
    _seed_legacy()
    app_settings.migrate_legacy_secrets()
    service = secret_store.service_name(APP)
    assert fake_keyring.items[(service, CV)] == "cv-old-key"
    assert fake_keyring.items[(service, GCD)] == "old-pass"
    text = _ini_text(tmp_ini)
    assert "cv-old-key" not in text and "api_key" not in text
    assert "password" not in text
    assert "reader" in text  # the username isn't a secret
    assert app_settings.load_comicvine_api_key() == "cv-old-key"
    assert app_settings.load_gcd_account() == ("reader", "old-pass")


def test_unavailable_keyring_leaves_legacy_and_keeps_working(unavailable, tmp_ini):
    _seed_legacy()
    app_settings.migrate_legacy_secrets()  # must not raise
    text = _ini_text(tmp_ini)
    assert "cv-old-key" in text and "password" in text
    assert app_settings.load_comicvine_api_key() == "cv-old-key"
    assert app_settings.load_gcd_account() == ("reader", "old-pass")


def test_unmigrated_install_works_before_migration_runs():
    _seed_legacy()
    assert app_settings.load_comicvine_api_key() == "cv-old-key"
    assert app_settings.load_gcd_account() == ("reader", "old-pass")


def test_saving_writes_nothing_secret_to_ini(fake_keyring, tmp_ini):
    _seed_legacy()
    app_settings.save_comicvine_api_key("cv-new-key")
    app_settings.save_gcd_account("reader2", "new-pass")
    text = _ini_text(tmp_ini)
    for secret in ("cv-new-key", "cv-old-key", "new-pass", _scramble("old-pass"), _scramble("new-pass")):
        assert secret not in text
    assert "password" not in text and "api_key" not in text
    assert app_settings.load_comicvine_api_key() == "cv-new-key"
    assert app_settings.load_gcd_account() == ("reader2", "new-pass")


def test_remove_account_and_blank_key(fake_keyring):
    app_settings.save_gcd_account("u", "p")
    app_settings.save_comicvine_api_key("k")
    app_settings.save_gcd_account("", "")
    app_settings.save_comicvine_api_key("  ")
    assert app_settings.load_gcd_account() == ("", "")
    assert app_settings.load_comicvine_api_key() == ""
    assert fake_keyring.items == {}


def test_save_without_store_raises_and_keeps_legacy(unavailable, tmp_ini):
    _seed_legacy()
    with pytest.raises(secret_store.SecretStoreUnavailable):
        app_settings.save_comicvine_api_key("new")
    assert "cv-old-key" in _ini_text(tmp_ini)


def test_gcd_lookup_dialog_gets_stored_credentials(monkeypatch):
    import gui.gcd_lookup_dialog as gld

    app_settings.save_gcd_account("reader", "pw")
    seen = []
    monkeypatch.setattr(gld, "make_gcd_fetch", lambda *a: seen.append(a) or (lambda url: None))
    gld.GcdLookupDialog([])
    assert seen == [("reader", "pw")]


# -- dialogs -----------------------------------------------------------------

def test_comicvine_key_dialog_saves_to_store(fake_keyring, tmp_ini):
    from gui.comicvine_key_dialog import ComicVineKeyDialog

    _seed_legacy()
    dialog = ComicVineKeyDialog()
    dialog.key_field.edit.setText("typed-key")
    dialog.accept()
    assert app_settings.load_comicvine_api_key() == "typed-key"
    text = _ini_text(tmp_ini)
    assert "cv-old-key" not in text and "typed-key" not in text


def test_gcd_dialog_saves_password_to_store(fake_keyring, tmp_ini):
    from gui.gcd_account_dialog import GcdAccountDialog

    dialog = GcdAccountDialog()
    dialog.username_edit.setText("me")
    dialog.password_field.edit.setText("hunter2")
    dialog.accept()
    assert app_settings.load_gcd_account() == ("me", "hunter2")
    assert "hunter2" not in _ini_text(tmp_ini)


def test_gcd_dialog_empty_password_keeps_stored(fake_keyring):
    from gui.gcd_account_dialog import GcdAccountDialog

    app_settings.save_gcd_account("me", "kept")
    dialog = GcdAccountDialog()
    dialog.accept()
    assert app_settings.load_gcd_account() == ("me", "kept")


def test_dialog_offers_fallback_and_remembers_yes(unavailable, monkeypatch, tmp_ini):
    from gui.comicvine_key_dialog import ComicVineKeyDialog

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes,
    )
    dialog = ComicVineKeyDialog()
    dialog.key_field.edit.setText("k1")
    dialog.accept()
    assert asked == [1]
    assert app_settings.load_allow_unencrypted_fallback() is True
    assert secret_store.secret_source(APP, CV) == secret_store.SOURCE_FILE
    assert app_settings.load_comicvine_api_key() == "k1"


def test_dialog_declining_fallback_saves_nothing(unavailable, monkeypatch, tmp_ini):
    from gui.comicvine_key_dialog import ComicVineKeyDialog

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog = ComicVineKeyDialog()
    dialog.key_field.edit.setText("k1")
    dialog.accept()
    assert app_settings.load_allow_unencrypted_fallback() is False
    assert app_settings.load_comicvine_api_key() == ""
    assert dialog.result() == 0  # still open, not accepted


# -- the merged Tools > API Keys... dialog -------------------------------------

def test_api_keys_dialog_saves_both_sections(fake_keyring, tmp_ini):
    from gui.api_keys_dialog import ApiKeysDialog

    dialog = ApiKeysDialog()
    dialog.key_field.edit.setText("typed-key")
    dialog.username_edit.setText("me")
    dialog.password_field.edit.setText("hunter2")
    dialog.accept()
    assert app_settings.load_comicvine_api_key() == "typed-key"
    assert app_settings.load_gcd_account() == ("me", "hunter2")
    text = _ini_text(tmp_ini)
    assert "typed-key" not in text and "hunter2" not in text


def test_api_keys_dialog_empty_boxes_keep_stored_values(fake_keyring):
    from gui.api_keys_dialog import ApiKeysDialog

    app_settings.save_comicvine_api_key("kept-key")
    app_settings.save_gcd_account("me", "kept")
    ApiKeysDialog().accept()
    assert app_settings.load_comicvine_api_key() == "kept-key"
    assert app_settings.load_gcd_account() == ("me", "kept")


def test_api_keys_dialog_remove_account(fake_keyring):
    from gui.api_keys_dialog import ApiKeysDialog

    app_settings.save_gcd_account("me", "pw")
    dialog = ApiKeysDialog()
    dialog._remove_account()
    assert app_settings.load_gcd_account() == ("", "")
