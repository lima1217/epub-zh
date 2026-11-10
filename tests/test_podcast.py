from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from epub_zh import server
from epub_zh.podcast import PodcastError, Transcript, fetch_transcript, fmt_ts, parse_episode_id

EID = "6ae0dc3797726cd46758b96b"
EPISODE = {
    "eid": EID,
    "podcast_title": "独树不成林",
    "title": "372-浮士德/博士",
    "duration_seconds": 2694,
    "pub_date": "2026-10-27T17:41:14.364Z",  # 2026-10-28 in Beijing
    "media_id": "pid/abc.m4a",
    "audio_url": "https://media.xyzcdn.net/pid/abc.m4a",
}
SEGMENTS = [
    {"startMs": 0, "text": "[Music]"},
    {"startMs": 4790, "text": "大家好，欢迎来到第四期。"},
    {"startMs": 47190, "text": "除了无限的知识，"},
    {"startMs": 48710, "text": "我们还应该受到指引吗？"},
    {"startMs": 3725000, "text": "再见"},
]


def fake_xyz(calls: list[list[str]] | None = None, *, transcript: dict | None = None, fail: dict | None = None):
    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(args)
        if fail is not None:
            return subprocess.CompletedProcess(args, 2, "", json.dumps(fail))
        out = EPISODE if args[0] == "episode" else transcript or {"status": "available", "segments": SEGMENTS}
        return subprocess.CompletedProcess(args, 0, json.dumps(out, ensure_ascii=False), "")

    return run


def test_parse_episode_id() -> None:
    assert parse_episode_id(f"https://www.xiaoyuzhoufm.com/episode/{EID}?s=abc") == EID
    assert parse_episode_id(f"  {EID} ") == EID
    assert parse_episode_id(f"听听这个 https://www.xiaoyuzhoufm.com/episode/{EID} 吧") == EID
    with pytest.raises(PodcastError):
        parse_episode_id("https://www.xiaoyuzhoufm.com/podcast/64acd33c7a3d479103fbd32d")


def test_fetch_uses_media_id_and_merges_paragraphs() -> None:
    calls: list[list[str]] = []
    t = fetch_transcript(EID, runner=fake_xyz(calls))
    assert calls == [["episode", EID], ["transcript", EID, "--media-id", "pid/abc.m4a", "--format", "segments"]]
    assert t.paragraphs() == [
        (0, "[Music]"),
        (4790, "大家好，欢迎来到第四期。"),
        (47190, "除了无限的知识，我们还应该受到指引吗？"),
        (3725000, "再见"),
    ]
    md = t.to_markdown()
    assert md.startswith("# 372-浮士德/博士\n")
    assert "- 发布：2026-10-28　时长：44:54　字幕段数：5" in md
    assert "**[00:47]** 除了无限的知识，我们还应该受到指引吗？" in md
    assert t.to_timestamped().splitlines()[-1] == "[01:02:05] 再见"
    assert fmt_ts(59_999) == "00:59"


def test_no_subtitle_and_login_errors() -> None:
    with pytest.raises(PodcastError, match="还没有官方字幕"):
        fetch_transcript(EID, runner=fake_xyz(transcript={"status": "no_subtitle"}))
    with pytest.raises(PodcastError, match="xyz login"):
        fetch_transcript(EID, runner=fake_xyz(fail={"error": "auth_expired", "message": "x"}))
    with pytest.raises(PodcastError, match="boom"):
        fetch_transcript(EID, runner=fake_xyz(fail={"error": "api_error", "message": "boom"}))


@pytest.fixture
def app(tmp_path: Path):
    manager = server.JobManager(tmp_path / "jobs")
    store = server.PodcastStore(tmp_path / "podcasts", runner=fake_xyz())
    httpd = server.make_server(port=0, manager=manager, podcasts=store)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", store
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


def test_http_fetch_list_download_delete(app) -> None:
    base, store = app
    status, _, body = _req(base, "/api/podcasts", "POST", {"url": f"https://www.xiaoyuzhoufm.com/episode/{EID}"})
    assert status == 201, body
    assert json.loads(body)["segment_count"] == 5

    status, _, body = _req(base, "/api/podcasts")
    [item] = json.loads(body)
    assert item["eid"] == EID and item["date"] == "2026-10-28"

    status, _, body = _req(base, f"/api/podcasts/{EID}")
    assert json.loads(body)["markdown"].startswith("# 372-")

    status, headers, body = _req(base, f"/api/podcasts/{EID}/download?format=md")
    assert status == 200 and headers["Content-Type"].startswith("text/markdown")
    assert "filename*=UTF-8''372-" in headers["Content-Disposition"]
    assert "%2F" not in headers["Content-Disposition"]  # '/' in the title is not a path
    status, headers, body = _req(base, f"/api/podcasts/{EID}/download?format=txt")
    assert body.decode().startswith("[00:00] [Music]\n")

    # survives a restart: a fresh store reads the same file
    assert server.PodcastStore(store.dir).get(EID) == Transcript.from_dict(json.loads(store._path(EID).read_text()))

    assert _req(base, f"/api/podcasts/{EID}", "DELETE")[0] == 200
    assert _req(base, f"/api/podcasts/{EID}")[0] == 404


def test_http_bad_link_and_traversal(app) -> None:
    base, _ = app
    status, _, body = _req(base, "/api/podcasts", "POST", {"url": "https://example.com"})
    assert status == 400 and "单集链接" in json.loads(body)["error"]
    assert _req(base, "/api/podcasts/..%2F..%2Fetc")[0] == 404
    assert _req(base, f"/api/podcasts/{EID}/download?format=exe")[0] == 404  # not fetched yet


def test_settings_report_xyz_install(app, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base, _ = app
    monkeypatch.setattr(server, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(server, "find_xyz", lambda: None)
    data = json.loads(_req(base, "/api/settings")[2])
    assert data["xyz_path"] is None and data["xyz_repo"] == "https://github.com/lima1217/xiaoyuzhou"
    monkeypatch.setattr(server, "find_xyz", lambda: "/opt/bin/xyz")
    assert json.loads(_req(base, "/api/settings")[2])["xyz_path"] == "/opt/bin/xyz"


def test_missing_xyz_points_to_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    from epub_zh import podcast

    monkeypatch.setattr(podcast, "find_xyz", lambda: None)
    with pytest.raises(PodcastError, match="github.com/lima1217/xiaoyuzhou"):
        fetch_transcript(EID)
