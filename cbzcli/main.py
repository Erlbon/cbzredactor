"""
cbzcli/main.py

The cbzredactor command line, run through the app's own exe (main.py dispatches here when its first
argument is a command; the window never starts):

    cbzredactor info    PATH...                      what the comics are
    cbzredactor set     PATH... -s Series=Saga       change ComicInfo fields
    cbzredactor convert PATH...                      CBR/CB7/CBT -> CBZ
    cbzredactor rename  PATH... -p "%series% %number%"
    cbzredactor move    PATH... -p "%publisher%/%series%/..." --root LIBRARY
    cbzredactor redact  PATH...                      run the saved Redact recipe

Every command takes --json (one JSON document), --quiet and --output FILE (the result goes to a file: the
reliable way to read it from a script, since a windowed exe cannot be waited for by an interactive shell),
and the ones that change files take --dry-run. Exit codes: 0 done, 1 some files failed, 2 bad arguments,
70 internal error, 130 interrupted. It reads the same settings file as the app (Tools > Preferences, the
saved Redact recipe, the API keys).
"""

from __future__ import annotations

import argparse
from typing import Sequence

from core.version import APP_VERSION
from redactor_common.cli import make_output

from cbzcli import COMMANDS, cmd_files, cmd_redact, cmd_tags

PROG = "cbzredactor"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG, description="The CBZ Redactor on the command line: inspect, fix, convert, rename and redact comics.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {APP_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    cmd_tags.add_info_parser(sub)
    cmd_tags.add_set_parser(sub)
    cmd_files.add_convert_parser(sub)
    cmd_files.add_rename_parser(sub)
    cmd_files.add_move_parser(sub)
    cmd_redact.add_redact_parser(sub)
    assert tuple(sub.choices) == COMMANDS, "cbzcli.COMMANDS must list the subcommands"
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = make_output(args)
    try:
        return args.handler(args, out)
    finally:
        out.close()
