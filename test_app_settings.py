"""Tests for the pure-logic helpers in gui/app_settings.py -- the
merge/exclude/dedupe functions don't touch QSettings, so they're
testable without a live Qt platform backend."""

from gui.app_settings import (
    _dedupe_and_trim,
    _exclude_hidden_genres,
    _exclude_hidden_languages,
    _merge_genres,
    _merge_languages,
)


def test_dedupe_and_trim_moves_pattern_to_front():
    history = ["%series% %number%", "%title%"]
    assert _dedupe_and_trim(history, "%title%") == ["%title%", "%series% %number%"]


def test_dedupe_and_trim_ignores_blank_pattern():
    history = ["%title%"]
    assert _dedupe_and_trim(history, "   ") == history


def test_dedupe_and_trim_caps_length():
    history = [f"pattern{i}" for i in range(20)]
    result = _dedupe_and_trim(history, "new", max_history=15)
    assert len(result) == 15
    assert result[0] == "new"


def test_merge_genres_skips_duplicates_case_insensitively():
    defaults = ["Superhero", "Crime"]
    custom = ["superhero", "Noir"]
    assert _merge_genres(defaults, custom) == ["Superhero", "Crime", "Noir"]


def test_exclude_hidden_genres_case_insensitive():
    defaults = ["Superhero", "Crime", "Horror"]
    assert _exclude_hidden_genres(defaults, ["crime"]) == ["Superhero", "Horror"]


def test_merge_languages_defaults_win_on_code_conflict():
    defaults = [("en", "English")]
    custom = [("en", "Should Be Ignored"), ("pt", "Portuguese")]
    assert _merge_languages(defaults, custom) == [("en", "English"), ("pt", "Portuguese")]


def test_exclude_hidden_languages():
    defaults = [("en", "English"), ("de", "German")]
    assert _exclude_hidden_languages(defaults, ["de"]) == [("en", "English")]


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
