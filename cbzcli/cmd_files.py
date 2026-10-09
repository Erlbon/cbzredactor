"""
cbzcli/cmd_files.py

  convert PATH...   CBR/CB7/CBT (or a mislabeled .cbz) to a real .cbz
  rename  PATH...   rename by a metadata pattern ("%series% %number% - %title%")
  move    PATH...   move (or copy) into folders under a library root by a pattern ("%publisher%/%series%/...")

Renames and moves are recorded in the same log the app's File > Undo Last Rename reads.
"""

from __future__ import annotations

import argparse
import os

from core.cbz_file import CbzBook, path_needs_conversion
from core.foreign_archive_convert import ForeignArchiveConversionError, convert_to_cbz, relabel_mislabeled_cbz
from core.redact_steps import FILENAME_FIELD_KEYS
from gui import app_settings
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options
from redactor_common.core.move_plan import execute_move, plan_moves
from redactor_common.core.rename_pattern import render_filename, unique_path, zero_pad_numeric_value
from redactor_common.core.trash import TrashError, move_to_trash

from cbzcli.files import add_path_arguments, collect, load_books, rename_log

DEFAULT_RENAME_PATTERN = "%series% %number% - %title%"


def _add_pattern_options(parser, pattern_required: bool) -> None:
    parser.add_argument("-p", "--pattern", required=pattern_required, metavar="PATTERN",
                        help="filename pattern with %%field%% tokens, e.g. \"%%series%% %%number%% - %%title%%\"")
    parser.add_argument("--zero-pad", type=int, default=0, metavar="N", help="pad the number to N digits (001)")
    parser.add_argument("--ascii", action="store_true", help="ASCII-safe names (é -> e, æ -> ae, ...)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would happen, change nothing")


def _values_for(book: CbzBook, zero_pad: int) -> dict[str, str]:
    values = {attr: getattr(book.metadata, attr, "") or "" for attr in FILENAME_FIELD_KEYS}
    if zero_pad > 0 and values.get("number"):
        values["number"] = zero_pad_numeric_value(values["number"], zero_pad)
    if values.get("month"):
        values["month"] = zero_pad_numeric_value(values["month"], 2)
    return values


def _row(path: str) -> dict:
    return {"path": path, "status": "", "message": "", "new_path": ""}


def _say(out: Output, row: dict, verb_width: int = 9) -> None:
    target = f"  ->  {row['new_path']}" if row["new_path"] else ""
    note = f"  ({row['message']})" if row["message"] else ""
    out.line(f"{row['status']:{verb_width}} {row['path']}{target}{note}")


# --- convert ------------------------------------------------------------------------


def add_convert_parser(sub) -> None:
    parser = sub.add_parser(
        "convert", help="convert CBR/CB7/CBT to CBZ",
        description="Convert CBR, CB7 and CBT files (and .cbz files that are really another format) to real .cbz "
                    "files beside them. Never overwrites: a .cbz that already exists is left as it is.",
    )
    add_path_arguments(parser)
    parser.add_argument("--resize", action="store_true",
                        help="shrink the pages in the same pass, with the saved Resize defaults (Tools > Preferences)")
    parser.add_argument("--trash-original", action="store_true",
                        help="send the original to the Recycle Bin after the new .cbz is made (never deleted for good)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would be converted, change nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_convert)


def run_convert(args: argparse.Namespace, out: Output) -> int:
    files = collect(args.paths, out, recurse=not args.no_recurse)
    resize = app_settings.load_resize_options() if args.resize else None
    failed = 0
    for index, path in enumerate(files, start=1):
        out.progress(index, len(files), path)
        row = _row(path)
        _convert_one(path, args, resize, row, out)
        failed += row["status"] == "failed"
        out.record(row)
        _say(out, row)
    out.finish({"files": len(files), "failed": failed, "dry_run": args.dry_run})
    return EXIT_PARTIAL if failed else EXIT_OK


def _convert_one(path: str, args, resize, row: dict, out: Output) -> None:
    if not path_needs_conversion(path):
        row["status"], row["message"] = "skipped", "already a real CBZ"
        return
    target = os.path.splitext(path)[0] + ".cbz"
    if target != path and os.path.lexists(target):
        row["status"], row["message"], row["new_path"] = "skipped", "a .cbz of that name already exists; left alone", target
        return
    if args.dry_run:
        row["status"], row["new_path"] = "planned", target
        return
    source = path
    try:
        if os.path.splitext(path)[1].lower() == ".cbz":
            source = relabel_mislabeled_cbz(path)  # book.cbz that is really a RAR/7z/tar gets its true extension first
        new_path = convert_to_cbz(source, resize=resize)
    except ForeignArchiveConversionError as exc:
        _restore_name(source, path, row)
        row["status"], row["message"] = "failed", str(exc)
        return
    except OSError as exc:
        _restore_name(source, path, row)
        row["status"], row["message"] = "failed", str(exc)
        return
    row["status"], row["new_path"] = "converted", new_path
    if args.trash_original:
        try:
            move_to_trash(source)
        except TrashError as exc:
            row["message"] = f"converted, but the original was kept: {exc}"
            out.warn(f"{os.path.basename(source)}: the original was kept ({exc})")


def _restore_name(source: str, path: str, row: dict) -> None:
    if source != path:
        try:
            os.rename(source, path)
        except OSError as exc:
            row["message"] = f"could not restore the name {os.path.basename(path)}: {exc}"


# --- rename -------------------------------------------------------------------------


def add_rename_parser(sub) -> None:
    parser = sub.add_parser(
        "rename", help="rename files by a metadata pattern",
        description="Rename each comic from its ComicInfo fields. Never overwrites: a name that is taken gets (2), (3)...",
    )
    add_path_arguments(parser)
    _add_pattern_options(parser, pattern_required=False)
    add_common_options(parser)
    parser.set_defaults(handler=run_rename)


def run_rename(args: argparse.Namespace, out: Output) -> int:
    pattern = args.pattern or DEFAULT_RENAME_PATTERN
    files = collect(args.paths, out, recurse=not args.no_recurse)
    books = load_books(files)
    taken: set[str] = set()
    renamed: list[tuple[str, str]] = []
    failed = 0
    for index, book in enumerate(books, start=1):
        out.progress(index, len(books), book.path)
        row = _row(book.path)
        if book.load_error or book.needs_conversion:
            row["status"], row["message"] = "skipped", book.load_error or "needs conversion first"
        else:
            # fallback="": a file the pattern has nothing for is skipped, not renamed to "untitled"
            stem = render_filename(_values_for(book, args.zero_pad), pattern, fallback="", ascii_only=args.ascii)
            if not stem.strip():
                row["status"], row["message"] = "skipped", "the pattern gives an empty name for this file"
            else:
                directory = os.path.dirname(book.path)
                new_path = unique_path(directory, stem, os.path.splitext(book.path)[1], taken, book.path)
                taken.add(os.path.normcase(os.path.abspath(new_path)))
                row["new_path"] = new_path
                if os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(os.path.abspath(book.path)):
                    row["status"], row["new_path"] = "unchanged", ""
                elif args.dry_run:
                    row["status"] = "planned"
                else:
                    try:
                        os.rename(book.path, new_path)
                    except OSError as exc:
                        row["status"], row["message"], row["new_path"] = "failed", str(exc), ""
                        failed += 1
                    else:
                        row["status"] = "renamed"
                        renamed.append((book.path, new_path))
        out.record(row)
        _say(out, row)
    if renamed:
        rename_log().record("Rename by Pattern (command line)", renamed)
    out.finish({"files": len(books), "failed": failed, "dry_run": args.dry_run, "pattern": pattern})
    return EXIT_PARTIAL if failed else EXIT_OK


# --- move ---------------------------------------------------------------------------


def add_move_parser(sub) -> None:
    parser = sub.add_parser(
        "move", help="move files into folders under a library root",
        description="Move (or copy) each comic to <root>/<pattern>, the pattern may contain / to make sub-folders, "
                    "e.g. \"%publisher%/%series%/%series% %number%\". Never overwrites.",
    )
    add_path_arguments(parser)
    _add_pattern_options(parser, pattern_required=True)
    parser.add_argument("--root", metavar="FOLDER", help="the library folder (default: the one saved in the app)")
    parser.add_argument("--copy", action="store_true", help="copy instead of move, leaving the originals")
    add_common_options(parser)
    parser.set_defaults(handler=run_move)


def run_move(args: argparse.Namespace, out: Output) -> int:
    root = args.root or app_settings.load_library_root()
    if not root:
        raise CliError("no library folder: give --root FOLDER (or set one in the app's Rename / Export / Move window)")
    if not os.path.isdir(root):
        raise CliError(f"the library folder does not exist: {root}")
    files = collect(args.paths, out, recurse=not args.no_recurse)
    books = [b for b in load_books(files)]
    movable = [b for b in books if not b.load_error and not b.needs_conversion]
    plans = plan_moves(
        movable, root, args.pattern, lambda b: _values_for(b, args.zero_pad), lambda b: b.path,
        copy=args.copy, ascii_only=args.ascii,
    )
    by_path = {p.old_path: p for p in plans}
    failed = 0
    log = rename_log()
    for index, book in enumerate(books, start=1):
        out.progress(index, len(books), book.path)
        row = _row(book.path)
        plan = by_path.get(book.path)
        if plan is None:
            row["status"], row["message"] = "skipped", book.load_error or "needs conversion first"
        elif plan.blocking:
            row["status"], row["message"] = "failed", plan.warning
            failed += 1
        elif plan.is_noop:
            row["status"] = "unchanged"
        elif "empty" in plan.warning:  # the planner would call it "untitled"; a batch should not do that silently
            row["status"], row["message"] = "skipped", "the pattern gives an empty name for this file"
        else:
            row["new_path"] = plan.new_path
            if plan.warning:
                row["message"] = plan.warning
            if args.dry_run:
                row["status"] = "planned"
            else:
                try:
                    result = execute_move(plan.old_path, plan.new_path, copy=args.copy, trash=move_to_trash)
                except (OSError, ValueError) as exc:
                    row["status"], row["message"] = "failed", str(exc)
                    failed += 1
                else:
                    row["status"] = "copied" if args.copy else "moved"
                    if result.warning:
                        row["message"] = result.warning
                    if not args.copy and not result.original_kept:
                        trashed = [(plan.old_path, result.new_path)] if result.original_trashed else []
                        log.record(
                            "Move into folders (command line)", [(plan.old_path, result.new_path)],
                            created_dirs=result.created_dirs, trashed=trashed, root=plan.root,
                        )
        out.record(row)
        _say(out, row)
    out.finish({"files": len(books), "failed": failed, "dry_run": args.dry_run, "root": root})
    return EXIT_PARTIAL if failed else EXIT_OK
