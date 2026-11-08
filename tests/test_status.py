from __future__ import annotations

import json
from pathlib import Path

from epub_zh.epub_io import JobState


def _state(tmp_path: Path, **kw: object) -> JobState:
    base = dict(
        source="in.epub",
        output=str(tmp_path / "out.epub"),
        mode="zh",
        model="m",
        base_url="",
        workdir=str(tmp_path / "work"),
    )
    base.update(kw)
    return JobState(**base)  # type: ignore[arg-type]


def test_status_running(tmp_path: Path) -> None:
    s = _state(tmp_path, completed_docs=["a", "b"], total_docs=5)
    assert s.status_line() == "running 2/5"


def test_status_failed(tmp_path: Path) -> None:
    s = _state(tmp_path, completed_docs=["a"], total_docs=5, failed_doc="b", error="boom")
    assert s.status_line() == "failed 1/5 on b: boom"


def test_status_complete_needs_output_file(tmp_path: Path) -> None:
    s = _state(tmp_path, completed_docs=["a"], total_docs=1)
    assert s.status_line() == "running 1/1"  # packing not finished yet
    Path(s.output).write_bytes(b"x")
    assert s.status_line().startswith("complete 1/1")


def test_old_state_without_total_loads(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _state(tmp_path, completed_docs=["a"]).save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["total_docs"]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = JobState.load(path)
    assert loaded.total_docs is None
    assert loaded.status_line() == "running 1/?"
    Path(loaded.output).write_bytes(b"x")
    assert loaded.status_line().startswith("complete 1/?")
