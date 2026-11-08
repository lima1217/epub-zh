"""Find bibliography / index docs so ``--skip-refs`` can leave them in English.

Signals, any of which marks a doc:
- EPUB2 ``<guide><reference type="index|bibliography">``
- EPUB3 nav landmarks ``<a epub:type="index|bibliography">``
- ``epub:type`` on ``<body>`` or its direct children
- a title like "Index" / "Bibliography" / "References" among the first blocks

Converters such as calibre split long docs into ``name_split_000.html``,
``name_split_001.html``...; later parts of a marked doc have no heading of
their own, so they inherit the mark.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from epub_zh.epub_io import _find_opf, _is_within
from epub_zh.translate import BlockUnit

REF_TYPES = frozenset({"index", "bibliography"})
_OPS_TYPE = "{http://www.idpf.org/2007/ops}type"
_HEADING_SCAN = 5
_TITLE_RE = re.compile(
    r"^(?:(?:general|subject|name|selected|select|annotated)\s+)?"
    r"(?:index|index of names|index of subjects|bibliography|references|works cited"
    r"|sources|further reading|suggested reading|recommended reading|sources and further reading)$",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"^(?P<base>.+)_split_(?P<n>\d+)$")


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _resolve_href(base_dir: Path, href: str, workdir: Path) -> Path | None:
    href = unquote(href.split("#", 1)[0])
    if not href:
        return None
    path = (base_dir / href).resolve()
    return path if _is_within(workdir, path) else None


def landmark_docs(workdir: Path) -> set[Path]:
    """Docs the package itself labels as index / bibliography (guide + nav landmarks)."""
    workdir = workdir.resolve()
    opf = _find_opf(workdir)
    found: set[Path] = set()
    nav_hrefs: list[str] = []
    root = ET.parse(opf).getroot()
    for el in root.iter():
        name = _local(el.tag)
        if name == "reference" and el.attrib.get("type", "").lower() in REF_TYPES:
            path = _resolve_href(opf.parent, el.attrib.get("href", ""), workdir)
            if path:
                found.add(path)
        elif name == "item" and "nav" in el.attrib.get("properties", "").split():
            nav_hrefs.append(el.attrib.get("href", ""))

    for href in nav_hrefs:
        nav = _resolve_href(opf.parent, href, workdir)
        if nav is None or not nav.is_file():
            continue
        try:
            tree = etree.parse(str(nav), etree.XMLParser(recover=True, resolve_entities=False))
        except etree.XMLSyntaxError:
            continue
        for a in tree.iter("{*}a"):
            if set((a.get(_OPS_TYPE) or "").split()) & REF_TYPES:
                path = _resolve_href(nav.parent, a.get("href", ""), workdir)
                if path:
                    found.add(path)
    return found


def _typed_as_reference(root: etree._Element) -> bool:
    body = next(iter(root.iter("{*}body")), None)
    if body is None:
        return False
    for el in [body, *body]:
        if set((el.get(_OPS_TYPE) or "").split()) & REF_TYPES:
            return True
    return False


def _titled_as_reference(units: list[BlockUnit]) -> bool:
    for unit in units[:_HEADING_SCAN]:
        if _TITLE_RE.match(unit.source_text.strip().rstrip(".:")):
            return True
    return False


def reference_docs(
    workdir: Path,
    docs: list[Path],
    parsed: dict[Path, tuple[etree._Element, list[BlockUnit]]],
) -> set[Path]:
    """Subset of ``docs`` (spine order) that are bibliography / index.

    ``parsed`` maps doc -> (root, blocks) for docs already parsed; docs missing
    from it (e.g. finished in an earlier run) are judged by landmarks only.
    """
    marked = {d.resolve() for d in docs} & landmark_docs(workdir)
    for doc, (root, units) in parsed.items():
        if _typed_as_reference(root) or _titled_as_reference(units):
            marked.add(doc.resolve())

    # Later split parts follow a marked part in the spine.
    result: set[Path] = set()
    inherited: str | None = None
    for doc in docs:
        doc = doc.resolve()
        m = _SPLIT_RE.match(doc.stem)
        base = f"{doc.parent}/{m.group('base')}" if m else None
        if doc in marked:
            result.add(doc)
            inherited = base
        elif base is not None and base == inherited:
            result.add(doc)
        else:
            inherited = None
    return result
