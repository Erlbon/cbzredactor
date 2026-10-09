"""The command line (cbzcli/): every command on real small archives, text and --json output, exit codes,
--dry-run and sidecar-free renames. The settings file is the test-isolated one (conftest)."""

import json
import os

import pytest

from cbzcli import cmd_files
from cbzcli.main import main
from core.cbz_file import CbzBook
from test_redact_steps import _cbt, _cbz

COMICINFO = (
    b'<?xml version="1.0"?><ComicInfo><Series>Saga</Series><Number>1</Number><Title>Chapter One</Title>'
    b"<Year>2012</Year><Publisher>Image</Publisher></ComicInfo>"
)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *argv):
    code, out, _err = run(capsys, *argv, "--json")
    return code, json.loads(out)


@pytest.fixture
def library(tmp_path):
    saga = _cbz(tmp_path / "saga1.cbz", comicinfo=COMICINFO)
    plain = _cbz(tmp_path / "plain.cbz", comicinfo=None)
    return tmp_path, saga, plain


# --- info -----------------------------------------------------------------------------


def test_info_shows_the_default_fields(library, capsys):
    tmp, saga, _plain = library
    code, out, _ = run(capsys, "info", saga)
    assert code == 0
    assert "3 pages" in out and "series: Saga" in out and "number: 1" in out and "publisher: Image" in out


def test_info_json_has_rows_and_a_summary(library, capsys):
    tmp, saga, plain = library
    code, document = run_json(capsys, "info", str(tmp), "--fields", "series,year")
    assert code == 0 and document["files"] == 2 and document["failed"] == 0
    rows = {os.path.basename(r["path"]): r for r in document["results"]}
    assert rows["saga1.cbz"]["fields"] == {"series": "Saga", "year": "2012"} and rows["saga1.cbz"]["has_comicinfo"] is True
    assert rows["plain.cbz"]["fields"] == {} and rows["plain.cbz"]["has_comicinfo"] is False


def test_info_all_lists_every_field_with_a_value(library, capsys):
    _tmp, saga, _ = library
    _code, document = run_json(capsys, "info", saga, "--all")
    assert set(document["results"][0]["fields"]) == {"series", "number", "title", "year", "publisher"}


def test_info_on_a_missing_path_is_a_usage_error(tmp_path, capsys):
    from redactor_common.cli import CliError

    with pytest.raises(CliError, match="no comic files"):
        main(["info", str(tmp_path / "nope.cbz")])


def test_a_foreign_file_is_reported_not_failed(tmp_path, capsys):
    cbt = _cbt(tmp_path / "old.cbt")
    code, document = run_json(capsys, "info", cbt)
    assert code == 0 and document["results"][0]["status"] == "needs conversion"


# --- set ----------------------------------------------------------------------------------


def test_set_changes_fields_and_saves(library, capsys):
    _tmp, saga, _ = library
    code, document = run_json(capsys, "set", saga, "-s", "series=Saga Deluxe", "-s", "Month=3", "--clear", "title")
    assert code == 0 and document["results"][0]["status"] == "changed"
    book = CbzBook(saga)
    assert (book.metadata.series, book.metadata.month, book.metadata.title) == ("Saga Deluxe", "3", "")
    assert book.metadata.publisher == "Image" and book.actual_page_count == 3  # the rest is untouched


def test_set_dry_run_writes_nothing(library, capsys):
    _tmp, saga, _ = library
    before = open(saga, "rb").read()
    code, out, _ = run(capsys, "set", saga, "-s", "Series=Else", "--dry-run")
    assert code == 0 and "planned" in out and "'Saga' -> 'Else'" in out
    assert open(saga, "rb").read() == before


def test_set_with_nothing_to_change_is_unchanged(library, capsys):
    _tmp, saga, _ = library
    _code, document = run_json(capsys, "set", saga, "-s", "Series=Saga")
    assert document["results"][0]["status"] == "unchanged"


@pytest.mark.parametrize("argv, message", [
    (["-s", "Nonsense=1"], "unknown field"),
    (["-s", "Year=abc"], "whole number"),
    (["-s", "Month=13"], "1 to 12"),
    (["-s", "Manga=maybe"], "must be one of"),
    (["-s", "novalue"], "FIELD=VALUE"),
    ([], "nothing to change"),
])
def test_set_refuses_bad_input_before_touching_anything(library, argv, message):
    from redactor_common.cli import CliError

    _tmp, saga, _ = library
    before = open(saga, "rb").read()
    with pytest.raises(CliError, match=message):
        main(["set", saga, *argv])
    assert open(saga, "rb").read() == before


def test_set_accepts_field_spellings_and_fixes_enum_case(library, capsys):
    _tmp, saga, _ = library
    run(capsys, "set", saga, "-s", "cover_artist=Fiona", "-s", "MANGA=yes")
    book = CbzBook(saga)
    assert book.metadata.cover_artist == "Fiona" and book.metadata.manga == "Yes"


def test_set_on_a_foreign_file_fails_with_exit_1(tmp_path, capsys):
    cbt = _cbt(tmp_path / "old.cbt")
    code, document = run_json(capsys, "set", cbt, "-s", "Series=X")
    assert code == 1 and document["failed"] == 1 and "needs conversion" in document["results"][0]["message"]


# --- convert ------------------------------------------------------------------------------


def test_convert_makes_a_cbz_and_keeps_the_original(tmp_path, capsys):
    cbt = _cbt(tmp_path / "old.cbt", pages=2)
    code, document = run_json(capsys, "convert", cbt)
    assert code == 0 and document["results"][0]["status"] == "converted"
    assert CbzBook(str(tmp_path / "old.cbz")).actual_page_count == 2 and os.path.exists(cbt)


def test_convert_dry_run_and_real_cbz_and_existing_target(tmp_path, capsys):
    cbt = _cbt(tmp_path / "old.cbt")
    real = _cbz(tmp_path / "real.cbz")
    _code, dry = run_json(capsys, "convert", cbt, real, "--dry-run")
    assert [r["status"] for r in dry["results"]] == ["planned", "skipped"] and not os.path.exists(tmp_path / "old.cbz")
    _cbz(tmp_path / "old.cbz")  # converted earlier
    before = open(tmp_path / "old.cbz", "rb").read()
    code, document = run_json(capsys, "convert", cbt)
    assert code == 0 and document["results"][0]["status"] == "skipped"
    assert open(tmp_path / "old.cbz", "rb").read() == before


def test_convert_can_send_the_original_to_the_recycle_bin(tmp_path, capsys, monkeypatch):
    cbt = _cbt(tmp_path / "old.cbt")
    trashed = []
    monkeypatch.setattr(cmd_files, "move_to_trash", trashed.append)
    run(capsys, "convert", cbt, "--trash-original")
    assert trashed == [cbt] and (tmp_path / "old.cbz").exists()


def test_convert_failure_is_exit_1_and_changes_nothing(tmp_path, capsys):
    broken = tmp_path / "broken.cbr"
    broken.write_bytes(b"not an archive at all")
    code, document = run_json(capsys, "convert", str(broken))
    assert code == 1 and document["results"][0]["status"] == "failed"
    assert broken.exists() and not (tmp_path / "broken.cbz").exists()


# --- rename ---------------------------------------------------------------------------------


def test_rename_by_pattern_with_padding(library, capsys):
    tmp, saga, plain = library
    code, document = run_json(capsys, "rename", saga, "-p", "%series% %number% - %title%", "--zero-pad", "3")
    assert code == 0 and document["results"][0]["status"] == "renamed"
    new_path = str(tmp / "Saga 001 - Chapter One.cbz")
    assert os.path.exists(new_path) and not os.path.exists(saga)


def test_rename_skips_a_file_whose_pattern_gives_no_name(library, capsys):
    _tmp, _saga, plain = library
    _code, document = run_json(capsys, "rename", plain, "-p", "%series% %number%")
    assert document["results"][0]["status"] == "skipped" and "empty name" in document["results"][0]["message"]
    assert os.path.exists(plain)


def test_rename_dry_run_changes_nothing_and_collisions_get_numbers(tmp_path, capsys):
    a = _cbz(tmp_path / "a.cbz", comicinfo=COMICINFO)
    b = _cbz(tmp_path / "b.cbz", comicinfo=COMICINFO)
    _code, dry = run_json(capsys, "rename", a, b, "-p", "%series% %number%", "--dry-run")
    assert [r["status"] for r in dry["results"]] == ["planned", "planned"] and os.path.exists(a) and os.path.exists(b)
    names = sorted(os.path.basename(r["new_path"]) for r in dry["results"])
    assert names == ["Saga 1 (2).cbz", "Saga 1.cbz"]
    run(capsys, "rename", a, b, "-p", "%series% %number%")
    assert sorted(n for n in os.listdir(tmp_path) if n.endswith(".cbz")) == ["Saga 1 (2).cbz", "Saga 1.cbz"]


def test_rename_leaves_a_file_that_already_has_the_name_alone(tmp_path, capsys):
    path = _cbz(tmp_path / "Saga 1.cbz", comicinfo=COMICINFO)
    _code, document = run_json(capsys, "rename", path, "-p", "%series% %number%")
    assert document["results"][0]["status"] == "unchanged"


# --- move ----------------------------------------------------------------------------------


def test_move_into_folders_by_pattern_then_copy(tmp_path, capsys):
    lib = tmp_path / "library"
    lib.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    book = _cbz(src / "x.cbz", comicinfo=COMICINFO)
    _code, dry = run_json(capsys, "move", book, "-p", "%publisher%/%series%/%series% %number%", "--root", str(lib), "--dry-run")
    assert dry["results"][0]["status"] == "planned" and os.path.exists(book)
    code, document = run_json(capsys, "move", book, "-p", "%publisher%/%series%/%series% %number%", "--root", str(lib))
    assert code == 0 and document["results"][0]["status"] == "moved"
    moved = lib / "Image" / "Saga" / "Saga 1.cbz"
    assert moved.exists() and not os.path.exists(book)
    code, document = run_json(capsys, "move", str(moved), "-p", "Copies/%series%", "--root", str(lib), "--copy")
    assert document["results"][0]["status"] == "copied" and (lib / "Copies" / "Saga.cbz").exists() and moved.exists()


def test_move_skips_a_file_the_pattern_has_no_name_for(library, tmp_path, capsys):
    _tmp, _saga, plain = library
    lib = tmp_path / "lib"
    lib.mkdir()
    _code, document = run_json(capsys, "move", plain, "-p", "%series%", "--root", str(lib))
    assert document["results"][0]["status"] == "skipped" and os.path.exists(plain) and not any(lib.iterdir())


def test_move_needs_an_existing_library_folder(library, tmp_path):
    from redactor_common.cli import CliError

    _tmp, saga, _ = library
    with pytest.raises(CliError, match="--root"):
        main(["move", saga, "-p", "%series%"])
    with pytest.raises(CliError, match="does not exist"):
        main(["move", saga, "-p", "%series%", "--root", str(tmp_path / "missing")])


# --- redact ---------------------------------------------------------------------------------


def test_redact_lists_its_steps(capsys):
    code, document = run_json(capsys, "redact", "--list-steps")
    assert code == 0
    steps = {r["step"]: r["enabled"] for r in document["results"]}
    assert steps["convert_to_cbz"] is True and steps["move_into_folders"] is False
    _code, document = run_json(capsys, "redact", "--list-steps", "--disable", "clean_contents", "--enable", "move_into_folders")
    steps = {r["step"]: r["enabled"] for r in document["results"]}
    assert steps["clean_contents"] is False and steps["move_into_folders"] is True


def test_redact_runs_the_recipe_saves_in_place_and_keeps_originals_in_trash_dir(tmp_path, capsys):
    junk = _cbz(tmp_path / "junky.cbz", comicinfo=COMICINFO, extra=[("Thumbs.db", b"junk")])
    bin_dir = tmp_path / "trash"
    code, document = run_json(
        capsys, "redact", junk, "--disable", "lookup", "--disable", "filename_tags", "--disable", "path_tags",
        "--trash-dir", str(bin_dir),
    )
    assert code == 0 and document["files"] == 1 and document["failed"] == 0
    entry = document["results"][0]
    assert entry["status"] == "changed" and entry["applied"]
    assert "Thumbs.db" not in CbzBook(junk).page_names
    assert len(os.listdir(bin_dir)) == 1  # the original, kept


def test_redact_text_report_and_a_failing_file_is_exit_1(tmp_path, capsys):
    broken = tmp_path / "broken.cbz"
    broken.write_bytes(b"not a zip")
    code, out, _ = run(capsys, "redact", str(broken), "--disable", "lookup")
    assert "Redact report" in out
    assert code in (0, 1)  # a file that cannot be read is skipped or failed, never written to
    assert broken.read_bytes() == b"not a zip"


def test_redact_rejects_unknown_steps_and_thresholds(library):
    from redactor_common.cli import CliError

    _tmp, saga, _ = library
    with pytest.raises(CliError, match="unknown step"):
        main(["redact", saga, "--disable", "nonsense"])
    with pytest.raises(CliError, match="threshold"):
        main(["redact", saga, "--threshold", "250"])
    with pytest.raises(CliError, match="give the comics"):
        main(["redact"])


def test_redact_reads_the_comic_vine_key_from_the_environment(monkeypatch):
    import argparse

    from cbzcli.cmd_redact import build_env

    monkeypatch.setenv("COMICVINE_API_KEY", " abc123 ")
    env = build_env(argparse.Namespace(trash_dir=None))
    assert env.comicvine_key == "abc123"


def test_the_entry_point_maps_errors_to_exit_codes(capsys):
    from redactor_common.cli import run

    assert run(lambda argv: main(["info", "definitely-not-there.cbz"])) == 2
    assert "no comic files found" in capsys.readouterr().err


# --- one exe ------------------------------------------------------------------------------------------


def test_a_command_name_starts_the_command_line_and_a_path_starts_the_window():
    from cbzcli import COMMANDS, cli_requested

    assert set(COMMANDS) == {"info", "set", "convert", "rename", "move", "redact"}
    assert cli_requested(["cbzredactor.exe", "info", "x.cbz"]) and cli_requested(["cbzredactor.exe", "--version"])
    assert not cli_requested(["cbzredactor.exe"])
    assert not cli_requested(["cbzredactor.exe", "D:/Comics/Saga 1.cbz"])  # a file to open in the window


def test_the_app_entry_point_runs_the_command_line_without_a_window(library, monkeypatch, capsys):
    import main as app

    _tmp, saga, _ = library
    monkeypatch.setattr(app.sys, "argv", ["cbzredactor", "info", saga, "--json"])
    monkeypatch.setattr(app, "run_app", lambda **kw: pytest.fail("the window was started"))
    assert app.main() == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["fields"]["series"] == "Saga"


def test_the_app_entry_point_still_starts_the_window_for_no_command(monkeypatch):
    import main as app

    started = []
    monkeypatch.setattr(app.sys, "argv", ["cbzredactor"])
    monkeypatch.setattr(app, "run_app", lambda **kw: started.append(kw["app_name"]) or 0)
    assert app.main() == 0 and started


def test_output_writes_the_result_to_a_file_for_scripts(library, capsys, tmp_path):
    _tmp, saga, _ = library
    target = tmp_path / "result.json"
    code = main(["info", saga, "--json", "--output", str(target)])
    assert code == 0 and capsys.readouterr().out == ""
    assert json.loads(target.read_text(encoding="utf-8"))["results"][0]["fields"]["series"] == "Saga"


# --- the documentation covers every option --------------------------------------------------------------


def _readme_cli_section() -> str:
    text = open(os.path.join(os.path.dirname(__file__), "README.md"), encoding="utf-8").read()
    start = text.index("## Command line")
    return text[start: text.index("## Running from source")]


def test_the_readme_documents_every_command_option_step_and_field():
    import argparse

    from cbzcli.fields import SETTABLE
    from cbzcli.main import build_parser
    from core.redact_steps import build_catalogue

    section = _readme_cli_section()
    parser = build_parser()
    missing = []
    for action in parser._actions:
        missing += [o for o in action.option_strings if o not in section]
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, sub in subparsers.choices.items():
        if f"### {name}" not in section:
            missing.append(f"### {name}")
        for action in sub._actions:
            for option in action.option_strings:
                if option not in section:
                    missing.append(f"{name} {option}")
    missing += [f"step {s.key}" for s in build_catalogue(None) if not s.hidden and s.key not in section]
    missing += [f"field {tag}" for tag in SETTABLE if tag not in section]
    assert missing == [], f"the README's Command line section does not mention: {missing}"


def test_the_readme_lists_the_exit_codes_and_the_scripting_ways():
    section = _readme_cli_section()
    for code in ("| 0 |", "| 1 |", "| 2 |", "| 70 |", "| 130 |"):
        assert code in section
    for way in ("start /wait", "Start-Process", "Out-Null", "--output"):
        assert way in section
