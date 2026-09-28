"""
core/trash.py

Removing a converted original: sent to the Recycle Bin (Trash on
Linux/Mac) via send2trash, never permanently deleted -- with automatic
conversion there's otherwise no way back if a conversion went wrong in
a way the checks didn't catch.
"""

from __future__ import annotations


class TrashError(Exception):
    pass


def move_to_trash(path: str) -> None:
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise TrashError(
            "the 'send2trash' package isn't installed (pip install send2trash), "
            "so the original was kept"
        ) from exc
    try:
        send2trash(path)
    except OSError as exc:
        raise TrashError(f"couldn't move it to the Recycle Bin: {exc}") from exc
