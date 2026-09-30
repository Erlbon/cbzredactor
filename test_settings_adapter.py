"""Export/Import Settings: gui/settings_adapter.py over gui/app_settings.py,
and the File menu entries that run the shared dialogs."""

import json
import sys

import pytest
from PyQt6.QtWidgets import QApplication

import gui.main_window as mw
from gui import app_settings
from gui.settings_adapter import CbzSettingsAdapter
from redactor_common.core import secret_store
from redactor_common.core import settings_bundle as sb

_app = QApplication.instance() or QApplication(sys.argv)

ALL = {s.key for s in CbzSettingsAdapter().sections()}
PORTABLE = {s.key for s in CbzSettingsAdapter().sections() if s.portable}


@pytest.fixture(autouse=True)
def temp_ini(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "settings.ini"))


def _roundtrip(adapter, keys):
    text = sb.dump_bundle(sb.build_bundle(adapter, keys))
    return sb.parse_bundle(text, "cbzredactor")


def test_export_change_import_restores():
    adapter = CbzSettingsAdapter("x")
    app_settings.save_pattern_used("%series% %number%")
    app_settings.save_custom_genres(["Noir", "Pulp"])
    app_settings.save_custom_languages([("pt", "Portuguese")])
    app_settings.save_hidden_default_language_codes(["fr"])
    app_settings.save_column_order(["filename", "title"])
    app_settings.save_column_widths({"filename": 300})
    app_settings.save_hidden_columns({"genre"})
    app_settings.save_rename_zero_pad(True, 3)
    app_settings.save_ascii_filenames(True)
    app_settings.save_resize_max_width(1600)
    app_settings.save_recycle_originals(True)
    app_settings.save_foreign_load_behavior(app_settings.FOREIGN_LOAD_ASK)
    app_settings.save_redact_recipe(json.dumps({"steps": [{"id": "a", "enabled": False}]}))
    bundle = _roundtrip(adapter, PORTABLE)

    app_settings.save_pattern_history([])
    app_settings.save_custom_genres([])
    app_settings.save_custom_languages([])
    app_settings.save_hidden_default_language_codes([])
    app_settings.save_column_order([])
    app_settings.save_column_widths({})
    app_settings.save_hidden_columns(set())
    app_settings.save_rename_zero_pad(False, 2)
    app_settings.save_ascii_filenames(False)
    app_settings.save_resize_max_width(1440)
    app_settings.save_recycle_originals(False)
    app_settings.save_foreign_load_behavior(app_settings.FOREIGN_LOAD_UNCONVERTED)
    app_settings.save_redact_recipe("")

    result = sb.apply_bundle(adapter, bundle, PORTABLE)
    assert result.failed == {}
    assert app_settings.load_pattern_history() == ["%series% %number%"]
    assert app_settings.load_custom_genres() == ["Noir", "Pulp"]
    assert app_settings.load_custom_languages() == [("pt", "Portuguese")]
    assert app_settings.load_hidden_default_language_codes() == ["fr"]
    assert app_settings.load_column_order() == ["filename", "title"]
    assert app_settings.load_column_widths() == {"filename": 300}
    assert app_settings.load_hidden_columns() == {"genre"}
    assert app_settings.load_rename_zero_pad() == (True, 3)
    assert app_settings.load_ascii_filenames() is True
    assert app_settings.load_resize_max_width() == 1600
    assert app_settings.load_recycle_originals() is True
    assert app_settings.load_foreign_load_behavior() == app_settings.FOREIGN_LOAD_ASK
    assert json.loads(app_settings.load_redact_recipe()) == {"steps": [{"id": "a", "enabled": False}]}


def test_values_are_normalised_to_json_types():
    # QSettings' ini backend returns strings for everything it stored.
    app_settings.save_custom_genres(["Noir"])
    app_settings.save_rename_zero_pad(True, 4)
    app_settings.save_resize_max_height(900)
    data = CbzSettingsAdapter()
    assert data.read_section("lists")["custom_genres"] == ["Noir"]
    rename = data.read_section("rename_defaults")
    assert rename["zero_pad_enabled"] is True and rename["zero_pad_width"] == 4
    assert data.read_section("resize")["max_height"] == 900
    assert isinstance(data.read_section("resize")["jpeg_quality"], int)
    assert data.read_section("conversion")["recycle_originals"] is False
    assert data.read_section("lists")["custom_languages"] == []
    json.dumps({k: data.read_section(k) for k in ALL})  # all plain JSON


def test_unset_hidden_columns_report_the_first_run_default():
    adapter = CbzSettingsAdapter(default_hidden_columns={"b", "a"})
    assert adapter.read_section("columns")["hidden"] == ["a", "b"]
    app_settings.save_hidden_columns(set())
    assert adapter.read_section("columns")["hidden"] == []


def test_machine_specific_section_is_not_portable_and_not_in_default_export():
    specs = {s.key: s for s in CbzSettingsAdapter().sections()}
    assert specs["paths"].portable is False
    assert [k for k, s in specs.items() if not s.portable] == ["paths"]
    app_settings.save_gcd_local_database("C:/db/gcd.db")
    bundle = _roundtrip(CbzSettingsAdapter(), PORTABLE)
    assert "paths" not in bundle.sections
    assert "gcd.db" not in sb.dump_bundle(bundle)
    with_paths = _roundtrip(CbzSettingsAdapter(), ALL)
    assert with_paths.sections["paths"].items["gcd_local_database"] == "C:/db/gcd.db"


def test_no_secret_ever_in_the_bundle():
    app_settings.save_comicvine_api_key("CV-SECRET-KEY-123")
    app_settings.save_gcd_account("gcduser", "GCD-SECRET-PW-456")
    assert secret_store.get_secret(app_settings.SECRET_APP, app_settings.GCD_SECRET) == "GCD-SECRET-PW-456"
    text = sb.dump_bundle(sb.build_bundle(CbzSettingsAdapter(), ALL))
    for needle in ("CV-SECRET-KEY-123", "GCD-SECRET-PW-456", "gcduser", "comicvine", "password"):
        assert needle not in text
    for key in ALL:
        assert not any(sb.looks_secret(k) for k in CbzSettingsAdapter().read_section(key))


def test_import_never_writes_secrets_or_unknown_keys():
    adapter = CbzSettingsAdapter()
    app_settings.save_gcd_account("me", "pw")
    doc = {
        "format": "redactor-settings", "version": 1, "app": "cbzredactor",
        "sections": {
            "resize": {"items": {"max_width": 1000, "api_key": "evil", "bogus": 1}},
            "nope": {"items": {"x": 1}},
            "conversion": {"items": {"gcd_password": "evil"}},
        },
    }
    bundle = sb.parse_bundle(json.dumps(doc), "cbzredactor")
    result = sb.apply_bundle(adapter, bundle, {"resize", "conversion", "nope"})
    assert app_settings.load_resize_max_width() == 1000
    assert app_settings.load_gcd_password() == "pw"
    assert app_settings.load_gcd_username() == "me"
    assert app_settings._settings().value("resize/bogus") is None
    assert result.failed == {}


def test_invalid_values_are_skipped():
    adapter = CbzSettingsAdapter()
    adapter.write_section("resize", {"max_width": "wide", "jpeg_quality": 500, "output_format": "BMP"})
    adapter.write_section("conversion", {"on_load": "explode", "recycle_originals": "yes"})
    adapter.write_section("columns", {"order": "filename", "widths": {"a": -5}})
    assert app_settings.load_resize_max_width() == app_settings._DEFAULT_RESIZE_MAX_WIDTH
    assert app_settings.load_resize_jpeg_quality() == 90
    assert app_settings.load_resize_output_format() == ""
    assert app_settings.load_foreign_load_behavior() == app_settings.FOREIGN_LOAD_UNCONVERTED
    assert app_settings.load_recycle_originals() is False
    assert app_settings.load_column_order() == [] and app_settings.load_column_widths() == {}


def test_other_apps_file_is_rejected():
    text = json.dumps({"format": "redactor-settings", "version": 1, "app": "epubredactor", "sections": {}})
    with pytest.raises(sb.SettingsBundleError):
        sb.parse_bundle(text, "cbzredactor")


def test_file_menu_actions_are_enabled_and_open_the_dialogs(monkeypatch):
    calls = []
    monkeypatch.setattr(mw, "run_export_settings", lambda parent, adapter: calls.append(("export", adapter)))
    monkeypatch.setattr(
        mw, "run_import_settings",
        lambda parent, adapter, on_applied=None: calls.append(("import", adapter, on_applied)),
    )
    window = mw.MainWindow()
    for key in ("export_settings", "import_settings"):
        assert window.actions_[key].isEnabled()
    window.actions_["export_settings"].trigger()
    window.actions_["import_settings"].trigger()
    assert [c[0] for c in calls] == ["export", "import"]
    assert isinstance(calls[0][1], CbzSettingsAdapter)
    assert calls[0][1].app_version == mw.APP_VERSION
    assert calls[1][2] == window._on_settings_imported


def test_import_reapplies_columns_live():
    window = mw.MainWindow()
    keys = window._column_keys
    wanted = ["filename"] + [k for k in reversed(keys) if k != "filename"]
    app_settings.save_column_order(wanted)
    app_settings.save_hidden_columns({"genre"})
    window._on_settings_imported()
    header = window.table.horizontalHeader()
    assert [keys[header.logicalIndex(v)] for v in range(header.count())] == wanted
    assert window.table.isColumnHidden(window._col_index["genre"])
    assert not window.table.isColumnHidden(window._col_index["filename"])
