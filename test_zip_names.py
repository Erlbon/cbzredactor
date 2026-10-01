"""Legacy (non-UTF-8-flagged) ZIP entry names: core/zip_names.py and how every
rewrite treats them. Archives are built byte by byte with struct, so the tests
don't depend on zipfile's own name encoding, and the written headers are read
back the same way."""

import struct
import zipfile
import zlib

import pytest

from core.cbz_file import CbzBook, page_sort_key
from core.cbz_fingerprint import cbz_fingerprint
from core.zip_names import RawNameInfo, decode_zip_name, legacy_raw_name, open_zip, raw_names_supported

COMICINFO = b"<?xml version='1.0'?><ComicInfo><Title>T</Title><Series>S</Series></ComicInfo>"
UTF8_FLAG = 0x800


def build_zip(path, entries, comment=b""):
    """entries: (raw name bytes, data, general-purpose flags). Stored, no extras."""
    out, central = bytearray(), bytearray()
    for raw, data, flags in entries:
        offset, crc = len(out), zlib.crc32(data)
        out += struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, flags, 0, 0, 0x21, crc, len(data), len(data), len(raw), 0)
        out += raw + data
        central += struct.pack(
            "<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, flags, 0, 0, 0x21, crc, len(data), len(data),
            len(raw), 0, 0, 0, 0, 0, offset,
        )
        central += raw
    out_len = len(out)
    out += central
    out += struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(entries), len(entries), len(central), out_len, len(comment))
    out += comment
    path.write_bytes(bytes(out))
    return str(path)


def raw_headers(path):
    """[(central name bytes, central flags, local name bytes, local flags)] straight from the file."""
    raw = open(path, "rb").read()
    eocd = raw.rindex(b"PK\x05\x06")
    count, _size, cd_offset = struct.unpack_from("<HII", raw, eocd + 10)
    result, pos = [], cd_offset
    for _ in range(count):
        assert raw[pos:pos + 4] == b"PK\x01\x02"
        flags = struct.unpack_from("<H", raw, pos + 8)[0]
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", raw, pos + 28)
        local_offset = struct.unpack_from("<I", raw, pos + 42)[0]
        name = raw[pos + 46:pos + 46 + name_len]
        assert raw[local_offset:local_offset + 4] == b"PK\x03\x04"
        local_flags = struct.unpack_from("<H", raw, local_offset + 6)[0]
        local_len = struct.unpack_from("<H", raw, local_offset + 26)[0]
        local_name = raw[local_offset + 30:local_offset + 30 + local_len]
        result.append((name, flags, local_name, local_flags))
        pos += 46 + name_len + extra_len + comment_len
    return result


JPEG = b"\xff\xd8\xff\xe0fakejpeg"
SHIFT_JIS = "第二話/ページ01.jpg".encode("cp932")  # "chapter two/page 01"
SHIFT_JIS_2 = "第二話/ページ02.jpg".encode("cp932")
UTF8_UNFLAGGED = "Märchen/Seite é2.jpg".encode("utf-8")
UTF8_UNFLAGGED_1 = "Märchen/Seite é1.jpg".encode("utf-8")
CP437_ONLY = b"caf\x82 01.jpg"  # 0x82 = "e acute" in cp437; invalid as UTF-8


# --- decoding ---------------------------------------------------------------------


def _first(path):
    with zipfile.ZipFile(path) as zf:
        return zf.infolist()[0]


def test_flagged_utf8_name_is_taken_as_is(tmp_path):
    path = build_zip(tmp_path / "a.zip", [("é.jpg".encode("utf-8"), JPEG, UTF8_FLAG)])
    assert decode_zip_name(_first(path)) == "é.jpg"
    assert legacy_raw_name(_first(path)) is None


def test_unflagged_ascii_name_is_untouched(tmp_path):
    path = build_zip(tmp_path / "a.zip", [(b"001.jpg", JPEG, 0)])
    assert decode_zip_name(_first(path)) == "001.jpg"
    assert legacy_raw_name(_first(path)) is None


def test_unflagged_utf8_bytes_are_repaired(tmp_path):
    path = build_zip(tmp_path / "a.zip", [(UTF8_UNFLAGGED, JPEG, 0)])
    info = _first(path)
    assert info.filename != "Märchen/Seite é2.jpg"  # zipfile's own cp437 reading is mojibake
    assert decode_zip_name(info) == "Märchen/Seite é2.jpg"
    assert legacy_raw_name(info) is None  # repaired, so written as a proper UTF-8 name


@pytest.mark.parametrize("raw", [SHIFT_JIS, CP437_ONLY])
def test_other_bytes_are_kept_as_zipfile_reads_them(tmp_path, raw):
    path = build_zip(tmp_path / "a.zip", [(raw, JPEG, 0)])
    info = _first(path)
    assert decode_zip_name(info) == info.filename == raw.decode("cp437")
    assert legacy_raw_name(info) == raw


def test_open_zip_uses_the_repaired_names_everywhere(tmp_path):
    path = build_zip(tmp_path / "a.zip", [(UTF8_UNFLAGGED, b"page", 0), (b"plain.jpg", b"plain", 0)])
    with open_zip(path) as zf:
        assert zf.namelist() == ["Märchen/Seite é2.jpg", "plain.jpg"]
        assert zf.read("Märchen/Seite é2.jpg") == b"page"  # read by the repaired name
        assert zf.getinfo("Märchen/Seite é2.jpg").file_size == 4
        assert zf.testzip() is None


def test_open_zip_on_unrepairable_names_still_reads(tmp_path):
    path = build_zip(tmp_path / "a.zip", [(SHIFT_JIS, b"page", 0)])
    with open_zip(path) as zf:
        (name,) = zf.namelist()
        assert zf.read(name) == b"page"


def test_raw_name_hook_is_available():
    assert raw_names_supported()
    info = RawNameInfo("x", (2020, 1, 1, 0, 0, 0), b"\x82")
    assert info._encodeFilenameFlags() == (b"\x82", 0)


# --- the app: listing, sorting, ComicInfo -----------------------------------------------


def test_book_lists_and_sorts_repaired_page_names(tmp_path):
    path = build_zip(tmp_path / "b.cbz", [
        (b"ComicInfo.xml", COMICINFO, 0),
        (UTF8_UNFLAGGED, JPEG, 0),  # Seite é2
        ("Märchen/Seite é10.jpg".encode("utf-8"), JPEG, 0),
        (UTF8_UNFLAGGED_1, JPEG, 0),
    ])
    book = CbzBook(path)
    assert not book.load_error
    assert book.page_names == ["Märchen/Seite é1.jpg", "Märchen/Seite é2.jpg", "Märchen/Seite é10.jpg"]
    assert book.first_page_name == "Märchen/Seite é1.jpg"
    assert book.metadata.title == "T" and book.comicinfo_name == "ComicInfo.xml"
    assert book.read_first_page_bytes() == JPEG
    assert page_sort_key(book.page_names[1]) < page_sort_key(book.page_names[2])


def test_comicinfo_with_an_unrepairable_folder_is_still_found(tmp_path):
    path = build_zip(tmp_path / "b.cbz", [(b"ComicInfo.xml", COMICINFO, 0), (SHIFT_JIS, JPEG, 0)])
    book = CbzBook(path)
    assert book.metadata.title == "T" and book.actual_page_count == 1


# --- saving ------------------------------------------------------------------------------


def test_save_repairs_utf8_names_and_flags_them(tmp_path):
    path = build_zip(tmp_path / "b.cbz", [
        (b"ComicInfo.xml", COMICINFO, 0), (UTF8_UNFLAGGED_1, b"one", 0), (UTF8_UNFLAGGED, b"two", 0),
    ])
    book = CbzBook(path)
    book.metadata.title = "New"
    book.save()
    headers = raw_headers(path)
    by_name = {h[0]: h for h in headers}
    for raw in (UTF8_UNFLAGGED_1, UTF8_UNFLAGGED):
        c_name, c_flags, l_name, l_flags = by_name[raw]  # the bytes are the same UTF-8 ...
        assert c_flags & UTF8_FLAG and l_flags & UTF8_FLAG  # ... now flagged, central and local
        assert l_name == raw
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist()[:2] == ["Märchen/Seite é1.jpg", "Märchen/Seite é2.jpg"]  # plain zipfile reads it right now
        assert zf.read("Märchen/Seite é1.jpg") == b"one"
    assert CbzBook(path).page_names == ["Märchen/Seite é1.jpg", "Märchen/Seite é2.jpg"]
    assert CbzBook(path).metadata.title == "New"
    first = open(path, "rb").read()
    again = CbzBook(path)
    again.metadata.title = "Newer"
    again.save()
    assert {h[0] for h in raw_headers(path)} == {h[0] for h in headers}  # stable on a second save
    assert first != open(path, "rb").read()


def test_save_keeps_unrepairable_name_bytes_exactly(tmp_path):
    entries = [
        (b"ComicInfo.xml", COMICINFO, 0),
        (SHIFT_JIS, b"one", 0),
        (SHIFT_JIS_2, b"two", 0),
        (b"003.jpg", b"three", 0),
        (CP437_ONLY, b"four", 0),
    ]
    path = build_zip(tmp_path / "b.cbz", entries)
    book = CbzBook(path)
    before_order = book.page_names
    book.metadata.title = "Edited"
    book.save()
    headers = raw_headers(path)
    kept = {raw: (c_flags, l_name, l_flags) for raw, c_flags, l_name, l_flags in headers}
    for raw in (SHIFT_JIS, SHIFT_JIS_2, CP437_ONLY, b"003.jpg"):
        c_flags, l_name, l_flags = kept[raw]  # central name bytes identical
        assert l_name == raw  # local name bytes identical
        assert not c_flags & UTF8_FLAG and not l_flags & UTF8_FLAG  # flag bits unchanged
    reloaded = CbzBook(path)
    assert reloaded.page_names == before_order  # same pages, same order, same first page
    assert reloaded.first_page_name == book.first_page_name
    assert reloaded.metadata.title == "Edited"
    with open_zip(path) as zf:
        assert [zf.read(n) for n in reloaded.page_names if n.endswith("jpg")]  # entries still readable
        assert zf.testzip() is None


def test_mixed_names_in_one_archive(tmp_path):
    entries = [
        (b"ComicInfo.xml", COMICINFO, 0),
        ("ünï/ok.jpg".encode("utf-8"), b"flagged", UTF8_FLAG),
        (UTF8_UNFLAGGED, b"repairable", 0),
        (SHIFT_JIS, b"legacy", 0),
        (b"plain.jpg", b"plain", 0),
    ]
    path = build_zip(tmp_path / "b.cbz", entries)
    CbzBook(path).save()
    headers = {h[2]: h for h in raw_headers(path)}
    assert headers["ünï/ok.jpg".encode()][3] & UTF8_FLAG
    assert headers[UTF8_UNFLAGGED][3] & UTF8_FLAG  # repaired
    assert not headers[SHIFT_JIS][3] & UTF8_FLAG  # untouched
    assert not headers[b"plain.jpg"][3] & UTF8_FLAG
    with open_zip(path) as zf:
        assert zf.read("Märchen/Seite é2.jpg") == b"repairable"


def test_renaming_a_legacy_entry_gives_it_a_normal_name(tmp_path):
    from core.zip_rewrite import Action, RewritePlan, rewrite_archive

    path = build_zip(tmp_path / "b.zip", [(SHIFT_JIS, b"legacy", 0), (SHIFT_JIS_2, b"legacy2", 0)])
    legacy_name = SHIFT_JIS.decode("cp437")
    rewrite_archive(
        path, path, RewritePlan(decide=lambda e, r: Action(rename="001.jpg") if e.name == legacy_name else None),
    )
    names = {h[0] for h in raw_headers(path)}
    assert names == {b"001.jpg", SHIFT_JIS_2}


def test_remove_pages_and_clean_up_keep_legacy_bytes(tmp_path):
    path = build_zip(tmp_path / "b.cbz", [
        (b"ComicInfo.xml", COMICINFO, 0), (b"1.jpg", b"a", 0), (b"2.jpg", b"b", 0), (SHIFT_JIS, b"c", 0),
    ])
    book = CbzBook(path)
    gone = [n for n in book.page_names if n == "1.jpg"]
    book.remove_pages(gone)
    assert SHIFT_JIS in {h[2] for h in raw_headers(path)}
    assert b"1.jpg" not in {h[2] for h in raw_headers(path)}

    path2 = build_zip(tmp_path / "c.cbz", [
        (b"ComicInfo.xml", COMICINFO, 0), (b"Thumbs.db", b"x", 0), (b"junk/a.nfo", b"x", 0), (SHIFT_JIS, b"keep", 0),
    ])
    book2 = CbzBook(path2)
    book2.clean_contents()
    headers = {h[2]: h for h in raw_headers(path2)}
    assert b"Thumbs.db" not in headers and b"junk/a.nfo" not in headers
    # a renamed page is a normal ASCII name now; the data is intact
    with open_zip(path2) as zf:
        assert b"keep" in [zf.read(n) for n in zf.namelist()]


def test_fingerprint_survives_a_name_repairing_save(tmp_path):
    path = build_zip(tmp_path / "b.cbz", [
        (b"ComicInfo.xml", COMICINFO, 0), (UTF8_UNFLAGGED_1, b"one", 0), (SHIFT_JIS, b"two", 0),
    ])
    before = cbz_fingerprint(path)
    assert before
    book = CbzBook(path)
    book.metadata.title = "x"
    book.save()
    assert cbz_fingerprint(path) == before
