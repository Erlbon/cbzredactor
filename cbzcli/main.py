"""
cbzcli/main.py

The cbzredactor command line:

    cbzredactor-cli info    PATH...                      what the comics are
    cbzredactor-cli set     PATH... -s Series=Saga       change ComicInfo fields
    cbzredactor-cli convert PATH...                      CBR/CB7/CBT -> CBZ
    cbzredactor-cli rename  PATH... -p "%series% %number%"
    cbzredactor-cli move    PATH... -p "%publisher%/%series%/..." --root LIBRARY
    cbzredactor-cli redact  PATH...                      run the saved Redact recipe

Every command takes --json (one JSON document on stdout) and --quiet, and the ones that change files take
--dry-run. Exit codes: 0 done, 1 some files failed, 2 bad arguments, 130 interrupted.
It reads the same settings file as the app (Tools > Preferences, the saved Redact recipe, the API keys).
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from core.version import APP_VERSION
from redactor_common.cli import EXIT_USAGE, Output

from cbzcli import cmd_files, cmd_redact, cmd_tags

PROG = "cbzredactor-cli"


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Output(json_mode=getattr(args, "json", False), quiet=getattr(args, "quiet", False))
    return args.handler(args, out)


if __name__ == "__main__":
    from redactor_common.cli import run

    sys.exit(run(main))
