"""
cbzcli/files.py

Finding and loading the comics a command works on, and the small shared helpers (the app's own
settings and files, read without any window).
"""

from __future__ import annotations


from core.cbz_file import CbzBook
from core.foreign_archive_convert import FOREIGN_ARCHIVE_EXTENSIONS
from redactor_common.cli import CliError, Output, expand_paths

EXTENSIONS = (".cbz",) + FOREIGN_ARCHIVE_EXTENSIONS


def add_path_arguments(parser) -> None:
    parser.add_argument("paths", nargs="+", metavar="PATH", help="comic files, folders or wildcards")
    parser.add_argument(
        "-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it"
    )


def collect(paths: list[str], out: Output, recurse: bool = True) -> list[str]:
    """The comic files the arguments name. An argument that matches nothing is an error; if that leaves no
    files at all the command ends with a usage error."""
    files, missing = expand_paths(paths, EXTENSIONS, recursive=recurse)
    for argument in missing:
        out.error(f"nothing found for {argument}")
    if not files:
        raise CliError("no comic files found")
    return files


def load_books(files: list[str], out: Output | None = None) -> list[CbzBook]:
    """Loads each file; `out` shows "reading N/M" on stderr while a big batch is read."""
    books = []
    for index, path in enumerate(files, start=1):
        if out is not None:
            out.progress(index, len(files), f"reading {path}")
        books.append(CbzBook(path))
    return books


def book_status(book: CbzBook) -> str:
    """"ok", "needs conversion" or the load error."""
    if book.load_error:
        return book.load_error
    if book.needs_conversion:
        return "needs conversion"
    return "ok"
