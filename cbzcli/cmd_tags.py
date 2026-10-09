"""
cbzcli/cmd_tags.py

  info PATH...   what each comic is: format, pages, and its ComicInfo fields
  set  PATH...   change ComicInfo fields (-s Series=Saga -s Number=3, --clear Notes), saved in place
"""

from __future__ import annotations

import argparse

from core.cbz_file import CbzBook
from core.comicinfo import TAG_TO_ATTR
from core.zip_rewrite import CbzError
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options

from cbzcli.fields import DEFAULT_INFO_FIELDS, attr_for, parse_assignment, resolve_field
from cbzcli.files import add_path_arguments, book_status, collect, load_books

ALL_ATTRS = [attr for tag, attr in TAG_TO_ATTR.items() if tag != "PageCount"]


# --- info ---------------------------------------------------------------------------


def add_info_parser(sub) -> None:
    parser = sub.add_parser("info", help="show what the comics are", description="Show each comic's format, page count and fields.")
    add_path_arguments(parser)
    parser.add_argument("--fields", metavar="LIST", help="comma-separated fields to show (default: series, number, title, year, publisher)")
    parser.add_argument("--all", action="store_true", help="show every field that has a value")
    add_common_options(parser)
    parser.set_defaults(handler=run_info)


def run_info(args: argparse.Namespace, out: Output) -> int:
    files = collect(args.paths, out, recurse=not args.no_recurse)
    wanted = None if args.all else (
        [attr_for(name) for name in args.fields.split(",") if name.strip()] if args.fields else DEFAULT_INFO_FIELDS
    )
    failed = 0
    for index, book in enumerate(load_books(files), start=1):
        out.progress(index, len(files), book.path)
        shown = ALL_ATTRS if wanted is None else wanted
        fields = {a: getattr(book.metadata, a, "") for a in shown if (getattr(book.metadata, a, "") or "").strip()}
        status = book_status(book)
        if status != "ok" and not book.needs_conversion:
            failed += 1
        row = {
            "path": book.path, "format": book.container, "pages": book.actual_page_count,
            "status": status, "has_comicinfo": book.comicinfo_name is not None, "fields": fields,
        }
        out.record(row)
        out.line(f"{book.path}  [{book.container}, {book.actual_page_count} pages, {status}]")
        for attr, value in fields.items():
            out.line(f"  {attr}: {value}")
    out.finish({"files": len(files), "failed": failed})
    return EXIT_PARTIAL if failed else EXIT_OK


# --- set ----------------------------------------------------------------------------


def add_set_parser(sub) -> None:
    parser = sub.add_parser(
        "set", help="change ComicInfo fields",
        description="Set or clear ComicInfo fields and save each comic in place (a CBR/CB7/CBT must be converted first).",
    )
    add_path_arguments(parser)
    parser.add_argument("-s", "--set", dest="assignments", action="append", default=[], metavar="FIELD=VALUE",
                        help="set a field (repeat for several), e.g. -s Series=Saga -s Number=3")
    parser.add_argument("--clear", action="append", default=[], metavar="FIELD", help="empty a field (repeatable)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show the changes, write nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_set)


def run_set(args: argparse.Namespace, out: Output) -> int:
    changes: dict[str, str] = {}
    for text in args.assignments:
        _tag, attr, value = parse_assignment(text)
        changes[attr] = value
    for name in args.clear:
        _tag, attr = resolve_field(name, settable=True)
        changes[attr] = ""
    if not changes:
        raise CliError("nothing to change: give at least one -s FIELD=VALUE or --clear FIELD")

    files = collect(args.paths, out, recurse=not args.no_recurse)
    failed = 0
    for index, book in enumerate(load_books(files), start=1):
        out.progress(index, len(files), book.path)
        row = {"path": book.path, "status": "", "changes": {}, "message": ""}
        if book.load_error or book.needs_conversion:
            row["status"], row["message"] = "failed", book_status(book)
            failed += 1
        else:
            for attr, new in changes.items():
                old = getattr(book.metadata, attr, "") or ""
                if old != new:
                    row["changes"][attr] = {"old": old, "new": new}
            if not row["changes"]:
                row["status"] = "unchanged"
            elif args.dry_run:
                row["status"] = "planned"
            else:
                row["status"] = _save(book, changes, row)
                failed += row["status"] == "failed"
        out.record(row)
        out.line(f"{row['status']:9} {book.path}" + (f"  ({row['message']})" if row["message"] else ""))
        for attr, change in row["changes"].items():
            out.line(f"          {attr}: {change['old']!r} -> {change['new']!r}")
    out.finish({"files": len(files), "failed": failed, "dry_run": args.dry_run})
    return EXIT_PARTIAL if failed else EXIT_OK


def _save(book: CbzBook, changes: dict[str, str], row: dict) -> str:
    for attr, value in changes.items():
        setattr(book.metadata, attr, value)
    try:
        book.save()
    except CbzError as exc:
        row["message"] = str(exc)
        return "failed"
    return "changed"
