"""Entry point of the command-line tool: `python cbzredactor_cli.py COMMAND ...` (the release builds
cbzredactor-cli.exe from it). See cbzcli/main.py."""

import sys

from cbzcli.main import main
from redactor_common.cli import run

if __name__ == "__main__":
    sys.exit(run(main))
