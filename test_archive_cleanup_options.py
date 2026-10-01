"""Clean Up Archive Contents as three independent options -- remove junk, move
pages out of folders, rename pages to numbers (core/archive_contents.py,
CbzBook.clean_contents), the dialog's checkboxes and their persistence, and
the Redact step's matching options (rename off by default there)."""

import sys
import zipfile

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from redactor_common.core.pipeline import FileStatus, Recipe

from core.archive_contents import CleanupOptions, plan_cleanup
from core.cbz_file import CbzBook, page_sort_key
from core.redact_steps import CleanContentsStep, build_catalogue, recipe_from_setting, recipe_to_setting

from test_redact_steps import COMICINFO, _cbz, _only, _recipe, _run, env  # noqa: F401  (env is a fixture)

_app = QApplication.instance() or QApplication(sys.argv)

SCENE = "Batman 045 (2018) (Digital) (Zone-Empire)"
JUNK = ["Thumbs.db", "__MACOSX/._p1.jpg", "Zone-Empire.nfo"]
JUNK_ONLY = CleanupOptions(remove_junk=True, flatten_folders=False, rename_pages=False)
FLATTEN_ONLY = CleanupOptions(remove_junk=False, flatten_folders=True, rename_pages=False)
RENAME_ONLY = CleanupOptions(remove_junk=False, flatten_folders=False, rename_pages=True)


def _scene(path, pages=None, junk=True):
    """pages: (entry name, bytes). Default: a scene release in its group folder, unpadded numbers."""
    pages = pages if pages is not None else [(f"{SCENE}/{SCENE} p{n}.jpg", f"page {n}".encode()) for n in (1, 2, 10)]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{SCENE}/", b"")
        for name, data in pages:
            zf.writestr(name, data)
        if junk:
            for name in JUNK:
                zf.writestr(name, b"x")
        zf.writestr("ComicInfo.xml", COMICINFO)
    return str(path)


def _contents(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _page_data_in_reading_order(path):
    book = CbzBook(path)
    with zipfile.ZipFile(path) as zf:
        return [zf.read(name) for name in book.page_names]


ORIGINAL_PAGES = [b"page 1", b"page 2", b"page 10"]


# --- each option alone and together -----------------------------------------------------------


def test_defaults_are_all_three_on():
    assert CleanupOptions() == CleanupOptions(True, True, True)
    assert CleanupOptions().any and not CleanupOptions(False, False, False).any


def test_junk_only_removes_junk_and_touches_nothing_else(tmp_path):
    path = _scene(tmp_path / "x.cbz")
    before = _contents(path)
    book = CbzBook(path)
    plan = book.clean_contents(options=JUNK_ONLY, dispose_original=lambda p: None)
    assert plan.needed and not plan.renames and sorted(plan.removals) == sorted(JUNK)
    after = _contents(path)
    assert set(before) - set(after) == set(JUNK)
    assert {k: v for k, v in before.items() if k not in JUNK} == after
    assert list(zipfile.ZipFile(path).namelist()) == [n for n in before if n not in JUNK]  # entry order kept
    assert book.page_names == CbzBook(path).page_names and f"{SCENE}/" in after  # folder entry stays


def test_flatten_only_moves_pages_out_keeping_their_names_and_order(tmp_path):
    path = _scene(tmp_path / "x.cbz")
    plan = CbzBook(path).clean_contents(options=FLATTEN_ONLY, dispose_original=lambda p: None)
    assert plan.drop_folder_entries and plan.folders == 1 and not plan.removals and not plan.numbered
    names = zipfile.ZipFile(path).namelist()
    assert names[:3] == [f"{SCENE} p1.jpg", f"{SCENE} p2.jpg", f"{SCENE} p10.jpg"]  # own names, reading order
    assert not any("/" in n for n in names if n not in JUNK) and f"{SCENE}/" not in names
    assert "Thumbs.db" in names  # junk untouched
    assert _page_data_in_reading_order(path) == ORIGINAL_PAGES
    assert "move 3 page(s) out of 1 folder(s)" in plan.summary()


def test_rename_only_numbers_pages_inside_their_folder(tmp_path):
    path = _scene(tmp_path / "x.cbz")
    plan = CbzBook(path).clean_contents(options=RENAME_ONLY, dispose_original=lambda p: None)
    assert plan.numbered and not plan.drop_folder_entries and not plan.removals and not plan.notes
    names = zipfile.ZipFile(path).namelist()
    assert [f"{SCENE}/{n}" for n in ("001.jpg", "002.jpg", "003.jpg")] == [n for n in names if n.endswith(".jpg") and "/" in n and "MACOSX" not in n]
    assert f"{SCENE}/" in names and "Thumbs.db" in names
    assert _page_data_in_reading_order(path) == ORIGINAL_PAGES


def test_all_three_is_what_it_always_did(tmp_path):
    path = _scene(tmp_path / "x.cbz")
    book = CbzBook(path)
    book.clean_contents(dispose_original=lambda p: None)
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == ["001.jpg", "002.jpg", "003.jpg", "ComicInfo.xml"]
    assert _page_data_in_reading_order(path) == ORIGINAL_PAGES
    explicit = _scene(tmp_path / "y.cbz")
    CbzBook(explicit).clean_contents(options=CleanupOptions(True, True, True), dispose_original=lambda p: None)
    assert _contents(explicit) == _contents(path)


@pytest.mark.parametrize("junk,flatten,rename", [
    (True, True, False), (True, False, True), (False, True, True), (True, False, False), (False, False, True),
])
def test_combinations_keep_page_bytes_and_order(tmp_path, junk, flatten, rename):
    path = _scene(tmp_path / "x.cbz")
    options = CleanupOptions(junk, flatten, rename)
    CbzBook(path).clean_contents(options=options, dispose_original=lambda p: None)
    assert _page_data_in_reading_order(path) == ORIGINAL_PAGES
    names = zipfile.ZipFile(path).namelist()
    assert (not any(n in names for n in JUNK)) == junk
    assert CbzBook(path).metadata.series == "Saga"


def test_comicinfo_is_moved_to_the_top_only_when_folders_are_flattened():
    names = ["Book/ComicInfo.xml", "Book/p1.jpg"]
    assert plan_cleanup(names, ["Book/p1.jpg"], "Book/ComicInfo.xml", FLATTEN_ONLY).renames["Book/ComicInfo.xml"] == "ComicInfo.xml"
    assert "Book/ComicInfo.xml" not in plan_cleanup(names, ["Book/p1.jpg"], "Book/ComicInfo.xml", RENAME_ONLY).renames
    assert "Book/ComicInfo.xml" not in plan_cleanup(names, ["Book/p1.jpg"], "Book/ComicInfo.xml", JUNK_ONLY).renames


# --- no-op detection ---------------------------------------------------------------------------


@pytest.mark.parametrize("options", [JUNK_ONLY, FLATTEN_ONLY, RENAME_ONLY, CleanupOptions(),
                                     CleanupOptions(False, False, False)])
def test_a_clean_archive_is_a_noop_for_every_option_set(tmp_path, options):
    path = _cbz(tmp_path / "x.cbz")
    before = open(path, "rb").read()
    trashed = []
    plan = CbzBook(path).clean_contents(options=options, dispose_original=trashed.append)
    assert not plan.needed and not trashed and open(path, "rb").read() == before


def test_an_option_that_is_off_never_counts_as_needed(tmp_path):
    path = _scene(tmp_path / "x.cbz", junk=True)
    assert not CbzBook(path).cleanup_plan(CleanupOptions(False, False, False)).needed
    only_junk = _cbz(tmp_path / "j.cbz", extra=[("Thumbs.db", b"x")])
    assert CbzBook(only_junk).cleanup_plan(JUNK_ONLY).needed
    assert not CbzBook(only_junk).cleanup_plan(FLATTEN_ONLY).needed
    assert not CbzBook(only_junk).cleanup_plan(RENAME_ONLY).needed


# --- flattening keeps names unique and reading order -----------------------------------------------


def test_flatten_prefixes_a_number_only_where_names_collide(tmp_path):
    pages = [("Ch1/001.jpg", b"a"), ("Ch1/002.jpg", b"b"), ("Ch2/001.jpg", b"c"), ("Ch2/003.jpg", b"d")]
    path = _scene(tmp_path / "x.cbz", pages=pages, junk=False)
    book = CbzBook(path)
    assert book.page_names == ["Ch1/001.jpg", "Ch1/002.jpg", "Ch2/001.jpg", "Ch2/003.jpg"]
    plan = book.clean_contents(options=FLATTEN_ONLY, dispose_original=lambda p: None)
    assert plan.renames == {
        "Ch1/001.jpg": "001 - 001.jpg", "Ch1/002.jpg": "002.jpg",
        "Ch2/001.jpg": "003 - 001.jpg", "Ch2/003.jpg": "003.jpg",
    }
    assert book.page_names == sorted(book.page_names, key=page_sort_key)
    assert len({n.lower() for n in book.page_names}) == 4
    assert _page_data_in_reading_order(path) == [b"a", b"b", b"c", b"d"]
    assert CbzBook(path).page_names == book.page_names


def test_flatten_falls_back_to_numbering_every_page_when_order_would_change():
    # No clash, but "a.jpg" sorts before "z.jpg": the bare names would reorder the pages.
    pages = ["A/z.jpg", "B/a.jpg"]
    plan = plan_cleanup(pages, pages, None, FLATTEN_ONLY)
    assert plan.renames == {"A/z.jpg": "001 - z.jpg", "B/a.jpg": "002 - a.jpg"}


def test_flatten_never_reorders_across_many_shapes():
    import random

    rng = random.Random(7)
    for _ in range(300):
        pages = sorted({
            f"{rng.choice(['', 'A/', 'B/', 'a/', 'Ch 10/', 'Ch 2/'])}{rng.choice(['', 'p', 'Page '])}{rng.randint(1, 30)}.jpg"
            for _ in range(rng.randint(1, 12))
        }, key=page_sort_key)
        for options in (FLATTEN_ONLY, CleanupOptions(True, True, False)):
            plan = plan_cleanup(pages, pages, None, options)
            new = [plan.renames.get(p, p) for p in pages]
            assert len({n.lower() for n in new}) == len(new), (pages, new)
            assert new == sorted(new, key=page_sort_key), (pages, new)
            assert all("/" not in n for n in new)


def test_flatten_leaves_unique_names_in_order_alone():
    pages = ["Ch1/p1.jpg", "Ch1/p2.jpg", "Ch2/p3.jpg"]
    plan = plan_cleanup(pages, pages, None, FLATTEN_ONLY)
    assert plan.renames == {"Ch1/p1.jpg": "p1.jpg", "Ch1/p2.jpg": "p2.jpg", "Ch2/p3.jpg": "p3.jpg"}


def test_flatten_treats_names_that_differ_only_in_case_as_a_clash():
    pages = ["A/Page1.jpg", "B/page1.jpg"]
    plan = plan_cleanup(pages, pages, None, FLATTEN_ONLY)
    assert plan.renames == {"A/Page1.jpg": "001 - Page1.jpg", "B/page1.jpg": "002 - page1.jpg"}


def test_rename_without_flatten_moves_pages_out_when_folders_would_reorder_them():
    pages = ["Ch1/p1.jpg", "zzz.jpg"]  # reads Ch1 first; numbered in place, the top-level page would sort first
    plan = plan_cleanup(pages, pages, None, RENAME_ONLY)
    assert plan.renames == {"Ch1/p1.jpg": "001.jpg", "zzz.jpg": "002.jpg"}
    assert plan.notes and plan.folders == 1


# --- the dialog ----------------------------------------------------------------------------------


@pytest.fixture
def window(tmp_path, monkeypatch):
    from gui import app_settings

    monkeypatch.setattr(app_settings, "_settings_ini_path", lambda: str(tmp_path / "s.ini"))
    monkeypatch.setattr(app_settings, "credit_pages_path", lambda: str(tmp_path / "known.json"))
    trashed = []
    monkeypatch.setattr("gui.main_window.move_to_trash", trashed.append)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from gui.main_window import MainWindow

    w = MainWindow()
    w.trashed = trashed
    return w


def test_settings_default_to_all_on_and_persist(window):
    from gui import app_settings

    assert app_settings.load_cleanup_options() == CleanupOptions(True, True, True)
    app_settings.save_cleanup_options(CleanupOptions(True, False, False))
    assert app_settings.load_cleanup_options() == CleanupOptions(True, False, False)


def test_dialog_opens_with_the_saved_options_and_follows_the_checkboxes(tmp_path):
    from PyQt6.QtCore import Qt

    from gui.archive_cleanup_dialog import ArchiveCleanupDialog

    scene = CbzBook(_scene(tmp_path / "Scene.cbz"))
    only_junk = CbzBook(_cbz(tmp_path / "Junk.cbz", extra=[("Thumbs.db", b"x")]))
    candidates = [(b, b.entry_names()) for b in (scene, only_junk)]
    dialog = ArchiveCleanupDialog(candidates, CleanupOptions(), "", None)
    assert dialog.options() == CleanupOptions() and all(box.isChecked() for box in dialog.checkboxes.values())
    assert dialog.table.rowCount() == 2 and dialog.apply_button.text() == "Clean Up 2 File(s)"
    dialog.checkboxes["remove_junk"].setChecked(False)
    dialog.checkboxes["rename_pages"].setChecked(False)  # flatten only: the junk-only file has nothing to do
    assert dialog.table.rowCount() == 1 and dialog.ticked() == [scene]
    assert "move 3 page(s) out of 1 folder(s)" in dialog.table.item(0, 1).text()
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.ticked() == [] and not dialog.apply_button.isEnabled()
    dialog.checkboxes["flatten_folders"].setChecked(False)  # nothing left to do at all
    assert dialog.table.rowCount() == 0
    dialog.checkboxes["remove_junk"].setChecked(True)
    assert dialog.table.rowCount() == 2 and dialog.ticked() == [only_junk]  # the unticked file stays unticked


def test_each_option_has_plain_language_help(tmp_path):
    from gui.archive_cleanup_dialog import OPTION_TEXTS, ArchiveCleanupDialog

    dialog = ArchiveCleanupDialog([], CleanupOptions(), "", None)
    assert [field for field, _text, _help in OPTION_TEXTS] == ["remove_junk", "flatten_folders", "rename_pages"]
    for field, text, help_text in OPTION_TEXTS:
        assert dialog.checkboxes[field].text() == text and dialog.checkboxes[field].toolTip() == help_text
        assert len(help_text) > 40


def test_window_applies_and_remembers_the_chosen_options(window, tmp_path, monkeypatch):
    from gui import app_settings, archive_cleanup_dialog

    scene = _scene(tmp_path / "Batman 045.cbz")
    window._load_paths([scene])
    seen = {}

    def choose_junk_only(dialog):
        seen["initial"] = dialog.options()
        for field in ("flatten_folders", "rename_pages"):
            dialog.checkboxes[field].setChecked(False)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(archive_cleanup_dialog.ArchiveCleanupDialog, "exec", choose_junk_only)
    window._selected_rows = []
    window.open_clean_contents_dialog()
    assert seen["initial"] == CleanupOptions()  # first time: all on, as before
    assert window.trashed == [scene]
    names = zipfile.ZipFile(scene).namelist()
    assert not any(j in names for j in JUNK) and any(n.startswith(SCENE + "/") for n in names)  # pages stayed put
    assert app_settings.load_cleanup_options() == JUNK_ONLY
    # The next time the dialog starts from what was chosen.
    again = _scene(tmp_path / "Batman 046.cbz")
    window._load_paths([again])
    window.open_clean_contents_dialog()
    assert seen["initial"] == JUNK_ONLY


def test_window_says_nothing_to_do_for_a_clean_file(window, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    window._load_paths([_cbz(tmp_path / "Clean 001.cbz")])
    window._selected_rows = []
    window.open_clean_contents_dialog()
    assert messages and "Nothing to clean up in 1 file(s)" in messages[0]


# --- the Redact step -------------------------------------------------------------------------------


def _step():
    return next(s for s in build_catalogue(None) if s.key == "clean_contents")


def test_redact_step_declares_three_bool_options_with_the_required_defaults():
    step = _step()
    assert isinstance(step, CleanContentsStep)
    assert [(o.key, o.kind, o.default) for o in step.options] == [
        ("remove_junk", "bool", True), ("flatten_folders", "bool", True), ("rename_pages", "bool", False),
    ]
    assert "does not rename pages on its own" in step.description
    assert all(o.label and o.tooltip for o in step.options)


def test_default_recipe_cleans_junk_and_folders_but_does_not_rename_pages(env, tmp_path):  # noqa: F811
    path = _scene(tmp_path / "Saga 001.cbz")
    book = CbzBook(path)
    entry = _only(_run(env, [book], _recipe(env, disable=("lookup", "path_tags", "filename_tags", "tag_low_res"))))
    assert entry.status is FileStatus.CHANGED
    names = zipfile.ZipFile(path).namelist()
    assert not any(j in names for j in JUNK)
    assert f"{SCENE} p1.jpg" in names and "001.jpg" not in names  # moved out of the folder, name kept
    assert _page_data_in_reading_order(path) == ORIGINAL_PAGES


def test_redact_rename_pages_is_opt_in(env, tmp_path):  # noqa: F811
    path = _scene(tmp_path / "Saga 001.cbz")
    recipe = _recipe(env, disable=("lookup", "path_tags", "filename_tags", "tag_low_res"),
                     options={"clean_contents": {"rename_pages": True}})
    _only(_run(env, [CbzBook(path)], recipe))
    assert zipfile.ZipFile(path).namelist()[:3] == ["001.jpg", "002.jpg", "003.jpg"]


@pytest.mark.parametrize("options", [
    {"remove_junk": False, "flatten_folders": False, "rename_pages": False},
    {"remove_junk": False, "flatten_folders": False, "rename_pages": True},  # nothing to rename in a clean book
])
def test_redact_noop_is_unchanged_and_never_rewrites(env, tmp_path, options):  # noqa: F811
    path = _cbz(tmp_path / "Saga 001.cbz")
    before = open(path, "rb").read()
    recipe = _recipe(env, disable=("lookup", "path_tags", "filename_tags", "validate_fix", "tag_low_res"),
                     options={"clean_contents": options})
    entry = _only(_run(env, [CbzBook(path)], recipe))
    assert entry.status is FileStatus.UNCHANGED
    assert open(path, "rb").read() == before and env.bin == []


def test_redact_with_every_option_off_leaves_a_messy_archive_alone(env, tmp_path):  # noqa: F811
    path = _scene(tmp_path / "Saga 001.cbz")
    before = open(path, "rb").read()
    recipe = _recipe(env, disable=("lookup", "path_tags", "filename_tags", "validate_fix", "tag_low_res"),
                     options={"clean_contents": {"remove_junk": False, "flatten_folders": False, "rename_pages": False}})
    entry = _only(_run(env, [CbzBook(path)], recipe))
    assert entry.status is FileStatus.UNCHANGED and open(path, "rb").read() == before


def test_recipe_round_trips_the_options():
    recipe = Recipe.default_for(build_catalogue(None))
    recipe.options["clean_contents"] = {"remove_junk": False, "flatten_folders": True, "rename_pages": True}
    again = recipe_from_setting(recipe_to_setting(recipe))
    assert again.options["clean_contents"] == {"remove_junk": False, "flatten_folders": True, "rename_pages": True}
    (resolved,) = [opts for step, opts in again.resolve(build_catalogue(None)) if step.key == "clean_contents"]
    assert resolved == {"remove_junk": False, "flatten_folders": True, "rename_pages": True}


def test_a_recipe_saved_before_the_options_existed_gets_the_new_defaults():
    old = (
        '{"order": ["convert_to_cbz", "clean_contents"], "enabled": {"clean_contents": true}, '
        '"options": {"resize_images": {"max_width": 900}}, "confidence_threshold": 0.9}'
    )
    recipe = recipe_from_setting(old)
    assert "clean_contents" not in recipe.options
    resolved = {step.key: opts for step, opts in recipe.resolve(build_catalogue(None))}
    assert resolved["clean_contents"] == {"remove_junk": True, "flatten_folders": True, "rename_pages": False}
    # A hand-edited non-bool falls back to the default instead of crashing.
    recipe.options["clean_contents"] = {"rename_pages": "yes", "remove_junk": 0}
    resolved = {step.key: opts for step, opts in recipe.resolve(build_catalogue(None))}
    assert resolved["clean_contents"] == {"remove_junk": True, "flatten_folders": True, "rename_pages": False}
