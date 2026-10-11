"""gui/manga_lookup_dialog.py: one answer for every volume of a series, other matches offered, covers for
confirmation, a source that is down shown as a row error. Canned answers, no network."""

import sys
import zipfile

from PyQt6.QtWidgets import QApplication

from core.cbz_file import CbzBook
from gui.manga_lookup_dialog import MangaLookupDialog
from test_manga_lookup import FakeNet, client

_app = QApplication.instance() or QApplication(sys.argv)


def _book(tmp_path, name, comicinfo=b"<ComicInfo/>"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", comicinfo)
        zf.writestr("001.jpg", b"\xff\xd8\xff\xe0jpeg")
    return CbzBook(str(path))


def test_every_volume_of_a_series_gets_the_same_answer_from_one_request_pair(tmp_path):
    net = FakeNet()
    books = [_book(tmp_path, f"Berserk v0{i}.cbz") for i in (1, 2, 3)]
    dialog = MangaLookupDialog(books, client=client(net))
    changes = dialog.accepted_metadata()
    assert sorted(changes) == [0, 1, 2]
    assert all(c["series"] == "Berserk" and c["publisher"] == "Dark Horse" and "number" not in c for c in changes.values())
    assert len([u for u in net.requests if "graphql.anilist.co" in u]) == 1
    assert len([u for u in net.requests if "img.anilist.co" in u]) == 1  # the cover, once for three volumes
    dialog.table.selectRow(0)
    assert dialog.detail_cover_found.pixmap() is not None or dialog.detail_cover_found.text() != ""


def test_other_series_with_a_similar_title_are_offered_and_picking_one_resolves_it(tmp_path):
    dialog = MangaLookupDialog([_book(tmp_path, "Berserk v01.cbz")], client=client(FakeNet()))
    result = dialog._row_results[0]
    assert [a.label for a in result.alternatives] == ["Berserk of Gluttony (2018) [AniList]"]
    other = dialog._resolve(dialog.items[0], result.alternatives[0].data)
    assert other.fields["series"] == "Berserk of Gluttony" and "publisher" not in other.fields


def test_a_book_without_a_series_name_says_so_and_a_correction_searches_again(tmp_path):
    book = _book(tmp_path, "scan0001.cbz")
    net = FakeNet()
    dialog = MangaLookupDialog([book], client=client(net))
    assert not dialog._row_results[0].found  # "scan" is nothing like any title: no candidate, not a wild guess
    again = dialog._search_one_book(book, {"series": "Berserk"})
    assert again.fields["series"] == "Berserk" and again.used_query == {"series": "Berserk"}


def test_both_sources_down_is_a_row_error_not_a_crash(tmp_path):
    dialog = MangaLookupDialog(
        [_book(tmp_path, "Berserk v01.cbz")], client=client(FakeNet(fail=("graphql.anilist.co", "mangaupdates")))
    )
    assert dialog._row_results[0].error and not dialog.accepted_metadata()
