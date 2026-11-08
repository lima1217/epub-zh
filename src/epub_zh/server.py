"""Local web UI: drag an EPUB onto a page, translate it, save the result.

Stdlib only (``http.server``). Binds to loopback; jobs live on disk under
``jobs_dir`` so a server restart keeps finished files and can resume
unfinished ones. One worker thread runs jobs in upload order.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from epub_zh.config import (
    format_settings_rows,
    resolve_settings,
    set_config_value,
    user_config_path,
)
from epub_zh.epub_io import JobState, state_dir_for
from epub_zh.pipeline import JobProgress, TranslationFailed, run_resume, run_translate
from epub_zh.translate import list_models

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MODES = {"zh", "bilingual"}
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")
_SETTABLE = ("api_key", "base_url", "model", "batch_size")


def default_jobs_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "epub-zh" / "jobs"


class JobCancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    filename: str
    mode: str
    created: float
    dir: Path
    status: str = "queued"  # queued | running | done | failed | cancelled
    error: str | None = None
    progress: JobProgress | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def source(self) -> Path:
        return self.dir / "source.epub"

    @property
    def output(self) -> Path:
        return self.dir / "output.epub"

    @property
    def state_path(self) -> Path:
        return state_dir_for(self.output) / "state.json"

    @property
    def download_name(self) -> str:
        stem = self.filename[:-5] if self.filename.lower().endswith(".epub") else self.filename
        suffix = "zh" if self.mode == "zh" else "bilingual"
        return f"{stem}.{suffix}.epub"

    def save_meta(self) -> None:
        meta = {"id": self.id, "filename": self.filename, "mode": self.mode, "created": self.created}
        (self.dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        p = self.progress
        percent = 0.0
        if self.status == "done":
            percent = 100.0
        elif p and p.docs_total:
            within = p.blocks_done / p.blocks_total if p.blocks_total else 0.0
            percent = min(99.9, 100.0 * (p.docs_done + within) / p.docs_total)
        return {
            "id": self.id,
            "filename": self.filename,
            "download_name": self.download_name,
            "mode": self.mode,
            "created": self.created,
            "status": self.status,
            "error": self.error,
            "percent": round(percent, 1),
            "docs_done": p.docs_done if p else 0,
            "docs_total": p.docs_total if p else 0,
            "doc": p.doc if p else None,
        }


def _clean_filename(raw: str) -> str:
    name = Path(raw.replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"/\\')
    return name.strip() or "book.epub"


class JobManager:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self._load_existing()
        self.worker = threading.Thread(target=self._work, name="epub-zh-worker", daemon=True)
        self.worker.start()

    # ---- disk -------------------------------------------------------------

    def _load_existing(self) -> None:
        for meta_path in sorted(self.jobs_dir.glob("*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                job = Job(
                    id=meta["id"],
                    filename=meta["filename"],
                    mode=meta["mode"],
                    created=float(meta["created"]),
                    dir=meta_path.parent,
                )
            except (OSError, ValueError, KeyError):
                continue
            if not _JOB_ID.match(job.id) or job.dir.name != job.id:
                continue
            state = self._read_state(job)
            if job.output.is_file() and state and state.failed_doc is None:
                job.status = "done"
            else:
                job.status = "failed"
                job.error = (state.error if state and state.error else None) or "服务重启前未完成，可点“继续”"
            if state and state.total_docs:
                job.progress = JobProgress(len(state.completed_docs), state.total_docs, None, 0, 0)
            self.jobs[job.id] = job

    @staticmethod
    def _read_state(job: Job) -> JobState | None:
        try:
            return JobState.load(job.state_path)
        except (OSError, ValueError, TypeError):
            return None

    # ---- public API -------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
            return [j.to_dict() for j in jobs]

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def create(self, *, filename: str, mode: str, data: bytes) -> Job:
        if mode not in MODES:
            raise ValueError("mode must be 'zh' or 'bilingual'")
        filename = _clean_filename(filename)
        if not filename.lower().endswith(".epub"):
            raise ValueError("只支持 .epub 文件")
        if not data.startswith(b"PK"):
            raise ValueError("文件不是有效的 EPUB（不是 zip 格式）")
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True)
        job = Job(id=job_id, filename=filename, mode=mode, created=time.time(), dir=job_dir)
        job.source.write_bytes(data)
        job.save_meta()
        with self.lock:
            self.jobs[job_id] = job
        self.queue.put(job_id)
        return job

    def resume(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs[job_id]
            if job.status not in {"failed", "cancelled"}:
                raise ValueError(f"任务状态为 {job.status}，无法继续")
            job.status = "queued"
            job.error = None
            job.cancel.clear()
        self.queue.put(job_id)
        return job

    def cancel(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs[job_id]
            if job.status == "queued":
                job.status = "cancelled"
            elif job.status == "running":
                job.cancel.set()
            else:
                raise ValueError(f"任务状态为 {job.status}，无法取消")
        return job

    def delete(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job.status == "running":
                raise ValueError("任务正在运行，请先取消")
            del self.jobs[job_id]
        shutil.rmtree(job.dir, ignore_errors=True)

    # ---- worker -----------------------------------------------------------

    def _work(self) -> None:
        while True:
            job_id = self.queue.get()
            with self.lock:
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                job.status = "running"
            self._run(job)

    def _run(self, job: Job) -> None:
        def on_progress(p: JobProgress) -> None:
            job.progress = p
            if job.cancel.is_set():
                raise JobCancelled()

        try:
            settings = resolve_settings()
            if not settings.api_key:
                raise RuntimeError("还没有配置 API Key，请在“设置”里填写")
            state = self._read_state(job)
            if state is not None and Path(state.workdir).is_dir():
                run_resume(state_path=job.state_path, api_key=settings.api_key, on_progress=on_progress)
            else:
                run_translate(
                    source=job.source,
                    output=job.output,
                    mode=job.mode,
                    settings=settings,
                    on_progress=on_progress,
                )
        except Exception as exc:  # noqa: BLE001 — surface any failure in the UI
            cancelled = isinstance(exc, JobCancelled) or isinstance(exc.__cause__, JobCancelled)
            with self.lock:
                job.status = "cancelled" if cancelled else "failed"
                job.error = None if cancelled else str(exc.__cause__ or exc)
            return
        with self.lock:
            job.status = "done"
            job.error = None


# ---- HTTP -------------------------------------------------------------------


def _index_html() -> bytes:
    return resources.files("epub_zh").joinpath("web/index.html").read_bytes()


def _settings_payload() -> dict[str, Any]:
    settings = resolve_settings()
    rows = {name: {"value": value, "source": src} for name, value, src in format_settings_rows(settings)}
    return {
        "has_key": bool(settings.api_key),
        "model": settings.model,
        "base_url": settings.base_url,
        "batch_size": settings.batch_size,
        "rows": rows,
        "config_path": str(user_config_path()),
    }


class Handler(BaseHTTPRequestHandler):
    manager: JobManager  # set by make_server
    server_version = "epub-zh"

    # quiet: the page polls /api/jobs every second or two
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # ---- guards -----------------------------------------------------------

    def _host_ok(self) -> bool:
        """Reject DNS-rebinding (Host) and cross-site writes (Origin)."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if self.headers.get("Host", "") not in allowed:
            return False
        if self.command not in {"GET", "HEAD"}:
            origin = self.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc not in allowed:
                return False
        return True

    # ---- responses --------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _read_body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.LENGTH_REQUIRED, "缺少 Content-Length")
            return None
        if length < 0 or length > limit:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"文件超过 {limit // 1024 // 1024} MB 上限")
            return None
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any] | None:
        body = self._read_body(64 * 1024)
        if body is None:
            return None
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "请求体不是 JSON")
            return None
        if not isinstance(data, dict):
            self._error(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")
            return None
        return data

    # ---- routing ----------------------------------------------------------

    def _route(self) -> tuple[list[str], dict[str, list[str]]]:
        url = urlsplit(self.path)
        return [p for p in url.path.split("/") if p], parse_qs(url.query)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "forbidden host")
        parts, _ = self._route()
        if parts == []:
            return self._send(200, _index_html(), "text/html; charset=utf-8")
        if parts == ["api", "jobs"]:
            return self._json(self.manager.list())
        if parts == ["api", "settings"]:
            return self._json(_settings_payload())
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "download":
            job = self._job(parts[2])
            if job is None:
                return
            if job.status != "done" or not job.output.is_file():
                return self._error(HTTPStatus.CONFLICT, "译文还没有生成")
            name = job.download_name
            ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
            disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
            return self._send(
                200, job.output.read_bytes(), "application/epub+zip", {"Content-Disposition": disposition}
            )
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "forbidden origin")
        parts, query = self._route()
        if parts == ["api", "jobs"]:
            mode = (query.get("mode") or ["zh"])[0]
            filename = (query.get("filename") or ["book.epub"])[0]
            data = self._read_body(MAX_UPLOAD_BYTES)
            if data is None:
                return
            try:
                job = self.manager.create(filename=filename, mode=mode, data=data)
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return self._json(job.to_dict(), HTTPStatus.CREATED)
        if parts == ["api", "settings"]:
            return self._save_settings()
        if parts == ["api", "settings", "check"]:
            return self._check_settings()
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] in {"resume", "cancel"}:
            job = self._job(parts[2])
            if job is None:
                return
            action = self.manager.resume if parts[3] == "resume" else self.manager.cancel
            try:
                return self._json(action(job.id).to_dict())
            except ValueError as exc:
                return self._error(HTTPStatus.CONFLICT, str(exc))
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "forbidden origin")
        parts, _ = self._route()
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job = self._job(parts[2])
            if job is None:
                return
            try:
                self.manager.delete(job.id)
            except ValueError as exc:
                return self._error(HTTPStatus.CONFLICT, str(exc))
            return self._json({"ok": True})
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _job(self, job_id: str) -> Job | None:
        job = self.manager.get(job_id) if _JOB_ID.match(job_id) else None
        if job is None:
            self._error(HTTPStatus.NOT_FOUND, "任务不存在")
        return job

    # ---- settings ---------------------------------------------------------

    def _save_settings(self) -> None:
        data = self._read_json()
        if data is None:
            return
        path = user_config_path()
        try:
            for name in _SETTABLE:
                value = data.get(name)
                if value is None or str(value).strip() == "":
                    continue  # blank = keep current (the key is never sent back to the page)
                set_config_value(path, name, str(value).strip())
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        self._json(_settings_payload())

    def _check_settings(self) -> None:
        settings = resolve_settings()
        if not settings.api_key:
            return self._json({"ok": False, "message": "还没有配置 API Key"})
        try:
            models = list_models(api_key=settings.api_key, base_url=settings.base_url)
        except Exception as exc:  # noqa: BLE001 — report any gateway/auth failure
            return self._json({"ok": False, "message": f"连接失败：{exc}"})
        if settings.model in models:
            return self._json({"ok": True, "message": f"连接正常，模型 {settings.model} 可用"})
        shown = ", ".join(models[:20]) or "(无)"
        self._json({"ok": False, "message": f"连接成功，但模型 {settings.model} 不在可用列表中：{shown}"})


def make_server(*, port: int, manager: JobManager, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"manager": manager})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def bind_server(*, port: int, jobs_dir: Path, tries: int = 10) -> ThreadingHTTPServer:
    """Bind ``port``, or the next free one up to ``tries`` ports above it."""
    manager = JobManager(jobs_dir)
    last: OSError | None = None
    for candidate in range(port, port + tries):
        try:
            return make_server(port=candidate, manager=manager)
        except OSError as exc:
            last = exc
    raise OSError(f"ports {port}-{port + tries - 1} are all in use") from last
