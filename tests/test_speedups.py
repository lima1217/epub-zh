"""Concurrency, char-based batching, skip/dedup, and --skip-refs."""

from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from epub_zh import pipeline
from epub_zh.backmatter import reference_docs
from epub_zh.config import ResolvedSettings
from epub_zh.epub_io import JobState, list_content_docs, unpack_epub
from epub_zh.pipeline import TranslationFailed, run_resume, run_translate
from epub_zh.translate import collect_blocks, make_batches, needs_translation, parse_xhtml

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body{attrs}>{body}</body></html>"""


def _epub(path: Path, docs: dict[str, list[str]], *, guide: dict[str, str] | None = None, body_attrs: dict[str, str] | None = None) -> Path:
    """docs: file name -> paragraphs (spine order). guide: type -> file name."""
    items = "".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, n in enumerate(docs))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    refs = "".join(f'<reference type="{t}" href="{n}" title="{t}"/>' for t, n in (guide or {}).items())
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title><dc:identifier id="uid">id</dc:identifier></metadata>
  <manifest>{items}</manifest><spine>{spine}</spine><guide>{refs}</guide>
</package>"""
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for name, paras in docs.items():
            attrs = (body_attrs or {}).get(name, "")
            body = "".join(f"<p>{t}</p>" for t in paras)
            zf.writestr(f"OEBPS/{name}", XHTML.format(attrs=attrs, body=body))
    path.write_bytes(buf.getvalue())
    return path


def _doc_text(epub: Path, name: str) -> str:
    with zipfile.ZipFile(epub) as zf:
        return zf.read(f"OEBPS/{name}").decode()


class Recorder:
    """Fake Translator recording every batch; optional gate / failure trigger."""

    calls: list[list[str]] = []
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    delay = 0.0
    fail_on: str | None = None

    def __init__(self, **_: Any) -> None:
        pass

    def translate_batch(self, texts: list[str]) -> list[str]:
        cls = Recorder
        with cls.lock:
            cls.calls.append(list(texts))
            cls.in_flight += 1
            cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        try:
            time.sleep(cls.delay)
            if cls.fail_on is not None and cls.fail_on in texts:
                raise RuntimeError("boom")
            return [f"中:{t}" for t in texts]
        finally:
            with cls.lock:
                cls.in_flight -= 1


@pytest.fixture(autouse=True)
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[Recorder]:
    Recorder.calls = []
    Recorder.in_flight = Recorder.max_in_flight = 0
    Recorder.delay = 0.0
    Recorder.fail_on = None
    monkeypatch.setattr(pipeline, "Translator", Recorder)
    return Recorder


def _settings(**kw: Any) -> ResolvedSettings:
    base = dict(api_key="sk-x", base_url=None, model="m", batch_size=40, sources={}, batch_chars=3000, concurrency=4)
    base.update(kw)
    return ResolvedSettings(**base)


# ---- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Hello world.", True),
        ("Chapter 3", True),
        ("1984", False),
        ("— 42 —", False),
        ("已经是中文", False),
        ("https://example.com/a?b=c", False),
        ("www.example.org", False),
        ("someone@example.com", False),
        ("See https://example.com for more.", True),
    ],
)
def test_needs_translation(text: str, want: bool) -> None:
    assert needs_translation(text) is want


def test_make_batches_by_chars_and_count() -> None:
    texts = ["a" * 400] * 5 + ["b" * 10] * 50 + ["c" * 5000]
    batches = make_batches(texts, max_chars=1000, max_items=20)
    assert [i for b in batches for i in b] == list(range(len(texts)))  # order kept, nothing lost
    assert batches[0] == [0, 1]  # 800 chars; a third 400 would pass 1000
    assert all(len(b) <= 20 for b in batches)
    assert batches[-1] == [55]  # oversized text alone
    assert make_batches([], max_chars=10, max_items=5) == []


# ---- pipeline --------------------------------------------------------------


def test_dedup_and_skip_send_each_text_once(tmp_path: Path, recorder: type[Recorder]) -> None:
    src = _epub(
        tmp_path / "b.epub",
        {
            "a.xhtml": ["The Book Title", "First paragraph.", "1984", "Ibid."],
            "b.xhtml": ["The Book Title", "Second paragraph.", "Ibid."],
        },
    )
    out = tmp_path / "o.epub"
    run_translate(source=src, output=out, mode="zh", settings=_settings())
    sent = [t for call in recorder.calls for t in call]
    assert sorted(sent) == sorted(["The Book Title", "First paragraph.", "Ibid.", "Second paragraph."])
    b = _doc_text(out, "b.xhtml")
    assert "中:The Book Title" in b and "中:Ibid." in b and "中:Second paragraph." in b
    assert "<p>1984</p>" in _doc_text(out, "a.xhtml")  # left as is


def test_batches_run_concurrently(tmp_path: Path, recorder: type[Recorder]) -> None:
    recorder.delay = 0.05
    paras = [f"Paragraph number {i} " + "x" * 100 for i in range(12)]
    src = _epub(tmp_path / "b.epub", {"a.xhtml": paras[:6], "b.xhtml": paras[6:]})
    out = tmp_path / "o.epub"
    run_translate(source=src, output=out, mode="zh", settings=_settings(batch_chars=200, concurrency=4))
    assert len(recorder.calls) == 12
    assert recorder.max_in_flight > 1
    a, b = _doc_text(out, "a.xhtml"), _doc_text(out, "b.xhtml")
    for p in paras[:6]:
        assert f"中:{p}" in a  # results land on the right paragraph despite completion order
    for p in paras[6:]:
        assert f"中:{p}" in b


def test_failure_keeps_finished_docs_and_resume_skips_them(tmp_path: Path, recorder: type[Recorder]) -> None:
    src = _epub(tmp_path / "b.epub", {"a.xhtml": ["Good one."], "b.xhtml": ["Bad one."]})
    out = tmp_path / "o.epub"
    recorder.fail_on = "Bad one."
    with pytest.raises(TranslationFailed) as info:
        run_translate(source=src, output=out, mode="zh", settings=_settings(batch_size=1, concurrency=1))
    assert info.value.doc == "OEBPS/b.xhtml"
    state_path = out.parent / ".epub-zh-state" / "o" / "state.json"
    state = JobState.load(state_path)
    assert state.completed_docs == ["OEBPS/a.xhtml"] and state.failed_doc == "OEBPS/b.xhtml"

    recorder.fail_on = None
    recorder.calls = []
    run_resume(state_path=state_path, api_key="sk-x")
    assert recorder.calls == [["Bad one."]]
    assert "中:Good one." in _doc_text(out, "a.xhtml") and "中:Bad one." in _doc_text(out, "b.xhtml")


def test_skip_refs_leaves_index_and_split_continuation(tmp_path: Path, recorder: type[Recorder]) -> None:
    src = _epub(
        tmp_path / "b.epub",
        {
            "ch1.xhtml": ["Real chapter text."],
            "bib.xhtml": ["Bibliography", "Smith, J. A Book. 1999."],
            "idx_split_000.xhtml": ["Book Title", "Index", "Aberdeen 34, 54"],
            "idx_split_001.xhtml": ["Lebling, David 308"],
            "after.xhtml": ["Acknowledgments text."],
        },
    )
    out = tmp_path / "o.epub"
    run_translate(source=src, output=out, mode="zh", settings=_settings(), skip_refs=True)
    sent = {t for call in recorder.calls for t in call}
    assert sent == {"Real chapter text.", "Acknowledgments text."}
    assert "<p>Lebling, David 308</p>" in _doc_text(out, "idx_split_001.xhtml")
    state = JobState.load(out.parent / ".epub-zh-state" / "o" / "state.json")
    assert state.skip_refs is True and len(state.completed_docs) == 5


def test_without_skip_refs_index_is_translated(tmp_path: Path, recorder: type[Recorder]) -> None:
    src = _epub(tmp_path / "b.epub", {"idx.xhtml": ["Index", "Aberdeen 34"]})
    run_translate(source=src, output=tmp_path / "o.epub", mode="zh", settings=_settings())
    assert {t for call in recorder.calls for t in call} == {"Index", "Aberdeen 34"}


# ---- detection signals -----------------------------------------------------


def _refs(tmp_path: Path, src: Path) -> list[str]:
    work = tmp_path / "w"
    unpack_epub(src, work)
    docs = list_content_docs(work)
    parsed = {}
    for d in docs:
        root = parse_xhtml(str(d)).getroot()
        parsed[d] = (root, collect_blocks(root))
    return sorted(p.name for p in reference_docs(work, docs, parsed))


def test_detects_guide_reference(tmp_path: Path) -> None:
    src = _epub(tmp_path / "b.epub", {"ch.xhtml": ["Text."], "back.xhtml": ["Entries 12"]}, guide={"index": "back.xhtml"})
    assert _refs(tmp_path, src) == ["back.xhtml"]


def test_detects_epub_type_on_body(tmp_path: Path) -> None:
    src = _epub(
        tmp_path / "b.epub",
        {"ch.xhtml": ["Text."], "bib.xhtml": ["Works by others"]},
        body_attrs={"bib.xhtml": ' epub:type="backmatter bibliography"'},
    )
    assert _refs(tmp_path, src) == ["bib.xhtml"]


def test_chapter_mentioning_index_is_not_marked(tmp_path: Path) -> None:
    src = _epub(tmp_path / "b.epub", {"ch.xhtml": ["Chapter 1", "The index finger points.", "References matter."]})
    assert _refs(tmp_path, src) == []
