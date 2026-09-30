# Credits

Third-party libraries and references this project depends on or draws from:

- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) -- GUI toolkit
- [lxml](https://lxml.de/) -- XML parsing/serialization for ComicInfo.xml
- [Pillow](https://python-pillow.org/) -- page-image decoding/resizing for Repair > Resize Images...
- [rarfile](https://github.com/markokr/rarfile) -- optional CBR reading (Repair > Convert to CBZ)
- [py7zr](https://github.com/miurahr/py7zr) -- optional CB7 reading (Repair > Convert to CBZ)
- [PyInstaller](https://pyinstaller.org/) -- standalone Windows .exe packaging
- [The Anansi Project](https://anansi-project.github.io/docs/comicinfo/intro) -- ComicInfo.xml schema documentation
- [Comic Vine](https://comicvine.gamespot.com/api/) -- metadata source for Metadata > Look Up > Comic Vine
- [Grand Comics Database](https://www.comics.org/) -- metadata source for Metadata > Look Up > Grand Comics Database; also the data behind Metadata > Look Up > GCD Local Database, read from the user's own downloaded copy of GCD's SQLite data dump (never bundled with this app)
- [Bedetheque](https://www.bedetheque.com/) -- metadata source for Metadata > Look Up > Bedetheque; its site structure was analyzed via three community scraper projects (givka/bedetheque-scraper, vsoeiro/bedetheque, maforget/Bedetheque-Scrapper-2 on GitHub) before writing this app's own module against the current live site
- [cloudscraper](https://github.com/VeNoMouS/cloudscraper) -- optional dependency for Metadata > Look Up > Bedetheque, needed to get past that site's Cloudflare protection
- [ComicTagger](https://github.com/comictagger/comictagger) -- open-source prior art this lookup feature's design follows; its `comicapi/tags/comicrack.py` was also consulted directly to confirm ComicInfo.xml field-handling compatibility
- [send2trash](https://github.com/arsenetar/send2trash) -- moves converted originals to the Recycle Bin/Trash instead of deleting them
- [ZenCBR](https://zentastic.subcutis.net/blog/2012/01/30/zencbr-comic-book-archive-maintenance-utility/) by the late Shannon Larratt -- inspiration for the scene-tag handling behind Metadata > Read Filename Tags: matching whole bracketed phrases against a learned list of release groups, and its "Clean Names" rules. The built-in tag list was seeded from the maintainer's own ZenCBR training list. No ZenCBR code is used.
- [CbxConverter](https://github.com/tomek-o/CbxConverter) by tomek-o -- inspiration for the Size column and the Resize Images options (height limit, output format, width presets); this app does the same job in-process with Pillow rather than via ImageMagick. No CbxConverter code is used.
- [redactor_common](https://github.com/Erlbon/redactor_common) -- shared UI/core code across the Redactor family
