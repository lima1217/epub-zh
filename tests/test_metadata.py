from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from epub_zh import pipeline
from epub_zh.config import ResolvedSettings
from epub_zh.epub_io import JobState
from epub_zh.metadata import apply_output_metadata, output_title, read_title, safe_filename
from epub_zh.pipeline import run_translate

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">M. Mitchell Waldrop</dc:creator>
    <dc:identifier id="uuid_id" opf:scheme="uuid">355a-uuid</dc:identifier>
    <dc:identifier opf:scheme="calibre">355a-uuid</dc:identifier>
    <dc:identifier opf:scheme="ISBN">9781953953216</dc:identifier>
    <dc:language>en</dc:language>
    <meta name="calibre:title_sort" content="Dream Machine, The"/>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""
NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta content="355a-uuid" name="dtb:uid"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap/>
</ncx>"""
CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>"""


def _write_book(root: Path, title: str, paragraphs: list[str]) -> None:
    (root / "META-INF").mkdir(parents=True)
    (root / "OEBPS").mkdir()
    (root / "mimetype").write_text("application/epub+zip")
    (root / "META-INF/container.xml").write_text(CONTAINER)
    (root / "OEBPS/content.opf").write_text(OPF.format(title=title), encoding="utf-8")
    (root / "OEBPS/toc.ncx").write_text(NCX.format(title=title), encoding="utf-8")
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    (root / "OEBPS/ch1.xhtml").write_text(XHTML.format(body=body), encoding="utf-8")


def _epub(path: Path, title: str, paragraphs: list[str]) -> Path:
    src = path.parent / "unpacked"
    _write_book(src, title, paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())
    path.write_bytes(buf.getvalue())
    return path


# ---- pure helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "translated", "mode", "want"),
    [
        ("The Dream Machine", "梦想机器", "zh", "梦想机器"),
        ("The Dream Machine", "《梦想机器》", "zh", "梦想机器"),
        ("The Dream Machine", "梦想机器", "bilingual", "梦想机器（中英对照）"),
        ("Hamlet", "Hamlet", "zh", "Hamlet（中文版）"),  # model kept the proper noun
        ("Hamlet", None, "bilingual", "Hamlet（中英对照）"),
        ("三体", None, "zh", "三体（中文版）"),  # title already Chinese, never sent
        (None, None, "zh", None),
    ],
)
def test_output_title(original: str | None, translated: str | None, mode: str, want: str | None) -> None:
    assert output_title(original, translated, mode) == want


def test_safe_filename() -> None:
    assert safe_filename('A/B: "C"?') == "A B C"
    assert safe_filename("梦想机器（中英对照）") == "梦想机器（中英对照）"
    assert safe_filename("...") == "book"


# ---- OPF / NCX rewrite -------------------------------------------------------


def test_apply_output_metadata(tmp_path: Path) -> None:
    _write_book(tmp_path, "The Dream Machine", ["x"])
    for _ in range(2):  # idempotent: resume / re-run must not double the suffix
        apply_output_metadata(tmp_path, title="梦想机器", mode="zh")
    opf = (tmp_path / "OEBPS/content.opf").read_text(encoding="utf-8")
    ncx = (tmp_path / "OEBPS/toc.ncx").read_text(encoding="utf-8")
    assert "<dc:title>梦想机器</dc:title>" in opf  # dc: prefix kept
    assert 'content="梦想机器"' in opf  # calibre:title_sort
    assert "<dc:language>zh-CN</dc:language>" in opf
    assert opf.count(">355a-uuid-zh<") == 2  # uuid + calibre copy of it
    assert "9781953953216" in opf  # other identifiers untouched
    assert "-zh-zh" not in opf
    assert 'content="355a-uuid-zh"' in ncx and "<text>梦想机器</text>" in ncx
    assert read_title(tmp_path) == "梦想机器"


def test_bilingual_keeps_language(tmp_path: Path) -> None:
    _write_book(tmp_path, "T", ["x"])
    apply_output_metadata(tmp_path, title="T（中英对照）", mode="bilingual")
    opf = (tmp_path / "OEBPS/content.opf").read_text(encoding="utf-8")
    assert "<dc:language>en</dc:language>" in opf and ">355a-uuid-zh-bilingual<" in opf


# ---- through the pipeline ----------------------------------------------------


class Fake:
    calls: list[list[str]] = []

    def __init__(self, **_: Any) -> None:
        pass

    def translate_batch(self, texts: list[str]) -> list[str]:
        Fake.calls.append(list(texts))
        return ["梦想机器" if t == "The Dream Machine" else f"中:{t}" for t in texts]


def test_pipeline_translates_title_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Fake.calls = []
    monkeypatch.setattr(pipeline, "Translator", Fake)
    src = _epub(tmp_path / "book.epub", "The Dream Machine", ["The Dream Machine", "Body text."])
    out = tmp_path / "out.epub"
    settings = ResolvedSettings("sk-x", None, "m", 40, {}, batch_chars=3000, concurrency=2)
    run_translate(source=src, output=out, mode="zh", settings=settings)

    sent = [t for call in Fake.calls for t in call]
    assert sorted(sent) == ["Body text.", "The Dream Machine"]  # title shares the body block's call
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode()
    assert "<dc:title>梦想机器</dc:title>" in opf and ">355a-uuid-zh<" in opf
    state = JobState.load(out.parent / ".epub-zh-state" / "out" / "state.json")
    assert state.title_zh == "梦想机器" and state.output_title == "梦想机器"
