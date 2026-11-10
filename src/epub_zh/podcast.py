"""Xiaoyuzhou (小宇宙) podcast transcripts via the separately installed ``xyz`` CLI.

``xyz`` owns login and the Xiaoyuzhou API; this module only shells out to it:
``xyz episode <eid>`` for title / media id, then ``xyz transcript <eid>
--media-id <mid> --format segments`` for the official transcript. Segments are
glued into paragraphs and rendered as Markdown or timestamped text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_EID = re.compile(r"^[0-9a-f]{24}$")
_EID_IN_URL = re.compile(r"xiaoyuzhoufm\.com/episode/([0-9a-f]{24})")
_SENTENCE_END = tuple("。？！?!.…；;”’」』）)]")
CN_TZ = timezone(timedelta(hours=8))
XYZ_REPO = "https://github.com/lima1217/xiaoyuzhou"
LOGIN_HINT = "在终端运行 `xyz send-code <手机号>`，收到短信后运行 `xyz login <手机号> <验证码>`，再回来重试"

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class PodcastError(Exception):
    pass


@dataclass
class Transcript:
    eid: str
    title: str
    podcast_title: str
    pub_date: str  # UTC ISO, as xyz returns it
    duration_seconds: int
    audio_url: str
    segments: list[dict[str, Any]] = field(default_factory=list)  # [{startMs, text}]
    fetched: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transcript":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def episode_url(self) -> str:
        return f"https://www.xiaoyuzhoufm.com/episode/{self.eid}"

    @property
    def local_date(self) -> str:
        try:
            dt = datetime.fromisoformat(self.pub_date.replace("Z", "+00:00"))
        except ValueError:
            return self.pub_date[:10]
        return dt.astimezone(CN_TZ).strftime("%Y-%m-%d")

    def paragraphs(self) -> list[tuple[int, str]]:
        """Join a segment into the next while it stops mid-sentence (e.g. ends in '，')."""
        out: list[tuple[int, str]] = []
        start: int | None = None
        buf = ""
        for seg in self.segments:
            if start is None:
                start = int(seg["startMs"])
            buf += seg["text"]
            if buf.endswith(_SENTENCE_END) or buf.startswith("["):
                out.append((start, buf))
                start, buf = None, ""
        if buf:
            out.append((start or 0, buf))
        return out

    def to_markdown(self) -> str:
        lines = [
            f"# {self.title}",
            "",
            f"- 播客：{self.podcast_title}",
            f"- 发布：{self.local_date}　时长：{fmt_ts(self.duration_seconds * 1000)}　字幕段数：{len(self.segments)}",
            f"- 单集：{self.episode_url}",
        ]
        if self.audio_url:
            lines.append(f"- 音频：{self.audio_url}")
        lines += ["", "> 字幕来自小宇宙官方转录（`xyz transcript`），已按句合并为段落，时间码为该段起点。", ""]
        for ms, text in self.paragraphs():
            lines += [f"**[{fmt_ts(ms)}]** {text}", ""]
        return "\n".join(lines)

    def to_timestamped(self) -> str:
        return "".join(f"[{fmt_ts(ms)}] {text}\n" for ms, text in self.paragraphs())


def fmt_ts(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def parse_episode_id(text: str) -> str:
    """Episode id from a share link (``https://www.xiaoyuzhoufm.com/episode/<eid>``) or a bare id."""
    text = (text or "").strip()
    m = _EID_IN_URL.search(text)
    if m:
        return m.group(1)
    if _EID.match(text):
        return text
    raise PodcastError("没认出单集链接，应形如 https://www.xiaoyuzhoufm.com/episode/<24 位 id>")


def find_xyz() -> str | None:
    """``$EPUB_ZH_XYZ``, then ``xyz`` on PATH, then ``~/.local/bin/xyz`` (uv tool installs)."""
    explicit = os.environ.get("EPUB_ZH_XYZ")
    if explicit:
        return explicit
    found = shutil.which("xyz") or shutil.which("xiaoyuzhou")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "xyz"
    return str(fallback) if fallback.is_file() else None


def run_xyz(args: list[str]) -> "subprocess.CompletedProcess[str]":
    exe = find_xyz()
    if exe is None:
        raise PodcastError(f"找不到小宇宙命令行工具 xyz，请按 {XYZ_REPO} 的说明安装并登录（也可在“设置”里查看）")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as exc:
        raise PodcastError("xyz 超过 90 秒没有返回，请稍后重试") from exc


def _call(runner: Runner, args: list[str]) -> dict[str, Any]:
    proc = runner(args)
    if proc.returncode != 0:
        try:
            err = json.loads(proc.stderr.strip().splitlines()[-1])
        except (ValueError, IndexError):
            err = {"error": "unexpected", "message": proc.stderr.strip() or f"exit {proc.returncode}"}
        kind = err.get("error", "")
        if kind in {"not_logged_in", "auth_expired"}:
            raise PodcastError(f"小宇宙未登录或登录已过期。{LOGIN_HINT}")
        raise PodcastError(f"xyz {args[0]} 失败：{err.get('message', kind)}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise PodcastError(f"xyz {args[0]} 输出不是 JSON：{proc.stdout[:200]!r}") from exc


def fetch_transcript(eid: str, *, runner: Runner = run_xyz) -> Transcript:
    ep = _call(runner, ["episode", eid])
    media_id = ep.get("media_id")
    if not media_id:
        raise PodcastError("这一集没有音频 media id，拿不到字幕")
    tr = _call(runner, ["transcript", eid, "--media-id", media_id, "--format", "segments"])
    if tr.get("status") != "available":
        raise PodcastError("这一集还没有官方字幕（小宇宙未生成文稿）")
    return Transcript(
        eid=eid,
        title=ep.get("title") or eid,
        podcast_title=ep.get("podcast_title") or "",
        pub_date=ep.get("pub_date") or "",
        duration_seconds=int(ep.get("duration_seconds") or 0),
        audio_url=ep.get("audio_url") or "",
        segments=[{"startMs": int(s["startMs"]), "text": s["text"]} for s in tr.get("segments", [])],
    )
