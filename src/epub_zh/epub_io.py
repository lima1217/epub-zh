from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

# EPUB container namespaces
CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _is_within(root: Path, candidate: Path) -> bool:
    """True if candidate resolves inside root (after resolve)."""
    root_r = root.resolve()
    cand_r = candidate.resolve()
    return cand_r == root_r or root_r in cand_r.parents


def _assert_safe_zip_member(filename: str) -> None:
    """Reject absolute paths and .. traversal in ZIP member names (zip slip)."""
    name = filename.replace("\\", "/")
    if name.startswith("/") or Path(name).is_absolute():
        raise ValueError(f"Unsafe path in EPUB archive: {filename!r}")
    parts = Path(name).parts
    if ".." in parts:
        raise ValueError(f"Unsafe path in EPUB archive: {filename!r}")


def _safe_extractall(zf: zipfile.ZipFile, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    for info in zf.infolist():
        _assert_safe_zip_member(info.filename)
        target = (dest / info.filename).resolve()
        if not _is_within(dest_resolved, target):
            raise ValueError(f"Zip slip blocked for member: {info.filename!r}")
        if info.is_dir() or info.filename.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


@dataclass
class JobState:
    source: str
    output: str
    mode: str
    model: str
    base_url: str
    workdir: str
    completed_docs: list[str] = field(default_factory=list)
    failed_doc: str | None = None
    error: str | None = None
    batch_size: int = 8

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> JobState:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


def unpack_epub(source: Path, workdir: Path) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    with zipfile.ZipFile(source, "r") as zf:
        _safe_extractall(zf, workdir)


def pack_epub(workdir: Path, output: Path) -> None:
    """Write EPUB with mimetype stored first and uncompressed."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    mimetype = workdir / "mimetype"
    with zipfile.ZipFile(output, "w") as zf:
        if mimetype.exists():
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(workdir.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(workdir).as_posix()
            if arcname == "mimetype":
                continue
            zf.write(path, arcname, compress_type=zipfile.ZIP_DEFLATED)


def _find_opf(workdir: Path) -> Path:
    workdir_r = workdir.resolve()
    container = workdir / "META-INF" / "container.xml"
    if container.exists():
        root = ET.parse(container).getroot()
        rootfile = root.find("c:rootfiles/c:rootfile", CONTAINER_NS)
        if rootfile is not None:
            full_path = rootfile.attrib.get("full-path")
            if full_path:
                if ".." in Path(full_path).parts or Path(full_path).is_absolute():
                    raise ValueError(f"Unsafe OPF path in container.xml: {full_path!r}")
                opf = (workdir / full_path).resolve()
                if opf.exists() and _is_within(workdir_r, opf):
                    return opf
                if opf.exists():
                    raise ValueError(f"OPF path escapes EPUB workdir: {full_path!r}")
    matches = [p for p in workdir.rglob("*.opf") if _is_within(workdir_r, p)]
    if not matches:
        raise FileNotFoundError("No OPF package document found in EPUB")
    return matches[0]


def list_content_docs(workdir: Path) -> list[Path]:
    """Return spine document paths in reading order, falling back to all XHTML/HTML."""
    workdir_r = workdir.resolve()
    opf = _find_opf(workdir)
    if not _is_within(workdir_r, opf):
        raise ValueError(f"OPF outside EPUB workdir: {opf}")
    opf_dir = opf.parent
    tree = ET.parse(opf)
    root = tree.getroot()

    # Strip namespace for local-name matching when prefixes vary
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    manifest: dict[str, str] = {}
    for item in root.iter():
        if local(item.tag) != "item":
            continue
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media = item.attrib.get("media-type", "")
        if item_id and href and media in {
            "application/xhtml+xml",
            "text/html",
            "application/x-dtbook+xml",
        }:
            manifest[item_id] = href

    docs: list[Path] = []
    seen: set[Path] = set()
    for itemref in root.iter():
        if local(itemref.tag) != "itemref":
            continue
        idref = itemref.attrib.get("idref")
        if not idref or idref not in manifest:
            continue
        path = (opf_dir / manifest[idref]).resolve()
        if not _is_within(workdir_r, path):
            raise ValueError(f"Content doc path escapes EPUB workdir: {manifest[idref]!r}")
        if path.exists() and path not in seen:
            docs.append(path)
            seen.add(path)

    if docs:
        return docs

    # Fallback: every xhtml/html under the package
    for pattern in ("*.xhtml", "*.html", "*.htm"):
        for path in sorted(opf_dir.rglob(pattern)):
            resolved = path.resolve()
            if not _is_within(workdir_r, resolved):
                continue
            if resolved not in seen:
                docs.append(resolved)
                seen.add(resolved)
    if not docs:
        raise FileNotFoundError("No XHTML/HTML content documents found in EPUB")
    return docs


def state_dir_for(output: Path) -> Path:
    return output.parent / ".epub-zh-state" / output.stem
