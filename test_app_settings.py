"""gui/app_settings.py: thin delegations over redactor_common's
QSettingsStore at the app's own ini path, with the app's own keys."""

import pytest

from gui import app_settings


@pytest.fixture
def ini(monkeypatch, tmp_path):
    path = tmp_path / "s.ini"
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(path))
    return path


def test_pattern_history_moves_a_used_pattern_to_the_front_and_caps_it(ini):
    app_settings.save_pattern_used("%series% %number%")
    app_settings.save_pattern_used("%title%")
    app_settings.save_pattern_used("%series% %number%")
    app_settings.save_pattern_used("   ")  # a blank pattern is not remembered
    assert app_settings.load_pattern_history() == ["%series% %number%", "%title%"]
    for i in range(20):
        app_settings.save_pattern_used(f"pattern{i}")
    history = app_settings.load_pattern_history()
    assert len(history) == 15 and history[0] == "pattern19"
    app_settings.save_pattern_history([f"p{i}" for i in range(30)])
    assert len(app_settings.load_pattern_history()) == 15


def test_genres_merge_skips_duplicates_and_hiding_is_case_insensitive(ini, monkeypatch):
    monkeypatch.setattr(app_settings._GENRES, "_defaults", ["Superhero", "Crime", "Horror"])
    app_settings.add_custom_genre("superhero")
    app_settings.add_custom_genre("Noir")
    assert app_settings.load_genres() == ["Superhero", "Crime", "Horror", "Noir"]
    app_settings.hide_default_genre("crime")
    assert app_settings.load_visible_default_genres() == ["Superhero", "Horror"]
    app_settings.remove_custom_genre("Noir")
    app_settings.restore_default_genres()
    assert app_settings.load_genres()[:3] == ["Superhero", "Crime", "Horror"]
    assert "Noir" not in app_settings.load_custom_genres()


def test_languages_defaults_win_on_a_code_conflict_and_hide_by_code(ini, monkeypatch):
    monkeypatch.setattr(app_settings._LANGUAGES, "_defaults", [("en", "English"), ("de", "German")])
    app_settings.add_custom_language("en", "Should Be Ignored")
    app_settings.add_custom_language("pt", "Portuguese")
    assert app_settings.load_languages() == [("en", "English"), ("de", "German"), ("pt", "Portuguese")]
    app_settings.hide_default_language("de")
    assert app_settings.load_hidden_default_language_codes() == ["de"]
    assert app_settings.load_visible_default_languages() == [("en", "English")]


def test_keys_and_fallbacks_are_the_ones_the_app_always_used(ini):
    raw = app_settings._settings()
    raw.setValue("include_subfolders", "false")
    raw.setValue("table/column_widths", '{"series": 120, "title": "wide"}')
    raw.setValue("history/enabled", "banana")
    raw.setValue("conversion/resize_on_convert", "sometimes")
    raw.sync()
    assert app_settings.load_include_subfolders() is False
    assert app_settings.load_column_widths() == {}  # one unreadable width drops the map, as before
    assert app_settings.load_history_enabled() is False
    assert app_settings.load_resize_on_convert() == app_settings.RESIZE_ON_CONVERT_ASK
    assert app_settings.has_hidden_columns_preference() is False
    app_settings.save_hidden_columns(set())
    assert app_settings.has_hidden_columns_preference() is True and app_settings.load_hidden_columns() == set()
    app_settings.save_column_widths({"series": 0, "title": 300})
    assert app_settings.load_column_widths() == {"series": 0, "title": 300}
    app_settings.save_column_order(["title", "series"])
    assert app_settings.load_column_order() == ["title", "series"]
    app_settings.save_history_enabled(True)
    assert app_settings.load_history_enabled() is True
    assert app_settings._settings().contains("table/column_order")
    assert app_settings._settings().contains("history/enabled")


def test_last_directory_is_forgotten_when_the_folder_is_gone(ini, tmp_path):
    folder = tmp_path / "comics"
    folder.mkdir()
    app_settings.save_last_directory(str(folder / "a.cbz"))
    assert app_settings.load_last_directory() == str(folder)
    folder.rmdir()
    assert app_settings.load_last_directory() == ""
    app_settings.save_last_directory(str(tmp_path / "missing" / "a.cbz"))  # not stored
    assert app_settings._settings().value("files/last_directory") == str(folder)


def test_padding_choices_are_remembered(monkeypatch, tmp_path):
    from gui import app_settings

    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "s.ini"))

    assert app_settings.load_rename_zero_pad() == (False, 2)
    app_settings.save_rename_zero_pad(True, 3)
    assert app_settings.load_rename_zero_pad() == (True, 3)
    assert app_settings.load_auto_number_padding() == 2
    app_settings.save_auto_number_padding(4)
    assert app_settings.load_auto_number_padding() == 4


def test_hand_edited_int_settings_fall_back_to_defaults(monkeypatch, tmp_path):
    # T8: int(value) on "abc" crashed the Resize / Rename dialogs on open.
    from gui import app_settings

    ini = tmp_path / "s.ini"
    ini.write_text(
        "[resize]\nmax_width=wide\njpeg_quality=2.5\nmax_height=tall\n"
        "[rename]\nzero_pad_width=x\n[auto_numbering]\npadding=two\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(ini))
    options = app_settings.load_resize_options()
    assert options.max_width == app_settings._DEFAULT_RESIZE_MAX_WIDTH
    assert options.jpeg_quality == app_settings._DEFAULT_RESIZE_JPEG_QUALITY
    assert options.max_height is None
    assert app_settings.load_rename_zero_pad()[1] == 2
    assert app_settings.load_auto_number_padding() == 2
    app_settings.save_resize_max_width(1200)
    assert app_settings.load_resize_max_width() == 1200
