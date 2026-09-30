"""Tests for core/redact_steps.py -- the Redact recipe's steps and the save
stage, run through redactor_common's engine on synthetic CBZ/CBT files in
tmp_path. The Recycle Bin is faked (files move into a folder, so the
engine's "did the bin take it" check behaves as in real life); lookups are
mocked."""

import io
import os
import shutil
import tarfile
import types
import zipfile

import pytest
from PIL import Image, ImageDraw

from redactor_common.core.pipeline import FileStatus, Recipe, run_recipe
from redactor_common.core.rename_log import RenameLog

import core.redact_steps as rs
from core.cbz_file import CbzBook
from core.comicvine_lookup import ComicVineCandidate, ComicVineIssueDetails, ComicVineLookupError
from core.credit_pages import KnownCreditPages, dhash
from core.redact_steps import (
    FINALIZE_LABEL,
    CbzCtx,
    RedactEnv,
    build_catalogue,
    recipe_for_run,
    recipe_from_setting,
    recipe_to_setting,
    run_catalogue,
    save_stage,
)

COMICINFO = (
    b'<?xml version="1.0"?><ComicInfo><Series>Saga</Series><Number>1</Number><Year>2012</Year>'
    b"<Publisher>Image</Publisher><Writer>BKV</Writer><Summary>s</Summary></ComicInfo>"
)
FAKE_JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


def _jpeg(width, height, seed=0):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for i in range(6):
        x = (seed * 97 + i * 131) % max(1, width - 100)
        y = (seed * 53 + i * 211) % max(1, height - 100)
        draw.rectangle((x, y, x + 80, y + 90), fill=((seed * 40 + i * 30) % 255, 90, 160))
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def _tag_page(width=400, height=600):
    image = Image.new("RGB", (width, height), (20, 20, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((width * 0.1, height * 0.35, width * 0.9, height * 0.6), fill=(200, 30, 30))
    draw.ellipse((width * 0.3, height * 0.1, width * 0.7, height * 0.3), fill=(240, 240, 240))
    return image


def _cbz(path, comicinfo=COMICINFO, pages=None, extra=()):
    pages = pages if pages is not None else [(f"{i:03}.jpg", FAKE_JPEG) for i in range(1, 4)]
    with zipfile.ZipFile(path, "w") as zf:
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo)
        for name, data in pages:
            zf.writestr(name, data)
        for name, data in extra:
            zf.writestr(name, data)
    return str(path)


def _cbt(path, pages=3):
    with tarfile.open(path, "w") as tf:
        for i in range(1, pages + 1):
            info = tarfile.TarInfo(f"{i:03}.jpg")
            info.size = len(FAKE_JPEG)
            tf.addfile(info, io.BytesIO(FAKE_JPEG))
    return str(path)


@pytest.fixture
def env(tmp_path):
    """A run environment with a fake Recycle Bin (`env.bin` lists what it took)."""
    bin_dir = tmp_path / "_bin"
    bin_dir.mkdir()
    env = RedactEnv(rename_log=RenameLog(str(tmp_path / "log.json")))
    env.bin = []

    def fake_trash(path):
        env.bin.append(path)
        shutil.move(path, str(bin_dir / f"{len(env.bin)}-{os.path.basename(path)}"))

    env.trash = fake_trash
    return env


def _recipe(env, enable=(), disable=(), options=None, threshold=None):
    recipe = Recipe.default_for(build_catalogue(env))
    for key in enable:
        recipe.enabled[key] = True
    for key in disable:
        recipe.enabled[key] = False
    for key, opts in (options or {}).items():
        recipe.options[key] = {**recipe.options.get(key, {}), **opts}
    if threshold is not None:
        recipe.confidence_threshold = threshold
    return recipe


def _run(env, books, recipe=None):
    recipe = recipe_for_run(recipe or _recipe(env))
    return run_recipe(
        books, recipe, run_catalogue(env), lambda book: CbzCtx(book, env),
        describe=lambda book: os.path.basename(book.path), finalize=save_stage, finalize_label=FINALIZE_LABEL,
    )


def _only(report):
    assert len(report.entries) == 1
    return report.entries[0]


def _leftovers(folder):
    return sorted(n for n in os.listdir(folder) if "redact" in n)


# --- catalogue and recipe ---------------------------------------------------


def test_default_enabled_states(env):
    enabled = {s.key: s.default_enabled for s in build_catalogue(env)}
    assert enabled == {
        "convert_to_cbz": True, "clean_contents": True, "remove_credit_pages": False, "resize_images": False,
        "path_tags": True, "filename_tags": True, "lookup": True, "validate_fix": True, "tag_low_res": True,
        "rename": False, "move_into_folders": False,
    }
    env.pattern_history = ["%series% %number%"]
    assert {s.key: s.default_enabled for s in build_catalogue(env)}["rename"] is True


def test_rename_and_move_are_pinned_last_and_guard_first(env):
    resolved = [s.key for s, _ in _recipe(env, enable=("move_into_folders", "rename")).resolve(run_catalogue(env))]
    assert resolved[0] == "guard"
    assert resolved[-2:] == ["rename", "move_into_folders"]
    shuffled = Recipe(order=["move_into_folders", "rename", "lookup"], enabled={"move_into_folders": True, "rename": True})
    keys = [s.key for s, _ in recipe_for_run(shuffled).resolve(run_catalogue(env))]
    assert keys[0] == "guard" and keys[-2:] == ["rename", "move_into_folders"]


def test_recipe_round_trip_and_guard_cannot_be_switched_off(env):
    recipe = _recipe(env, enable=("resize_images",), options={"resize_images": {"max_width": 900}}, threshold=0.8)
    again = recipe_from_setting(recipe_to_setting(recipe))
    assert again.to_dict() == recipe.to_dict()
    assert again.options["resize_images"]["max_width"] == 900 and again.confidence_threshold == 0.8
    garbage = recipe_from_setting("{not json")
    assert garbage.order == [] and garbage.confidence_threshold == 0.9
    off = Recipe(enabled={"guard": False})
    assert "guard" in [s.key for s, _ in recipe_for_run(off).resolve(run_catalogue(env))]


def test_resize_step_defaults_to_the_existing_1440(env):
    step = next(s for s in build_catalogue(env) if s.key == "resize_images")
    assert {o.key: o.default for o in step.options}["max_width"] == 1440


# --- skips -------------------------------------------------------------------


def test_unsaved_and_unreadable_files_are_skipped(env, tmp_path):
    dirty = CbzBook(_cbz(tmp_path / "A.cbz"))
    dirty.dirty = True
    broken_path = tmp_path / "B.cbz"
    broken_path.write_bytes(b"not a zip")
    broken = CbzBook(str(broken_path))
    before = (tmp_path / "A.cbz").read_bytes()
    report = _run(env, [dirty, broken])
    assert [e.status for e in report.entries] == [FileStatus.SKIPPED, FileStatus.SKIPPED]
    assert "unsaved edits" in report.entries[0].skips[0]
    assert "could not be read" in report.entries[1].skips[0]
    assert (tmp_path / "A.cbz").read_bytes() == before and env.bin == []
    text = report.to_text()
    assert "SKIPPED" in text and "unsaved edits" in text


# --- save stage --------------------------------------------------------------


def test_clean_contents_and_metadata_fix_save_in_place(env, tmp_path):
    messy = COMICINFO.replace(b"<Series>Saga</Series>", b"<Series>  Saga  </Series>")
    path = _cbz(tmp_path / "Saga 001.cbz", comicinfo=messy, extra=[("Thumbs.db", b"junk")])
    book = CbzBook(path)
    entry = _only(_run(env, [book]))
    assert entry.status is FileStatus.CHANGED
    with zipfile.ZipFile(path) as zf:
        assert "Thumbs.db" not in zf.namelist()
    assert book.metadata.series == "Saga" and not book.dirty and book.page_names == ["001.jpg", "002.jpg", "003.jpg"]
    assert len(env.bin) == 1 and env.bin[0].endswith(".redact-orig.cbz")
    assert any("Save: saved in place" in a for a in entry.applied)
    assert _leftovers(tmp_path) == []


def test_untouched_file_is_not_rewritten(env, tmp_path):
    path = _cbz(tmp_path / "Saga 001.cbz")
    before = open(path, "rb").read()
    entry = _only(_run(env, [CbzBook(path)]))
    assert entry.status is FileStatus.UNCHANGED
    assert open(path, "rb").read() == before and env.bin == [] and _leftovers(tmp_path) == []


def test_failed_verification_leaves_the_original_untouched(env, tmp_path, monkeypatch):
    path = _cbz(tmp_path / "Saga 001.cbz", extra=[("Thumbs.db", b"junk")])
    before = open(path, "rb").read()
    monkeypatch.setattr(rs, "verify_written_file", lambda p, n: False)
    entry = _only(_run(env, [CbzBook(path)]))
    assert entry.status is FileStatus.FAILED and "NOT SAVED" in entry.failures[0]
    assert open(path, "rb").read() == before and env.bin == [] and _leftovers(tmp_path) == []


def test_bin_failure_keeps_the_original_beside_the_new_file(env, tmp_path):
    path = _cbz(tmp_path / "Saga 001.cbz", extra=[("Thumbs.db", b"junk")])

    def broken_bin(_path):
        raise OSError("no bin here")

    env.trash = broken_bin
    book = CbzBook(path)
    entry = _only(_run(env, [book]))
    assert entry.status is FileStatus.CHANGED
    assert any("kept" in a for a in entry.applied) and any("no bin here" in n for n in entry.notes)
    assert (tmp_path / "Saga 001.redact-orig.cbz").exists()
    with zipfile.ZipFile(path) as zf:
        assert "Thumbs.db" not in zf.namelist()


# --- convert -----------------------------------------------------------------


def test_convert_cbt_to_cbz(env, tmp_path):
    path = _cbt(tmp_path / "Saga 001.cbt")
    book = CbzBook(path)
    assert book.needs_conversion
    entry = _only(_run(env, [book]))
    cbz = str(tmp_path / "Saga 001.cbz")
    assert entry.status is FileStatus.CHANGED, entry
    assert os.path.exists(cbz) and not os.path.exists(path)
    assert env.bin == [path]
    assert book.path == cbz and not book.needs_conversion and book.actual_page_count == 3
    assert book.metadata.series == "" and entry.review  # the scene reader's guess waits for review
    assert _leftovers(tmp_path) == []


def test_convert_does_not_overwrite_an_existing_cbz(env, tmp_path):
    path = _cbt(tmp_path / "Saga 001.cbt")
    other = _cbz(tmp_path / "Saga 001.cbz")
    before = open(other, "rb").read()
    entry = _only(_run(env, [CbzBook(path)]))
    assert entry.status is FileStatus.FAILED and "already exists" in entry.failures[0]
    assert os.path.exists(path) and open(other, "rb").read() == before and env.bin == []


def test_foreign_file_with_convert_step_off_is_left_alone_with_a_note(env, tmp_path):
    path = _cbt(tmp_path / "Saga 001.cbt")
    entry = _only(_run(env, [CbzBook(path)], _recipe(env, disable=("convert_to_cbz",))))
    assert entry.status is FileStatus.UNCHANGED and os.path.exists(path) and env.bin == []
    assert any("needs converting" in n for n in entry.notes)


# --- filename tags -----------------------------------------------------------


def _empty_info(path):
    return _cbz(path, comicinfo=b"<ComicInfo/>")


def test_explicit_pattern_match_is_applied(env, tmp_path):
    book = CbzBook(_empty_info(tmp_path / "Saga 007 - Hello.cbz"))
    recipe = _recipe(env, options={"filename_tags": {"pattern": "%series% %number% - %title%"}})
    entry = _only(_run(env, [book], recipe))
    assert entry.status is FileStatus.CHANGED
    assert (book.metadata.series, book.metadata.number, book.metadata.title) == ("Saga", "7", "Hello")
    assert any("auto-applied at 95%" in a for a in entry.applied)


def test_history_pattern_goes_to_review_until_threshold_lowered(env, tmp_path):
    env.pattern_history = ["%series% %number% - %title%"]
    path = _empty_info(tmp_path / "Saga 007 - Hello.cbz")
    book = CbzBook(path)
    entry = _only(_run(env, [book]))
    assert entry.status is FileStatus.NEEDS_REVIEW and book.metadata.series == ""
    assert entry.review[0].confidence == pytest.approx(0.85) and "series=Saga" in str(entry.review[0].value)
    assert "NEEDS REVIEW" in _run(env, [CbzBook(path)]).to_text()
    again = _only(_run(env, [book], _recipe(env, threshold=0.8)))
    assert again.status is FileStatus.CHANGED and book.metadata.number == "7"


def test_scene_reader_is_the_low_confidence_fallback_and_never_overwrites(env, tmp_path):
    book = CbzBook(_cbz(tmp_path / "Other Series 012 (2020).cbz", comicinfo=b"<ComicInfo><Series>Keep</Series></ComicInfo>"))
    entry = _only(_run(env, [book]))
    assert entry.status is FileStatus.NEEDS_REVIEW
    fields = entry.review[0].value.fields
    assert "series" not in fields and fields["number"] == "12" and entry.review[0].confidence == rs.SCENE_CONFIDENCE


# --- lookup ------------------------------------------------------------------


class FakeDb:
    is_comicrack = False

    def __init__(self, candidates, fields):
        self.candidates, self.fields, self.searches = candidates, fields, []

    def search(self, series, number, year, series_year):
        self.searches.append((series, number, year, series_year))
        return self.candidates

    def details(self, issue_id):
        return types.SimpleNamespace(as_dict=lambda: dict(self.fields))


def _candidate(exact=True, number="1", key_date="2012-03-00"):
    return types.SimpleNamespace(
        issue_id=5, exact_name=exact, number=number, key_date=key_date, display_label=lambda: "Saga (2012) #1"
    )


LOOKUP_FIELDS = {"series": "Saga", "number": "1", "year": "2012", "publisher": "Image", "writer": "Brian K. Vaughan"}


def _lookup_env(env, db):
    env.local_databases = ["gcd.db"]
    env.open_database = lambda path: db
    return env


def test_local_lookup_exact_match_is_applied(env, tmp_path):
    db = FakeDb([_candidate()], LOOKUP_FIELDS)
    book = CbzBook(_cbz(tmp_path / "Saga 001 (2012).cbz", comicinfo=b"<ComicInfo><Title>T</Title></ComicInfo>"))
    entry = _only(_run(_lookup_env(env, db), [book], _recipe(env, disable=("filename_tags",))))
    assert entry.status is FileStatus.CHANGED
    assert (book.metadata.publisher, book.metadata.writer, book.metadata.title) == ("Image", "Brian K. Vaughan", "T")
    assert any("auto-applied at 95%" in a for a in entry.applied)
    assert db.searches == [("Saga", "1", "2012", "")]


def test_local_lookup_series_and_number_only_needs_review(env, tmp_path):
    db = FakeDb([_candidate(key_date="1999-01-00")], LOOKUP_FIELDS)
    book = CbzBook(_cbz(tmp_path / "Saga 001 (2012).cbz", comicinfo=b"<ComicInfo/>"))
    entry = _only(_run(_lookup_env(env, db), [book], _recipe(env, disable=("filename_tags",))))
    assert entry.status is FileStatus.NEEDS_REVIEW and book.metadata.publisher == ""
    assert entry.review[0].confidence == pytest.approx(0.85)


def test_lookup_without_sources_does_nothing_and_says_so(env, tmp_path):
    book = CbzBook(_cbz(tmp_path / "Saga 001.cbz", comicinfo=b"<ComicInfo/>"))
    entry = _only(_run(env, [book], _recipe(env, disable=("filename_tags",))))
    assert entry.status is FileStatus.UNCHANGED
    assert any("no local database" in n for n in entry.notes)


def test_comicvine_offline_is_a_note_not_a_failure_and_is_not_retried(env, tmp_path, monkeypatch):
    calls = []

    def offline(*args, **kwargs):
        calls.append(1)
        raise ComicVineLookupError("network unreachable")

    monkeypatch.setattr(rs, "search_comicvine", offline)
    env.comicvine_key = "key"
    books = [CbzBook(_cbz(tmp_path / f"Saga 00{i}.cbz", comicinfo=b"<ComicInfo/>")) for i in (1, 2)]
    report = _run(env, books, _recipe(env, disable=("filename_tags",)))
    assert [e.status for e in report.entries] == [FileStatus.UNCHANGED] * 2
    assert any("network unreachable" in n for n in report.entries[0].notes)
    assert any("skipped" in n for n in report.entries[1].notes)
    assert len(calls) == 1


def test_comicvine_match_confidence_from_quality(env, tmp_path, monkeypatch):
    candidate = ComicVineCandidate(
        issue_id="1", volume_name="Saga", issue_number="1", cover_date="2012-03-14", detail_url="d", volume_detail_url="v"
    )
    monkeypatch.setattr(rs, "search_comicvine", lambda *a, **k: [candidate])
    monkeypatch.setattr(
        rs, "fetch_issue_details", lambda key, url: ComicVineIssueDetails(volume_name="Saga", issue_number="1", writer="BKV")
    )
    monkeypatch.setattr(rs, "fetch_publisher", lambda key, url: "Image")
    env.comicvine_key = "key"
    book = CbzBook(_cbz(tmp_path / "Saga 001 (2012).cbz", comicinfo=b"<ComicInfo/>"))
    entry = _only(_run(env, [book], _recipe(env, disable=("filename_tags",))))
    assert entry.status is FileStatus.CHANGED and book.metadata.publisher == "Image"
    assert any("auto-applied at 95%" in a and "Comic Vine" in a for a in entry.applied)


# --- credit pages, resize, tag, validate -----------------------------------------


def test_credit_pages_and_resize_are_off_by_default(env, tmp_path):
    pages = [(f"{i:03}.jpg", _jpeg(1200, 1800, i)) for i in range(1, 6)]
    path = _cbz(tmp_path / "A.cbz", pages=pages)
    before = open(path, "rb").read()
    entry = _only(_run(env, [CbzBook(path)]))
    assert entry.status is FileStatus.UNCHANGED and open(path, "rb").read() == before


def _book_with_credit_page(tmp_path):
    pages = [(f"{i:03}.jpg", _jpeg(400, 600, i)) for i in range(1, 9)]
    tag = io.BytesIO()
    _tag_page().save(tag, "JPEG")
    pages.append(("zzz_credits.jpg", tag.getvalue()))
    known = KnownCreditPages(str(tmp_path / "known.json"))
    known.add(dhash(_tag_page()), b"", "credits")
    return CbzBook(_cbz(tmp_path / "A.cbz", pages=pages)), known


def test_credit_page_removal_applies_only_above_the_threshold(env, tmp_path):
    book, env.known_credits = _book_with_credit_page(tmp_path)
    enable = {"enable": ("remove_credit_pages",), "disable": ("tag_low_res", "clean_contents")}
    review = _only(_run(env, [book], _recipe(env, threshold=1.0, **enable)))
    assert review.status is FileStatus.NEEDS_REVIEW and book.actual_page_count == 9
    assert "zzz_credits.jpg" in str(review.review[0].value)
    done = _only(_run(env, [book], _recipe(env, **enable)))
    assert done.status is FileStatus.CHANGED and book.actual_page_count == 8
    assert "zzz_credits.jpg" not in book.page_names and book.metadata.page_count == "8"
    assert len(env.bin) == 1


def test_resize_when_enabled(env, tmp_path):
    pages = [(f"{i:03}.jpg", _jpeg(1200, 1800, i)) for i in range(1, 5)]
    book = CbzBook(_cbz(tmp_path / "A.cbz", pages=pages))
    recipe = _recipe(env, enable=("resize_images",), disable=("tag_low_res",), options={"resize_images": {"max_width": 600}})
    entry = _only(_run(env, [book], recipe))
    assert entry.status is FileStatus.CHANGED and book.actual_page_count == 4
    with zipfile.ZipFile(book.path) as zf:
        assert Image.open(io.BytesIO(zf.read("001.jpg"))).size[0] == 600
    # Already small: a second run finds nothing to do and rewrites nothing.
    before = open(book.path, "rb").read()
    assert _only(_run(env, [book], recipe)).status is FileStatus.UNCHANGED
    assert open(book.path, "rb").read() == before


def test_low_res_books_get_tagged_and_untagged(env, tmp_path):
    pages = [(f"{i:03}.jpg", _jpeg(600, 900, i)) for i in range(1, 4)]
    book = CbzBook(_cbz(tmp_path / "A.cbz", pages=pages))
    _only(_run(env, [book]))
    assert "Low-res scan" in book.metadata.tags
    # ...and the tag leaves a book that is no longer low-res.
    big = CbzBook(_cbz(tmp_path / "B.cbz", comicinfo=COMICINFO.replace(b"</ComicInfo>", b"<Tags>Low-res scan</Tags></ComicInfo>"),
                       pages=[(f"{i:03}.jpg", _jpeg(1200, 1800, i)) for i in range(1, 4)]))
    _only(_run(env, [big]))
    assert "Low-res scan" not in big.metadata.tags


def test_validate_fix_applies_fixable_issues(env, tmp_path):
    info = b"<ComicInfo><Series>Saga</Series><Number>#007</Number><LanguageISO>en-US</LanguageISO></ComicInfo>"
    book = CbzBook(_cbz(tmp_path / "A.cbz", comicinfo=info))
    entry = _only(_run(env, [book], _recipe(env, disable=("filename_tags",))))
    assert entry.status is FileStatus.CHANGED
    assert (book.metadata.number, book.metadata.language_iso) == ("7", "en")


# --- rename and move ---------------------------------------------------------------


def test_rename_runs_after_the_save_and_is_logged(env, tmp_path):
    env.pattern_history = ["%series% %number%"]
    path = _cbz(tmp_path / "old name.cbz", extra=[("Thumbs.db", b"x")])
    book = CbzBook(path)
    entry = _only(_run(env, [book], _recipe(env, disable=("filename_tags",))))
    new = str(tmp_path / "Saga 1.cbz")
    assert entry.status is FileStatus.CHANGED and book.path == new and os.path.exists(new)
    assert not os.path.exists(path) and book.metadata.series == "Saga"
    assert [a for a in entry.applied if "renamed" in a]
    batch = env.rename_log.last_batch()
    assert batch.renames == [(path, new)]


def test_rename_alone_does_not_rewrite_the_archive(env, tmp_path):
    env.pattern_history = ["%series% %number%"]
    path = _cbz(tmp_path / "old name.cbz")
    original = open(path, "rb").read()
    book = CbzBook(path)
    entry = _only(_run(env, [book], _recipe(env, disable=("filename_tags",))))
    assert entry.status is FileStatus.CHANGED and env.bin == []
    assert open(book.path, "rb").read() == original


def test_rename_numbers_a_collision_instead_of_overwriting(env, tmp_path):
    env.pattern_history = ["%series% %number%"]
    taken = _cbz(tmp_path / "Saga 1.cbz", comicinfo=b"<ComicInfo><Title>other</Title></ComicInfo>")
    book = CbzBook(_cbz(tmp_path / "old.cbz"))
    _only(_run(env, [book], _recipe(env, disable=("filename_tags",))))
    assert os.path.basename(book.path) == "Saga 1 (2).cbz" and CbzBook(taken).metadata.title == "other"


def test_move_into_folders_and_undo(env, tmp_path):
    root = tmp_path / "Library"
    root.mkdir()
    env.library_root = str(root)
    env.pattern_history = ["%publisher%/%series%/%series% %number%"]
    path = _cbz(tmp_path / "old.cbz")
    book = CbzBook(path)
    recipe = _recipe(env, enable=("move_into_folders",), disable=("filename_tags",))
    entry = _only(_run(env, [book], recipe))
    moved = root / "Image" / "Saga" / "Saga 1.cbz"
    assert entry.status is FileStatus.CHANGED and moved.exists() and book.path == str(moved)
    assert not os.path.exists(path)
    batch = env.rename_log.last_batch()
    assert batch.renames == [(path, str(moved))] and batch.root == str(root)
    assert batch.created_dirs == [str(root / "Image"), str(root / "Image" / "Saga")]
    result = env.rename_log.undo_last()
    assert not result.problems and os.path.exists(path) and not moved.exists()


def test_move_without_a_library_root_is_a_note(env, tmp_path):
    env.pattern_history = ["%series%/%series% %number%"]
    book = CbzBook(_cbz(tmp_path / "old.cbz"))
    entry = _only(_run(env, [book], _recipe(env, enable=("move_into_folders",), disable=("filename_tags",))))
    assert entry.status is FileStatus.UNCHANGED and any("library root" in n for n in entry.notes)
    assert os.path.exists(book.path)


def test_move_never_overwrites(env, tmp_path):
    root = tmp_path / "Library"
    (root / "Saga").mkdir(parents=True)
    env.library_root = str(root)
    env.pattern_history = ["%series%/%series% %number%"]
    _cbz(root / "Saga" / "Saga 1.cbz", comicinfo=b"<ComicInfo><Title>other</Title></ComicInfo>")
    book = CbzBook(_cbz(tmp_path / "old.cbz"))
    _only(_run(env, [book], _recipe(env, enable=("move_into_folders",), disable=("filename_tags",))))
    assert book.path == str(root / "Saga" / "Saga 1 (2).cbz")
    assert CbzBook(str(root / "Saga" / "Saga 1.cbz")).metadata.title == "other"


# --- path tags ---------------------------------------------------------------


def _library(tmp_path, env, *parts, name="Saga 007.cbz"):
    """A book at <Library>/<parts>/<name> with empty ComicInfo; env.library_root set."""
    root = tmp_path / "Library"
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    env.library_root = str(root)
    return CbzBook(_empty_info(folder / name))


def _path_recipe(env, pattern=None, **kwargs):
    options = {"path_tags": {"pattern": pattern}} if pattern else None
    return _recipe(env, disable=("filename_tags",), options=options, **kwargs)


def test_path_tags_runs_before_filename_tags_and_lookup(env):
    keys = [s.key for s in build_catalogue(env)]
    assert keys.index("path_tags") < keys.index("filename_tags") < keys.index("lookup")


def test_path_tags_confident_match_is_applied(env, tmp_path):
    book = _library(tmp_path, env, "Image", "Saga (2012)")
    entry = _only(_run(env, [book], _path_recipe(env, "%publisher%/%series% (%year%)/%series% %number%")))
    assert entry.status is FileStatus.CHANGED
    meta = book.metadata
    assert (meta.publisher, meta.series, meta.year, meta.number) == ("Image", "Saga", "2012", "7")
    assert any("from the folder path" in a for a in entry.applied)


def test_path_tags_bare_folder_capture_goes_to_review_then_applies_when_threshold_lowered(env, tmp_path):
    book = _library(tmp_path, env, "Saga")
    entry = _only(_run(env, [book], _path_recipe(env, "%series%/%title% %number%")))  # bare %series% folder
    assert entry.status is FileStatus.NEEDS_REVIEW and book.metadata.series == ""
    assert entry.review[0].confidence == pytest.approx(0.875)
    again = _only(_run(env, [book], _path_recipe(env, "%series%/%title% %number%", threshold=0.85)))
    assert again.status is FileStatus.CHANGED and book.metadata.series == "Saga" and book.metadata.number == "7"


def test_path_tags_missing_folder_is_reviewed_with_the_missing_segment_named(env, tmp_path):
    book = _library(tmp_path, env)  # the file sits directly in the root
    entry = _only(_run(env, [book], _path_recipe(env, "%publisher%/Saga %number%")))
    assert entry.status is FileStatus.NEEDS_REVIEW
    assert "no match for '%publisher%'" in entry.review[0].reason
    assert book.metadata.number == ""


def test_path_tags_fill_empty_only(env, tmp_path):
    root = tmp_path / "Library"
    (root / "Image").mkdir(parents=True)
    env.library_root = str(root)
    book = CbzBook(_cbz(root / "Image" / "Saga 007.cbz", comicinfo=b"<ComicInfo><Series>Keep</Series></ComicInfo>"))
    _only(_run(env, [book], _path_recipe(env, "%publisher%/%series% %number%", threshold=0.5)))
    assert (book.metadata.series, book.metadata.publisher, book.metadata.number) == ("Keep", "Image", "7")


def test_filename_tags_do_not_overwrite_path_results(env, tmp_path):
    book = _library(tmp_path, env, "Image", "Saga (2012)", name="Other 009.cbz")
    env.pattern_history = ["%series% %number%"]
    recipe = _recipe(
        env, options={"path_tags": {"pattern": "%publisher%/%series% (%year%)/%title% %number%"}}, threshold=0.8
    )
    _only(_run(env, [book], recipe))
    assert book.metadata.title == "Other" and book.metadata.series == "Saga"  # series from the path, not "Other"
    assert book.metadata.number == "9" and book.metadata.publisher == "Image"


def test_path_tags_without_a_library_root_is_a_note(env, tmp_path):
    book = CbzBook(_empty_info(tmp_path / "Saga 007.cbz"))
    entry = _only(_run(env, [book], _path_recipe(env, threshold=0.8)))
    assert entry.status is FileStatus.UNCHANGED and any("no library root" in n for n in entry.notes)
    assert book.metadata.series == ""


def test_path_tags_outside_the_root_is_a_note(env, tmp_path):
    _library(tmp_path, env, "Image")
    (tmp_path / "Elsewhere").mkdir()
    outside = CbzBook(_empty_info(tmp_path / "Elsewhere" / "Saga 007.cbz"))
    entry = _only(_run(env, [outside], _path_recipe(env, "%series%/%title% %number%")))
    assert entry.status is FileStatus.UNCHANGED and any("outside the library root" in n for n in entry.notes)
    assert outside.metadata.series == ""


def test_path_tags_default_pattern_is_the_newest_saved_path_pattern(env, tmp_path):
    book = _library(tmp_path, env, "Image", "Saga")
    env.pattern_history = ["%series% %number%", "%publisher%/%series%/%series% %number%", "%series%/%title%"]
    entry = _only(_run(env, [book], _path_recipe(env, threshold=0.8)))
    assert entry.status is FileStatus.CHANGED and book.metadata.publisher == "Image"
    # No saved path pattern at all: the built-in %series%/%title% %number%.
    env.pattern_history = ["%series% %number%"]
    second = _library(tmp_path, env, "Saga", name="Saga 008.cbz")
    _only(_run(env, [second], _path_recipe(env, threshold=0.8)))
    assert second.metadata.series == "Saga" and second.metadata.number == "8"


def test_recipe_round_trip_keeps_the_path_step(env):
    recipe = _recipe(env, options={"path_tags": {"pattern": "%series%/%series% %number%"}}, disable=("path_tags",))
    again = recipe_from_setting(recipe_to_setting(recipe))
    assert again.to_dict() == recipe.to_dict()
    assert again.options["path_tags"]["pattern"] == "%series%/%series% %number%"
    assert again.enabled["path_tags"] is False
    assert "path_tags" not in [s.key for s, _ in again.resolve(run_catalogue(env))]
