"""
cbzcli/cmd_files.py

  convert PATH...   CBR/CB7/CBT (or a mislabeled .cbz) to a real .cbz
  rename  PATH...   rename by a metadata pattern ("%series% %number% - %title%")
  move    PATH...   move (or copy) into folders under a library root by a pattern ("%publisher%/%series%/...")

Rename and move are the shared implementations in redactor_common.cli.commands; this file only says how a
comic's fields and path are read. Renames and moves are recorded in the same log the app's File > Undo Last
Rename reads.
"""

from __future__ import annotations

import argparse
import os

from core.cbz_file import CbzBook, path_needs_conversion
from core.foreign_archive_convert import ForeignArchiveConversionError, convert_to_cbz, relabel_mislabeled_cbz
from core.redact_steps import FILENAME_FIELD_KEYS
from gui import app_settings
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, Output, add_common_options, commands
from redactor_common.cli.commands import add_pattern_options, new_row, say
from redactor_common.core.rename_pattern import zero_pad_numeric_value
from redactor_common.core.trash import TrashError, move_to_trash

from cbzcli.files import add_path_arguments, collect, load_books, rename_log

DEFAULT_RENAME_PATTERN = "%series% %number% - %title%"


def _values_for(book: CbzBook, zero_pad: int) -> dict[str, str]:
    values = {attr: getattr(book.metadata, attr, "") or "" for attr in FILENAME_FIELD_KEYS}
    if zero_pad > 0 and values.get("number"):
        values["number"] = zero_pad_numeric_value(values["number"], zero_pad)
    if values.get("month"):
        values["month"] = zero_pad_numeric_value(values["month"], 2)
    return values


def _skip_reason(book: CbzBook) -> str:
    return book.load_error or ("needs conversion first" if book.needs_conversion else "")


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
        row = new_row(path)
        _convert_one(path, args, resize, row, out)
        failed += row["status"] == "failed"
        out.record(row)
        say(out, row)
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
    except (ForeignArchiveConversionError, OSError) as exc:
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
    add_pattern_options(parser, pattern_required=False)
    add_common_options(parser)
    parser.set_defaults(handler=run_rename)


def run_rename(args: argparse.Namespace, out: Output) -> int:
    pattern = args.pattern or DEFAULT_RENAME_PATTERN
    books = load_books(collect(args.paths, out, recurse=not args.no_recurse))
    failed = commands.rename_items(
        books, pattern=pattern, values_for=lambda b: _values_for(b, args.zero_pad), path_of=lambda b: b.path,
        skip_reason=_skip_reason, out=out, dry_run=args.dry_run, ascii_only=args.ascii, log=rename_log(),
        log_label="Rename by Pattern (command line)",
    )
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
    add_pattern_options(parser, pattern_required=True)
    parser.add_argument("--root", metavar="FOLDER", help="the library folder (default: the one saved in the app)")
    parser.add_argument("--copy", action="store_true", help="copy instead of move, leaving the originals")
    add_common_options(parser)
    parser.set_defaults(handler=run_move)


def run_move(args: argparse.Namespace, out: Output) -> int:
    root = args.root or app_settings.load_library_root()
    books = load_books(collect(args.paths, out, recurse=not args.no_recurse))
    failed = commands.move_items(
        books, root=root, pattern=args.pattern, values_for=lambda b: _values_for(b, args.zero_pad),
        path_of=lambda b: b.path, skip_reason=_skip_reason, out=out, dry_run=args.dry_run, copy=args.copy,
        ascii_only=args.ascii, log=rename_log(), log_label="Move into folders (command line)",
    )
    out.finish({"files": len(books), "failed": failed, "dry_run": args.dry_run, "root": root})
    return EXIT_PARTIAL if failed else EXIT_OK
