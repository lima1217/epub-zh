"""Book-level metadata for the translated copy.

So readers (and people) can tell it apart from the original:
- title: translated title (``梦想机器``; bilingual adds ``（中英对照）``)
- identifier: unique identifier gets a suffix, else apps such as Apple
  Books may treat the copy as the same book as the original
- language: ``zh-CN`` in zh mode
The NCX ``docTitle`` / ``dtb:uid`` are kept in step with the OPF.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from epub_zh.epub_io import _find_opf, _is_within

DC = "http://purl.org/dc/elements/1.1/"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
_CJK_RE = re.compile(r"[㐀-鿿]")
_QUOTES = "《》〈〉「」『』\"'“”‘’ "
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
ID_SUFFIX = {"zh": "-zh", "bilingual": "-zh-bilingual"}
MODE_LABEL = {"zh": "中文版", "bilingual": "中英对照"}


def _parse(path: Path) -> etree._ElementTree:
    return etree.parse(str(path), etree.XMLParser(recover=True, resolve_entities=False, remove_blank_text=False))


def _write(tree: etree._ElementTree, path: Path) -> None:
    path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="utf-8"))


def read_title(workdir: Path) -> str | None:
    """First ``dc:title`` of the package, whitespace-collapsed."""
    root = _parse(_find_opf(workdir)).getroot()
    el = next(iter(root.iter(f"{{{DC}}}title")), None)
    text = " ".join((el.text or "").split()) if el is not None else ""
    return text or None


def clean_translated_title(text: str | None) -> str | None:
    if not text:
        return None
    text = " ".join(text.split()).strip(_QUOTES)
    return text or None


def output_title(original: str | None, translated: str | None, mode: str) -> str | None:
    """Title for the translated copy, always distinguishable from ``original``.

    zh: the Chinese title; if the model kept it in English, ``原名（中文版）``.
    bilingual: ``中文名（中英对照）``.
    """
    translated = clean_translated_title(translated)
    has_zh = bool(translated and _CJK_RE.search(translated))
    if mode == "zh" and has_zh:
        return translated
    base = translated if has_zh else original
    if not base:
        return None
    return f"{base}（{MODE_LABEL.get(mode, '中文版')}）"


def safe_filename(title: str, *, max_len: int = 120) -> str:
    name = _BAD_FILENAME.sub(" ", title)
    name = " ".join(name.split()).strip(". ")
    return name[:max_len].rstrip() or "book"


def apply_output_metadata(workdir: Path, *, title: str | None, mode: str) -> None:
    """Rewrite OPF (and NCX) metadata in ``workdir`` for the translated copy. Idempotent."""
    workdir = workdir.resolve()
    opf_path = _find_opf(workdir)
    tree = _parse(opf_path)
    root = tree.getroot()
    suffix = ID_SUFFIX.get(mode, "-zh")

    if title:
        el = next(iter(root.iter(f"{{{DC}}}title")), None)
        if el is not None:
            el.text = title
        for meta in root.iter("{*}meta"):
            if meta.get("name") == "calibre:title_sort":
                meta.set("content", title)

    if mode == "zh":
        lang = next(iter(root.iter(f"{{{DC}}}language")), None)
        if lang is not None:
            lang.text = "zh-CN"

    # Unique identifier (+ any other identifier repeating its value, e.g. calibre's).
    uid_attr = root.get("unique-identifier")
    old_uid = new_uid = None
    for ident in root.iter(f"{{{DC}}}identifier"):
        if ident.get("id") == uid_attr and ident.text:
            old_uid = ident.text.strip()
            new_uid = old_uid if old_uid.endswith(suffix) else old_uid + suffix
            break
    if old_uid and new_uid != old_uid:
        for ident in root.iter(f"{{{DC}}}identifier"):
            if (ident.text or "").strip() == old_uid:
                ident.text = new_uid
    _write(tree, opf_path)

    for item in root.iter("{*}item"):
        if item.get("media-type") != "application/x-dtbncx+xml":
            continue
        ncx_path = (opf_path.parent / unquote(item.get("href", ""))).resolve()
        if not _is_within(workdir, ncx_path) or not ncx_path.is_file():
            continue
        ncx = _parse(ncx_path)
        nroot = ncx.getroot()
        if title:
            for text in nroot.iterfind(f"{{{NCX}}}docTitle/{{{NCX}}}text"):
                text.text = title
        if old_uid and new_uid != old_uid:
            for meta in nroot.iterfind(f"{{{NCX}}}head/{{{NCX}}}meta"):
                if meta.get("name") == "dtb:uid" and (meta.get("content") or "").strip() == old_uid:
                    meta.set("content", new_uid)
        _write(ncx, ncx_path)
