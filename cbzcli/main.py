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

from core.version import APP_VERSION
from redactor_common.cli.main_factory import make_main

from cbzcli import COMMANDS, cmd_files, cmd_redact, cmd_tags

PROG = "cbzredactor"

build_parser, main = make_main(
    PROG,
    APP_VERSION,
    "The CBZ Redactor on the command line: inspect, fix, convert, rename and redact comics.",
    [
        cmd_tags.add_info_parser,
        cmd_tags.add_set_parser,
        cmd_files.add_convert_parser,
        cmd_files.add_rename_parser,
        cmd_files.add_move_parser,
        cmd_redact.add_redact_parser,
    ],
    commands=COMMANDS,
)
