from __future__ import annotations

import io
import zipfile
from pathlib import Path

from epub_zh.pipeline import run_dry_run


def _minimal_epub(path: Path) -> None:
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>T</dc:title><dc:identifier id="uid">id</dc:identifier>
  </metadata>
  <manifest>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello world chapter.</p></body></html>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", xhtml)
    path.write_bytes(buf.getvalue())


def test_dry_run_leaves_no_state(tmp_path: Path) -> None:
    src = tmp_path / "book.epub"
    out = tmp_path / "out.epub"
    _minimal_epub(src)
    run_dry_run(source=src, mode="zh")
    assert not out.exists()
    assert not (tmp_path / ".epub-zh-state").exists()
    # No leftover epub-zh-dry-* under system temp is hard to assert globally;
    # absence of project state dir is the user-visible contract.
