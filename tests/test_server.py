from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest

from epub_zh import pipeline, server
from epub_zh.config import ResolvedSettings
from epub_zh.epub_io import JobState
from epub_zh.pipeline import JobProgress, TranslationFailed, run_translate


def _epub_bytes(paragraphs: list[str], title: str = "测试书") -> bytes:
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:identifier id="uid">id</dc:identifier>
  </metadata>
  <manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", xhtml)
    return buf.getvalue()


class FakeTranslator:
    gate: threading.Event | None = None  # when set on the class, each batch waits on it
    fail: bool = False

    def __init__(self, **_: Any) -> None:
        pass

    def translate_batch(self, texts: list[str]) -> list[str]:
        if FakeTranslator.gate is not None:
            assert FakeTranslator.gate.wait(5)
        if FakeTranslator.fail:
            raise RuntimeError("boom")
        return [f"中:{t}" for t in texts]


def _settings(batch_size: int = 8) -> ResolvedSettings:
    return ResolvedSettings(
        api_key="sk-test-1234567890",
        base_url="http://example.invalid/v1",
        model="m",
        batch_size=batch_size,
        sources={},
    )


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    FakeTranslator.gate = None
    FakeTranslator.fail = False
    monkeypatch.setattr(pipeline, "Translator", FakeTranslator)
    monkeypatch.setattr(server, "resolve_settings", lambda **_: _settings(batch_size=1))
    monkeypatch.setattr(server, "user_config_path", lambda: tmp_path / "config.toml")


@pytest.fixture
def app(tmp_path: Path):
    manager = server.JobManager(tmp_path / "jobs")
    httpd = server.make_server(port=0, manager=manager)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, manager
    httpd.shutdown()
    httpd.server_close()


def _req(base: str, path: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _wait_status(base: str, job_id: str, want: str, timeout: float = 5) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, _, body = _req(base, "/api/jobs")
        job = next(j for j in json.loads(body) if j["id"] == job_id)
        if job["status"] == want:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job never reached {want}: {job}")


def _upload(base: str, data: bytes, name: str = "My Book.epub", mode: str = "zh", skip_refs: bool = False) -> dict:
    q = urllib.parse.urlencode({"filename": name, "mode": mode, "skip_refs": "1" if skip_refs else "0"})
    status, _, body = _req(base, f"/api/jobs?{q}", "POST", data)
    assert status == 201, body
    return json.loads(body)


# ---- pipeline hooks ---------------------------------------------------------


def test_run_translate_reports_progress(tmp_path: Path) -> None:
    src = tmp_path / "b.epub"
    src.write_bytes(_epub_bytes(["One.", "Two.", "Three."]))
    seen: list[JobProgress] = []
    run_translate(
        source=src, output=tmp_path / "o.epub", mode="zh", settings=_settings(batch_size=2), on_progress=seen.append
    )
    assert [(p.blocks_done, p.blocks_total) for p in seen] == [(0, 3), (2, 3), (3, 3)]
    assert seen[-1].docs_done == 1 and seen[-1].fraction == 1.0


def test_failure_raises_translation_failed_and_saves_state(tmp_path: Path) -> None:
    src = tmp_path / "b.epub"
    src.write_bytes(_epub_bytes(["One."]))
    FakeTranslator.fail = True
    out = tmp_path / "o.epub"
    with pytest.raises(TranslationFailed) as info:
        run_translate(source=src, output=out, mode="zh", settings=_settings())
    assert info.value.doc == "OEBPS/ch1.xhtml"
    state = JobState.load(out.parent / ".epub-zh-state" / "o" / "state.json")
    assert state.failed_doc == "OEBPS/ch1.xhtml" and state.error == "boom"


# ---- HTTP -------------------------------------------------------------------


def test_index_page_served(app) -> None:
    base, _ = app
    status, headers, body = _req(base, "/")
    assert status == 200 and "text/html" in headers["Content-Type"]
    assert "把 EPUB 文件拖到这里" in body.decode()


def test_upload_translate_download(app) -> None:
    base, manager = app
    job = _upload(
        base, _epub_bytes(["Hello there."], title="Dream"), name="My Book.epub", mode="bilingual", skip_refs=True
    )
    assert job["download_name"] == "My Book（中英对照）.epub"  # before the title is translated
    assert job["skip_refs"] is True
    assert json.loads((manager.jobs_dir / job["id"] / "meta.json").read_text())["skip_refs"] is True
    done = _wait_status(base, job["id"], "done")
    assert done["percent"] == 100 and done["download_name"] == "中 Dream（中英对照）.epub"

    status, headers, body = _req(base, f"/api/jobs/{job['id']}/download")
    assert status == 200
    assert headers["Content-Type"] == "application/epub+zip"
    assert "filename*=UTF-8''%E4%B8%AD%20Dream%EF%BC%88" in headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        xhtml = zf.read("OEBPS/ch1.xhtml").decode()
    assert "Hello there." in xhtml and "中:Hello there." in xhtml


def test_cancel_then_resume(app) -> None:
    base, _ = app
    FakeTranslator.gate = threading.Event()
    job = _upload(base, _epub_bytes(["One.", "Two."]))
    _wait_status(base, job["id"], "running")
    assert _req(base, f"/api/jobs/{job['id']}/cancel", "POST")[0] == 200
    FakeTranslator.gate.set()
    _wait_status(base, job["id"], "cancelled")
    assert _req(base, f"/api/jobs/{job['id']}/download")[0] == 409

    assert _req(base, f"/api/jobs/{job['id']}/resume", "POST")[0] == 200
    _wait_status(base, job["id"], "done")


def test_failed_job_shows_error_and_delete(app) -> None:
    base, manager = app
    FakeTranslator.fail = True
    job = _upload(base, _epub_bytes(["One."]))
    failed = _wait_status(base, job["id"], "failed")
    assert failed["error"] == "boom"
    job_dir = manager.jobs_dir / job["id"]
    assert job_dir.is_dir()
    assert _req(base, f"/api/jobs/{job['id']}", "DELETE")[0] == 200
    assert not job_dir.exists()


def test_rejects_non_epub_and_bad_mode(app) -> None:
    base, _ = app
    q = urllib.parse.urlencode({"filename": "x.txt"})
    assert _req(base, f"/api/jobs?{q}", "POST", b"PK..")[0] == 400
    q = urllib.parse.urlencode({"filename": "x.epub"})
    assert _req(base, f"/api/jobs?{q}", "POST", b"not a zip")[0] == 400
    q = urllib.parse.urlencode({"filename": "x.epub", "mode": "fr"})
    assert _req(base, f"/api/jobs?{q}", "POST", _epub_bytes(["a"]))[0] == 400


def test_rejects_cross_origin_and_foreign_host(app) -> None:
    base, _ = app
    q = urllib.parse.urlencode({"filename": "x.epub"})
    status, _, _ = _req(base, f"/api/jobs?{q}", "POST", _epub_bytes(["a"]), {"Origin": "https://evil.example"})
    assert status == 403
    status, _, _ = _req(base, "/api/jobs", headers={"Host": "evil.example"})
    assert status == 403


def test_unknown_job_id_404(app) -> None:
    base, _ = app
    assert _req(base, "/api/jobs/../../etc/download")[0] == 404
    assert _req(base, "/api/jobs/zzzzzzzzzzzz/download")[0] == 404


def test_settings_write_never_echo_key(app, tmp_path: Path) -> None:
    base, _ = app
    payload = json.dumps({"api_key": "sk-secret-abcdefgh", "model": "other", "base_url": ""}).encode()
    status, _, body = _req(base, "/api/settings", "POST", payload, {"Content-Type": "application/json"})
    assert status == 200
    assert b"sk-secret-abcdefgh" not in body
    text = (tmp_path / "config.toml").read_text()
    assert 'api_key = "sk-secret-abcdefgh"' in text and 'model = "other"' in text
    assert "base_url" not in text  # blank fields are left alone


def test_restart_restores_jobs(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    first = server.JobManager(jobs_dir)
    done = first.create(filename="a.epub", mode="zh", data=_epub_bytes(["One."]))
    deadline = time.time() + 5
    while first.get(done.id).status != "done" and time.time() < deadline:
        time.sleep(0.02)
    assert first.get(done.id).status == "done"
    # a job interrupted mid-run (stand-in for the server being stopped)
    FakeTranslator.gate = threading.Event()
    pending = first.create(filename="b.epub", mode="zh", data=_epub_bytes(["Two."]))
    while first.get(pending.id).status != "running" and time.time() < deadline:
        time.sleep(0.02)
    first.cancel(pending.id)
    FakeTranslator.gate.set()
    while first.get(pending.id).status != "cancelled" and time.time() < deadline:
        time.sleep(0.02)
    assert first.get(pending.id).status == "cancelled"

    second = server.JobManager(jobs_dir)
    assert second.get(done.id).status == "done"
    assert second.get(pending.id).status == "failed"
    assert second.get(pending.id).error  # explains how to continue
    second.resume(pending.id)
    deadline = time.time() + 5
    while second.get(pending.id).status != "done" and time.time() < deadline:
        time.sleep(0.02)
    assert second.get(pending.id).status == "done"
