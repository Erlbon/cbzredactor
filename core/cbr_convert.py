"""
core/cbr_convert.py

CBR (RAR-based comic archive) -> CBZ conversion. RAR support is
read-only, and exists only to convert away from CBR immediately -- this
app never writes RAR and never edits a .cbr in place (see project
README, "CBR support": RAR5's compression is a proprietary format that
can't be safely round-tripped through the way ZIP can, so a CBR is
converted once, up front, and edited as the resulting CBZ from then on).

Uses the optional `rarfile` package, which itself shells out to a real
unrar/unar/bsdtar-compatible binary found on PATH -- none of which
ships with this tool yet. On a machine without one installed,
conversion fails with a clear, actionable error rather than a cryptic
traceback. Bundling a portable Windows binary (the same approach as
the keyfinder-cli-windows repo takes for FFmpeg) is the likely fix once
this is needed for real -- tracked in the README, not solved here.
"""

from __future__ import annotations

import os
import zipfile
from typing import Optional


class CbrConversionError(Exception):
    """Raised when a CBR file can't be converted -- either the archive
    itself is bad, or no RAR-reading tool is available on this machine."""


def convert_cbr_to_cbz(cbr_path: str, output_path: Optional[str] = None) -> str:
    """Converts a .cbr file to a .cbz at `output_path` (default: same
    name/location with a .cbz extension) -- the original .cbr is left
    untouched. Returns the path written.

    Raises CbrConversionError, with a specific and actionable message
    for each way this can fail, rather than letting a raw rarfile/
    zipfile exception surface unexplained:
    - the optional `rarfile` package isn't installed
    - no unrar/unar/bsdtar binary is on PATH for rarfile to shell out to
    - the archive itself is unreadable (corrupt, or actually password
      protected)
    """
    try:
        import rarfile
    except ImportError as exc:
        raise CbrConversionError(
            "CBR support needs the optional 'rarfile' package, which "
            "isn't installed. Run: pip install rarfile"
        ) from exc

    if output_path is None:
        output_path = os.path.splitext(cbr_path)[0] + ".cbz"

    try:
        with rarfile.RarFile(cbr_path) as rf:
            infos = [i for i in rf.infolist() if not i.is_dir()]
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for info in infos:
                    zf.writestr(info.filename, rf.read(info))
    except rarfile.RarCannotExec as exc:
        raise CbrConversionError(
            "No RAR-reading tool (unrar, unar, or bsdtar) was found on "
            "PATH. Install one -- e.g. WinRAR or 7-Zip's unrar.exe -- "
            "and try again. See the project README's 'CBR support' "
            "section."
        ) from exc
    except rarfile.Error as exc:
        raise CbrConversionError(f"Could not read CBR file: {exc}") from exc
    except OSError as exc:
        raise CbrConversionError(f"Could not write CBZ file: {exc}") from exc

    return output_path
