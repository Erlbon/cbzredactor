# Credits

Third-party libraries and references this project depends on or draws from:

- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) -- GUI toolkit
- [lxml](https://lxml.de/) -- XML parsing/serialization for ComicInfo.xml
- [Pillow](https://python-pillow.org/) -- page-image decoding/resizing for Operations > Resize Images...
- [rarfile](https://github.com/markokr/rarfile) -- optional CBR reading (Import > Convert CBR to CBZ)
- [PyInstaller](https://pyinstaller.org/) -- standalone Windows .exe packaging
- [The Anansi Project](https://anansi-project.github.io/docs/comicinfo/intro) -- ComicInfo.xml schema documentation
- [Comic Vine](https://comicvine.gamespot.com/api/) -- metadata source for Import > Look Up via Comic Vine
- [Grand Comics Database](https://www.comics.org/) -- metadata source for Import > Look Up via Grand Comics Database
- [ComicTagger](https://github.com/comictagger/comictagger) -- open-source prior art this lookup feature's design follows; its `comicapi/tags/comicrack.py` was also consulted directly to confirm ComicInfo.xml field-handling compatibility
- [redactor_common](https://github.com/Erlbon/redactor_common) -- shared UI/core code across the Redactor family
