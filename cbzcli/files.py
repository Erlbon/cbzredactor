"""
cbzcli/files.py

Finding and loading the comics a command works on, and the small shared helpers (the app's own
settings and files, read without any window).
"""

from __future__ import annotations

import os

from core.app_paths import base_dir
from core.cbz_file import CbzBook
from core.foreign_archive_convert import FOREIGN_ARCHIVE_EXTENSIONS
from redactor_common.cli import CliError, Output, expand_paths
from redactor_common.core.rename_log import RenameLog

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


def load_books(files: list[str]) -> list[CbzBook]:
    return [CbzBook(path) for path in files]


def book_status(book: CbzBook) -> str:
    """"ok", "needs conversion" or the load error."""
    if book.load_error:
        return book.load_error
    if book.needs_conversion:
        return "needs conversion"
    return "ok"


def rename_log() -> RenameLog:
    """The same log File > Undo Last Rename reads, so a rename done here can be undone from the app."""
    return RenameLog(os.path.join(str(base_dir()), "cbzredactor_rename_log.json"))
