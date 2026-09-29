"""
core/hathitrust.py

PROTOTYPE -- download full-view (public-domain) HathiTrust volumes as CBZ.

Only volumes the catalog marks "Full view" are ever fetched; limited /
search-only volumes are refused. No authentication is used or bypassed. HathiTrust's
babel.hathitrust.org sits behind a Cloudflare challenge for some clients: when
that happens we raise HathiBlockedError instead of trying to defeat it (the
supported routes for bulk access are the Data API with registered keys, and
the bulk datasets / rsync -- see the HathiTrust "Data API" and "Datasets"
pages).

Status: catalog lookup and CBZ assembly are unit-tested with fakes. The
babel page-image URL below has NOT been verified against the live site from
the build sandbox (it returned the Cloudflare challenge), so treat
PAGE_IMAGE_URL as unproven until run once on a real desktop.
"""

from __future__ import annotations

import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

CATALOG_BRIEF = "https://catalog.hathitrust.org/api/volumes/brief/{kind}/{value}.json"
PAGE_IMAGE_URL = "https://babel.hathitrust.org/cgi/imgsrv/image?id={htid}&seq={seq}&size=full"
USER_AGENT = "cbzredactor-hathitrust-prototype (+https://github.com/Erlbon/cbzredactor)"
MAX_PAGES = 5000
DELAY_SECONDS = 0.5  # be polite: one request at a time


class HathiError(Exception):
    pass


class HathiBlockedError(HathiError):
    """Cloudflare / bot challenge -- not bypassed."""


class NotFullViewError(HathiError):
    pass


@dataclass
class Volume:
    htid: str
    title: str
    rights: str  # e.g. "pd", "pdus", "ic"
    full_view: bool
    enumcron: str = ""


Fetcher = Callable[[str], bytes]


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("cf-mitigated") == "challenge":
            raise HathiBlockedError(
                "HathiTrust served a bot challenge; use the Data API or bulk datasets instead"
            ) from e
        raise


def parse_reference(text: str) -> tuple[str, str]:
    """Return (kind, value) for the catalog API: htid, oclc, isbn, lccn."""
    text = text.strip()
    if "hathitrust.org" in text:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        if q.get("id"):
            return "htid", q["id"][0]
        m = re.search(r"/Record/(\d+)", text)
        if m:
            return "recordnumber", m.group(1)
        raise HathiError(f"Cannot find a volume id in {text!r}")
    m = re.match(r"^(oclc|isbn|lccn|issn):\s*(\S+)$", text, re.I)
    if m:
        return m.group(1).lower(), m.group(2)
    if re.match(r"^[a-z0-9]+\.\S+$", text, re.I):
        return "htid", text
    if re.match(r"^\d{9,13}[\dXx]?$", text):
        return "isbn", text
    raise HathiError(f"Unrecognised HathiTrust reference: {text!r}")


def lookup(reference: str, fetch: Fetcher = _http_get) -> list[Volume]:
    import json

    kind, value = parse_reference(reference)
    url = CATALOG_BRIEF.format(kind=kind, value=urllib.parse.quote(value))
    data = json.loads(fetch(url).decode("utf-8"))
    titles = {rid: (r.get("titles") or [""])[0] for rid, r in data.get("records", {}).items()}
    volumes = []
    for item in data.get("items", []):
        rights_str = item.get("usRightsString", "")
        volumes.append(Volume(
            htid=item["htid"],
            title=titles.get(item.get("fromRecord", ""), ""),
            rights=item.get("rightsCode", ""),
            full_view=rights_str.lower().startswith("full view"),
            enumcron=item.get("enumcron") or "",
        ))
    return volumes


def download_cbz(volume: Volume, dest: str, fetch: Fetcher = _http_get,
                 progress: Optional[Callable[[int], bool]] = None,
                 delay: float = DELAY_SECONDS) -> int:
    """Write full-view *volume* page images to *dest* as a CBZ; returns page count.

    *progress(page)* may return False to cancel. Stops at the first missing page.
    """
    if not volume.full_view:
        raise NotFullViewError(f"{volume.htid} is not full view ({volume.rights}); not downloading")
    pages = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
        for seq in range(1, MAX_PAGES + 1):
            try:
                img = fetch(PAGE_IMAGE_URL.format(htid=urllib.parse.quote(volume.htid), seq=seq))
            except urllib.error.HTTPError as e:
                if e.code in (404, 400) and pages:
                    break
                raise
            if not img:
                break
            ext = "png" if img[:4] == b"\x89PNG" else "jpg"
            zf.writestr(f"{seq:05d}.{ext}", img)
            pages = seq
            if progress and progress(seq) is False:
                raise HathiError("cancelled")
            if delay:
                time.sleep(delay)
    if not pages:
        raise HathiError(f"No pages downloaded for {volume.htid}")
    return pages
