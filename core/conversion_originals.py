"""
core/conversion_originals.py

What happens to the ORIGINAL of a converted CBR/CBT/CB7 (or mislabeled archive), per the shared conversion policy
(redactor_common.core.conversion_policy). Qt-free.

Hard rules: media is never deleted. The caller disposes of an original only AFTER the conversion was verified
(convert_to_cbz opens the result as a valid CBZ with the same page count before it returns).

  quarantine  the original is MOVED into "converted originals <date>", under the set quarantine folder, else beside
              the file; a TSV undo log (UTF-8, one line per change, written right after the change succeeded) lists
              "<new path><TAB>CREATED" for the .cbz and "<new path><TAB><old path>" for the move, so undoing the lines
              in reverse order restores everything.
  recycle     the Recycle Bin (redactor_common.core.trash.move_to_trash, or the caller's retrying wrapper).
  keep        nothing happens; the .cbz has another extension, so the original can always stay.

legacy_policy() is what the policy is for a user who never saved the Preferences > Conversion page: the two old
settings (load behaviour, "Recycle Bin") mapped one to one, so nothing changes silently.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import date
from typing import Callable

from redactor_common.core import conversion_policy as policy_mod
from redactor_common.core.conversion_policy import ConversionPolicy

QUARANTINE_PREFIX = "converted originals"

# The old load behaviours (gui/app_settings.py FOREIGN_LOAD_*) as policy on_open values.
LEGACY_ON_OPEN = {"ask": policy_mod.ON_OPEN_ASK, "convert": policy_mod.ON_OPEN_AUTO, "unconverted": policy_mod.ON_OPEN_NEVER}
LEGACY_LOAD_BEHAVIOR = {value: key for key, value in LEGACY_ON_OPEN.items()}


def legacy_policy(load_behavior: str, recycle_originals: bool) -> ConversionPolicy:
    """The policy that equals the old settings: no quarantine folder (the old code never had one), no recent-file
    rule and no dry-run offer (neither existed)."""
    return ConversionPolicy(
        on_open=LEGACY_ON_OPEN.get(load_behavior, policy_mod.ON_OPEN_NEVER),
        originals=policy_mod.ORIGINALS_RECYCLE if recycle_originals else policy_mod.ORIGINALS_KEEP,
        quarantine_folder="", undo_log_folder="", recent_minutes=0, dry_run_first=False,
    )


def normalise_originals(value) -> str:
    """A policy `originals` value from what callers pass: the old bool (True = Recycle Bin, False = keep) or a mode."""
    if value is True:
        return policy_mod.ORIGINALS_RECYCLE
    if value in (False, None):
        return policy_mod.ORIGINALS_KEEP
    return value if value in policy_mod.ORIGINALS_CHOICES else policy_mod.ORIGINALS_KEEP


def is_recent(path: str, minutes: int, now: float | None = None) -> bool:
    """Modified in the last `minutes` (0 = the rule is off)."""
    if minutes <= 0:
        return False
    try:
        age = (time.time() if now is None else now) - os.path.getmtime(path)
    except OSError:
        return False
    return age < minutes * 60


def quarantine_dir(original: str, policy: ConversionPolicy, today: date | None = None) -> str:
    root = policy.quarantine_folder or os.path.dirname(os.path.abspath(original))
    return os.path.join(root, f"{QUARANTINE_PREFIX} {(today or date.today()).isoformat()}")


def undo_log_path(original: str, policy: ConversionPolicy, today: date | None = None) -> str:
    folder = policy.undo_log_folder or os.path.dirname(quarantine_dir(original, policy, today))
    return os.path.join(folder, f"{QUARANTINE_PREFIX} {(today or date.today()).isoformat()} undo.tsv")


def _free_name(folder: str, name: str) -> str:
    target = os.path.join(folder, name)
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.lexists(target):
        target = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return target


def _log(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(line + "\n" for line in lines))


def dispose_original(
    original: str, new_path: str, originals, policy: ConversionPolicy, trash: Callable[[str], None],
    today: date | None = None,
) -> str:
    """Applies `originals` to the verified-converted `original`. Returns a short description of what was done
    ("kept", "moved to the Recycle Bin", "moved to <folder>"). Raises OSError / the trash's error when the original
    could not be moved; the .cbz is then still fine and the original is still where it was."""
    mode = normalise_originals(originals)
    if mode == policy_mod.ORIGINALS_KEEP:
        return "kept"
    if mode == policy_mod.ORIGINALS_RECYCLE:
        trash(original)
        return "moved to the Recycle Bin"
    folder = quarantine_dir(original, policy, today)
    os.makedirs(folder, exist_ok=True)
    target = _free_name(folder, os.path.basename(original))
    shutil.move(original, target)
    try:
        _log(undo_log_path(original, policy, today), [f"{new_path}\tCREATED", f"{target}\t{original}"])
    except OSError:
        pass  # the move itself succeeded; a log that cannot be written must not undo it
    return f"moved to {folder}"


def plan_line(original: str, policy: ConversionPolicy, originals=None, today: date | None = None) -> str:
    """One dry-run line: what a real run would do with this file."""
    target = os.path.splitext(original)[0] + ".cbz"
    mode = normalise_originals(policy.originals if originals is None else originals)
    if mode == policy_mod.ORIGINALS_KEEP:
        after = "the original stays where it is"
    elif mode == policy_mod.ORIGINALS_RECYCLE:
        after = "the original goes to the Recycle Bin"
    else:
        after = f"the original moves to {quarantine_dir(original, policy, today)}"
    return f"{os.path.basename(original)} -> {os.path.basename(target)}; {after}"
