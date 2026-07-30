from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from epub_zh.epub_io import unpack_epub


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_unpack_rejects_zip_slip(tmp_path: Path) -> None:
    evil = tmp_path / "evil.epub"
    evil.write_bytes(_zip_bytes({"../../tmp/pwned.txt": b"pwned"}))
    workdir = tmp_path / "work"
    with pytest.raises(ValueError, match="Unsafe path"):
        unpack_epub(evil, workdir)


def test_unpack_extracts_safe_members(tmp_path: Path) -> None:
    src = tmp_path / "ok.epub"
    src.write_bytes(
        _zip_bytes(
            {
                "mimetype": b"application/epub+zip",
                "META-INF/container.xml": b"<container/>",
                "OEBPS/ch1.xhtml": b"<html/>",
            }
        )
    )
    workdir = tmp_path / "work"
    unpack_epub(src, workdir)
    assert (workdir / "mimetype").read_bytes() == b"application/epub+zip"
    assert (workdir / "OEBPS" / "ch1.xhtml").exists()
