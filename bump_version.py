#!/usr/bin/env python3
"""
bump_version.py

Maintainer utility -- run this immediately before packaging each
delivery, instead of hand-editing core/version.py's APP_VERSION string.

Behavior: if today's date matches what's already stored in
APP_VERSION, the counter increments (another build today). If it's a
new day, the counter resets to #01. Either way, the date portion comes
from the system clock, never typed by hand.

Usage:
    python3 bump_version.py
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "core" / "version.py"
VERSION_PATTERN = re.compile(r'APP_VERSION = "(\d{4}-\d{2}-\d{2})#(\d+)"')


def main() -> None:
    today = datetime.date.today().isoformat()  # from the actual system clock, not typed by hand

    text = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(text)
    if not match:
        print(f"ERROR: could not find APP_VERSION in {VERSION_FILE}", file=sys.stderr)
        sys.exit(1)

    old_date, old_counter = match.group(1), int(match.group(2))
    if old_date == today:
        new_counter = old_counter + 1
    else:
        new_counter = 1

    new_version = f"{today}#{new_counter:02d}"
    new_text = VERSION_PATTERN.sub(f'APP_VERSION = "{new_version}"', text, count=1)
    VERSION_FILE.write_text(new_text, encoding="utf-8")
    print(f"APP_VERSION bumped: {old_date}#{old_counter:02d} -> {new_version}")


if __name__ == "__main__":
    main()
