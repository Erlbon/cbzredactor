"""
cbzcli/cmd_redact.py

  redact PATH...   run the Redact recipe on the comics: the same steps as Operations > Redact in the app
                   (convert, clean up, remove credit pages, resize, fill from the filename and path,
                   lookups, validate and fix, tag, rename, move), saved in place with each original in the
                   Recycle Bin (or moved to --trash-dir).

The recipe is the one saved in the app (Operations > Edit Redact Recipe) unless --recipe FILE names a
JSON recipe; --enable / --disable / --threshold adjust it for this run only. A Comic Vine key comes from
the COMICVINE_API_KEY environment variable, else the app's saved one.
"""

from __future__ import annotations

import argparse
import os
import shutil

from core.cbz_file import CbzBook
from core.credit_pages import KnownCreditPages
from core.redact_steps import (
    FINALIZE_LABEL, CbzCtx, RedactEnv, build_catalogue, recipe_for_run, recipe_from_setting, run_catalogue, save_stage,
)
from gui import app_settings
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options
from redactor_common.core.pipeline import FileStatus, run_recipe

from cbzcli.files import add_path_arguments, collect, load_books, rename_log


def add_redact_parser(sub) -> None:
    parser = sub.add_parser(
        "redact", help="run the Redact recipe",
        description="Run the Redact recipe on the comics. Each file is saved in place and its original goes to the "
                    "Recycle Bin (or --trash-dir). Guesses below the confidence threshold are listed, not applied.",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH", help="comic files, folders or wildcards")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it")
    parser.add_argument("--recipe", metavar="FILE", help="a recipe JSON file (default: the one saved in the app)")
    parser.add_argument("--enable", action="append", default=[], metavar="STEP", help="turn a step on for this run (repeatable)")
    parser.add_argument("--disable", action="append", default=[], metavar="STEP", help="turn a step off for this run (repeatable)")
    parser.add_argument("--threshold", type=float, metavar="N", help="confidence needed to apply a guess, 0-1 (or a percentage)")
    parser.add_argument("--list-steps", action="store_true", help="show the steps and whether the recipe has them on, then stop")
    parser.add_argument("--trash-dir", metavar="FOLDER", help="move originals here instead of the Recycle Bin")
    add_common_options(parser)
    parser.set_defaults(handler=run_redact)


def _trash_to(folder: str):
    """A trash function that moves a file into `folder` (kept, numbered if the name is taken)."""
    os.makedirs(folder, exist_ok=True)

    def trash(path: str) -> None:
        stem, ext = os.path.splitext(os.path.basename(path))
        target, n = os.path.join(folder, stem + ext), 2
        while os.path.lexists(target):
            target = os.path.join(folder, f"{stem} ({n}){ext}")
            n += 1
        shutil.move(path, target)

    return trash


def build_env(args: argparse.Namespace) -> RedactEnv:
    """What the steps share, read from the app's settings like the window does -- minus anything visual."""
    resize = app_settings.load_resize_options() if app_settings.load_resize_on_convert() == app_settings.RESIZE_ON_CONVERT_YES else None
    return RedactEnv(
        rename_log=rename_log(),
        trash=_trash_to(args.trash_dir) if args.trash_dir else None,
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


def _recipe_for(args: argparse.Namespace, env: RedactEnv):
    catalogue = build_catalogue(env)
    if args.recipe:
        try:
            with open(args.recipe, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise CliError(f"cannot read the recipe file: {exc}") from exc
    else:
        text = app_settings.load_redact_recipe()
    recipe = recipe_from_setting(text)
    keys = {step.key for step in catalogue if not step.hidden}
    for key in args.enable + args.disable:
        if key not in keys:
            raise CliError(f"unknown step {key!r}. Steps: {', '.join(sorted(keys))}")
    for key in args.enable:
        recipe.enabled[key] = True
    for key in args.disable:
        recipe.enabled[key] = False
    if args.threshold is not None:
        value = args.threshold / 100 if args.threshold > 1 else args.threshold
        if not 0 <= value <= 1:
            raise CliError("--threshold must be between 0 and 1 (or 0 and 100)")
        recipe.confidence_threshold = value
    return recipe, catalogue


def run_redact(args: argparse.Namespace, out: Output) -> int:
    env = build_env(args)
    recipe, catalogue = _recipe_for(args, env)
    if args.list_steps:
        resolved = {step.key for step, _opts in recipe.resolve(catalogue)}
        for step in catalogue:
            if step.hidden:
                continue
            on = step.key in resolved
            out.record({"step": step.key, "label": step.label, "enabled": on})
            out.line(f"{'on ' if on else 'off'}  {step.key:22} {step.label}")
        out.finish({"confidence_threshold": recipe.confidence_threshold})
        return EXIT_OK
    if not args.paths:
        raise CliError("give the comics to redact (or --list-steps)")

    files = collect(args.paths, out, recurse=not args.no_recurse)
    books = load_books(files)
    env.begin()

    def progress(done: int, total: int, item) -> None:
        if item is not None:
            out.progress(done + 1, total, os.path.basename(item.path))

    report = run_recipe(
        books, recipe_for_run(recipe), run_catalogue(env), lambda book: CbzCtx(book, env),
        progress=progress, describe=lambda book: os.path.basename(book.path),
        finalize=save_stage, finalize_label=FINALIZE_LABEL,
    )
    for entry in report.entries:
        out.record({
            "file": entry.file, "path": entry.saved_path or (entry.item.path if isinstance(entry.item, CbzBook) else ""),
            "status": entry.status.value, "applied": entry.applied, "needs_review": [
                {"step": r.step_label, "value": r.value, "confidence": r.confidence, "reason": r.reason}
                for r in entry.review
            ], "failures": entry.failures, "notes": entry.notes, "skipped": entry.skips, "not_saved": entry.not_saved,
        })
    failed = report.count(FileStatus.FAILED) + report.count(FileStatus.ABORTED)
    if not out.json_mode:
        out.line(report.to_text())
    out.finish({
        "files": len(report.entries), "failed": failed, "needs_review": len(report.needs_review()),
        "cancelled": report.cancelled, "confidence_threshold": report.confidence_threshold, "run_notes": report.run_notes,
    })
    return EXIT_PARTIAL if failed else EXIT_OK
