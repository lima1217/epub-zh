from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from epub_zh import server
from epub_zh.config import ResolvedSettings
from epub_zh.youtube import VideoTranscript, YouTubeError, fetch_video, parse_video_id, pick_track

VID = "2Op3QLzMgSY"


@dataclass
class Snip:
    text: str
    start: float
    duration: float


@dataclass
class Lang:
    language_code: str


class IpBlocked(Exception):  # same class name the library raises
    pass


@dataclass
class Track:
    language: str
    language_code: str
    is_generated: bool
    translatable_to: list[str] = field(default_factory=list)
    snippets: list[Snip] = field(default_factory=list)
    fail: Exception | None = None

    @property
    def is_translatable(self) -> bool:
        return bool(self.translatable_to)

    @property
    def translation_languages(self) -> list[Lang]:
        return [Lang(c) for c in self.translatable_to]

    def translate(self, code: str) -> "Track":
        return Track(f"Chinese ({code})", code, self.is_generated, snippets=self.snippets, fail=self.fail)

    def fetch(self) -> list[Snip]:
        if self.fail:
            raise self.fail
        return self.snippets


SNIPS = [
    Snip("[MUSIC PLAYING]", 3.2, 2.0),
    Snip("PROFESSOR: I'd like to welcome\nyou to this course.", 5.5, 4.0),
    Snip("  ", 9.0, 1.0),
    Snip("Computer science is a terrible name.", 3700.0, 5.0),
]


def lister_of(*tracks: Track, seen: list | None = None):
    def lister(video_id: str, proxy: str | None) -> list[Any]:
        if seen is not None:
            seen.append((video_id, proxy))
        return list(tracks)

    return lister


def describer(video_id: str, proxy: str | None) -> tuple[str, str]:
    return "Lecture 1A | SICP", "MIT OpenCourseWare"


def test_parse_video_id() -> None:
    for link in (
        f"https://www.youtube.com/watch?v={VID}",
        f"https://www.youtube.com/watch?list=PL1&v={VID}&t=30s",
        f"https://youtu.be/{VID}?si=x",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://m.youtube.com/live/{VID}",
        f" {VID} ",
    ):
        assert parse_video_id(link) == VID, link
    with pytest.raises(YouTubeError):
        parse_video_id("https://www.youtube.com/@mitocw")


def test_pick_track_prefers_manual_then_language_then_translation() -> None:
    en_auto = Track("English (auto-generated)", "en", True, ["zh-Hant"])
    en = Track("English", "en", False, ["zh-Hant", "fr"])
    it_auto = Track("Italian (auto-generated)", "it", True)
    zh_tw = Track("Chinese (Taiwan)", "zh-TW", True)

    assert pick_track([en_auto, en], "auto") == (en, False)
    assert pick_track([it_auto, en_auto], "auto") == (it_auto, False)
    assert pick_track([en_auto, en], "en") == (en, False)
    assert pick_track([en_auto, zh_tw], "zh") == (zh_tw, False)
    # no Chinese track: translate the manual English one into the best target YouTube offers
    track, translated = pick_track([en_auto, en], "zh")
    assert translated and track.language_code == "zh-Hant"
    with pytest.raises(YouTubeError, match="可用的字幕：Italian"):
        pick_track([it_auto], "zh")


def test_fetch_video_builds_markdown() -> None:
    seen: list = []
    en = Track("English", "en", False, snippets=SNIPS)
    t = fetch_video(VID, "auto", proxy="http://p:1", lister=lister_of(en, seen=seen), describer=describer)
    assert seen == [(VID, "http://p:1")]
    assert t.key == f"{VID}.en" and t.download_stem == "Lecture 1A SICP.en"
    assert len(t.segments) == 3 and t.duration_seconds == 3705
    # short sentences keep joining until a paragraph reaches ~400 characters
    assert t.paragraphs() == [
        (3200, "[MUSIC PLAYING] PROFESSOR: I'd like to welcome you to this course. Computer science is a terrible name."),
    ]
    md = t.to_markdown()
    assert md.startswith("# Lecture 1A | SICP\n")
    assert "- 频道：MIT OpenCourseWare" in md
    assert "- 字幕：English　时长：01:01:45　字幕条数：3" in md
    assert "**[00:03]** [MUSIC PLAYING] PROFESSOR:" in md
    assert VideoTranscript.from_dict(t.to_dict()) == t


def test_paragraphs_break_long_unpunctuated_auto_captions() -> None:
    words = [Snip("so this is a caption line without any punctuation at all", i * 2.0, 2.0) for i in range(60)]
    t = fetch_video(VID, "en", lister=lister_of(Track("English (auto-generated)", "en", True, snippets=words)),
                    describer=describer)
    paras = t.paragraphs()
    assert len(paras) > 1 and all(len(text) <= 960 for _, text in paras)
    assert t.track_label == "English（自动生成）"


def test_blocked_translation_suggests_original() -> None:
    en = Track("English", "en", False, ["zh-Hant"], snippets=SNIPS, fail=IpBlocked("429"))
    with pytest.raises(YouTubeError, match="原始字幕"):
        fetch_video(VID, "zh", lister=lister_of(en), describer=describer)
    with pytest.raises(YouTubeError, match="代理"):
        fetch_video(VID, "en", lister=lister_of(en), describer=describer)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proxy = {"value": None}
    monkeypatch.setattr(
        server,
        "resolve_settings",
        lambda **_: ResolvedSettings(
            api_key=None, base_url=None, model="m", batch_size=1, sources={}, youtube_proxy=proxy["value"]
        ),
    )
    monkeypatch.setattr(server, "user_config_path", lambda: tmp_path / "config.toml")
    seen: list = []
    en = Track("English", "en", False, snippets=SNIPS)
    store = server.YouTubeStore(tmp_path / "youtube", lister=lister_of(en, seen=seen), describer=describer)
    httpd = server.make_server(port=0, manager=server.JobManager(tmp_path / "jobs"), stores={"youtube": store})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", proxy, seen
    httpd.shutdown()
    httpd.server_close()


def _req(base: str, path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_http_youtube_fetch_uses_configured_proxy(app) -> None:
    base, proxy, seen = app
    proxy["value"] = "http://127.0.0.1:7890"
    status, _, body = _req(base, "/api/youtube", "POST", {"url": f"https://youtu.be/{VID}", "lang": "auto"})
    assert status == 201, body
    item = json.loads(body)
    assert item["key"] == f"{VID}.en" and item["subtitle"] == "MIT OpenCourseWare · English"
    assert seen == [(VID, "http://127.0.0.1:7890")]

    [listed] = json.loads(_req(base, "/api/youtube")[2])
    assert listed["url"] == f"https://www.youtube.com/watch?v={VID}"
    status, headers, body = _req(base, f"/api/youtube/{VID}.en/download?format=txt")
    assert status == 200 and body.decode().startswith("[00:03] [MUSIC PLAYING]")
    assert "filename*=UTF-8''Lecture%201A%20SICP.en" in headers["Content-Disposition"]
    assert _req(base, f"/api/youtube/{VID}.en", "DELETE")[0] == 200
    assert json.loads(_req(base, "/api/youtube")[2]) == []


def test_http_youtube_rejects_bad_input(app) -> None:
    base, _, _ = app
    status, _, body = _req(base, "/api/youtube", "POST", {"url": "https://example.com/watch"})
    assert status == 400 and "YouTube 链接" in json.loads(body)["error"]
    status, _, body = _req(base, "/api/youtube", "POST", {"url": VID, "lang": "fr"})
    assert status == 400 and "auto / en / zh" in json.loads(body)["error"]
    assert _req(base, "/api/youtube/../../etc")[0] == 404


def test_settings_save_and_clear_youtube_proxy(app, tmp_path: Path) -> None:
    base, _, _ = app
    cfg = tmp_path / "config.toml"
    assert _req(base, "/api/settings", "POST", {"youtube_proxy": "http://127.0.0.1:7890"})[0] == 200
    assert 'youtube_proxy = "http://127.0.0.1:7890"' in cfg.read_text()
    assert _req(base, "/api/settings", "POST", {"youtube_proxy": ""})[0] == 200
    assert 'youtube_proxy = ""' in cfg.read_text()  # empty string reads back as unset
    data = json.loads(_req(base, "/api/settings")[2])
    assert "youtube_lib" in data and "youtube_proxy" in data
