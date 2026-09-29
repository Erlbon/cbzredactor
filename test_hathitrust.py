import json
import urllib.error
import zipfile

import pytest

from core import hathitrust as h


def test_parse_reference():
    assert h.parse_reference("https://babel.hathitrust.org/cgi/pt?id=mdp.39015005337788&seq=7") == ("htid", "mdp.39015005337788")
    assert h.parse_reference("https://catalog.hathitrust.org/Record/000578050") == ("recordnumber", "000578050")
    assert h.parse_reference("oclc:424023") == ("oclc", "424023")
    assert h.parse_reference("9780030110405") == ("isbn", "9780030110405")
    assert h.parse_reference("mdp.39015005337788") == ("htid", "mdp.39015005337788")
    with pytest.raises(h.HathiError):
        h.parse_reference("hello world")


def _catalog(_url):
    return json.dumps({
        "records": {"1": {"titles": ["Old Book"]}},
        "items": [
            {"htid": "a.1", "fromRecord": "1", "rightsCode": "pd", "usRightsString": "Full view"},
            {"htid": "b.2", "fromRecord": "1", "rightsCode": "ic", "usRightsString": "Limited (search-only)"},
        ],
    }).encode()


def test_lookup_flags_full_view():
    vols = h.lookup("oclc:1", fetch=_catalog)
    assert [(v.htid, v.full_view, v.title) for v in vols] == [("a.1", True, "Old Book"), ("b.2", False, "Old Book")]


def test_download_builds_cbz_and_stops_on_404(tmp_path):
    def fetch(url):
        seq = int(url.split("seq=")[1].split("&")[0])
        if seq > 3:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return b"\x89PNG" + bytes([seq])
    out = tmp_path / "x.cbz"
    n = h.download_cbz(h.Volume("a.1", "t", "pd", True), str(out), fetch=fetch, delay=0)
    assert n == 3
    assert zipfile.ZipFile(out).namelist() == ["00001.png", "00002.png", "00003.png"]


def test_refuses_non_full_view(tmp_path):
    with pytest.raises(h.NotFullViewError):
        h.download_cbz(h.Volume("b.2", "t", "ic", False), str(tmp_path / "y.cbz"), fetch=lambda u: b"x")
