"""
cbzcli/cmd_redact.py

  redact PATH...   run the Redact recipe on the comics: the same steps as Operations > Redact in the app
                   (convert, clean up, remove credit pages, resize, fill from the filename and path,
                   lookups, validate and fix, tag, rename, move), saved in place with each original in the
                   Recycle Bin (or moved to --trash-dir).

The recipe is the one saved in the app (Operations > Edit Redact Recipe) unless --recipe FILE names a
JSON recipe; --enable / --disable / --threshold adjust it for this run only. A Comic Vine key comes from
the COMICVINE_API_KEY environment variable, else the app's saved one. The running of the recipe and the
report are the shared ones in redactor_common.cli.commands.
"""

from __future__ import annotations

import argparse
import os

from core.credit_pages import KnownCreditPages
from core.redact_steps import (
    FINALIZE_LABEL, CbzCtx, RedactEnv, build_catalogue, recipe_for_run, recipe_from_setting, run_catalogue, save_stage,
)
from gui import app_settings
from redactor_common.cli import CliError, Output, add_common_options
from redactor_common.cli.commands import (
    add_redact_options, build_recipe, list_steps, read_recipe_file, redact_items, trash_to,
)

from cbzcli.files import collect, load_books


def add_redact_parser(sub) -> None:
    parser = sub.add_parser(
        "redact", help="run the Redact recipe",
        description="Run the Redact recipe on the comics. Each file is saved in place and its original goes to the "
                    "Recycle Bin (or --trash-dir). Guesses below the confidence threshold are listed, not applied.",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH", help="comic files, folders or wildcards")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it")
    add_redact_options(parser)
    add_common_options(parser)
    parser.set_defaults(handler=run_redact)


def build_env(args: argparse.Namespace) -> RedactEnv:
    """What the steps share, read from the app's settings like the window does -- minus anything visual."""
    resize = app_settings.load_resize_options() if app_settings.load_resize_on_convert() == app_settings.RESIZE_ON_CONVERT_YES else None
    return RedactEnv(
        rename_log=None,
        trash=trash_to(args.trash_dir) if args.trash_dir else None,
        pattern_history=app_settings.load_pattern_history(),
        ascii_filenames=app_settings.load_ascii_filenames(),
        zero_pad=app_settings.load_rename_zero_pad(),
        library_root=app_settings.load_library_root(),
        comicvine_key=os.environ.get("COMICVINE_API_KEY", "").strip() or app_settings.load_comicvine_api_key(),
        local_databases=[
            p for p in (app_settings.load_gcd_local_database(), app_settings.load_comicrack_database()) if p
        ],
        known_credits=KnownCreditPages(app_settings.credit_pages_path()),
        convert_resize=resize,
    )


def run_redact(args: argparse.Namespace, out: Output) -> int:
    env = build_env(args)
    catalogue = build_catalogue(env)
    text = read_recipe_file(args.recipe) if args.recipe else app_settings.load_redact_recipe()
    recipe = build_recipe(args, recipe_from_setting(text), catalogue)
    if args.list_steps:
        return list_steps(recipe, catalogue, out)
    if not args.paths:
        raise CliError("give the comics to redact (or --list-steps)")

    books = load_books(collect(args.paths, out, recurse=not args.no_recurse))
    env.begin()
    return redact_items(
        books, recipe_for_run(recipe), run_catalogue(env), make_context=lambda book: CbzCtx(book, env),
        describe=lambda book: os.path.basename(book.path), finalize=save_stage, finalize_label=FINALIZE_LABEL,
        path_of=lambda book: book.path, out=out,
    )
