"""ComicInfo.xml, zips and tags are written only through redactor_common's shared writers (core/comicinfo_write.py,
core/zip_rewrite.py, core/audio_write.py; merged from this app's and Dossier's own on 2026-11-04). A second writer here
would drift from the others again: this test names the line that writes on its own."""
import os

from redactor_common.core.writer_guard import find_private_writers


def test_nothing_in_the_app_writes_outside_the_shared_writers():
    here = os.path.dirname(os.path.abspath(__file__))
    assert find_private_writers(here) == []
