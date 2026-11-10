"""YouTube captions via ``youtube-transcript-api`` (the method the local
youtube-transcript skill uses), plus the title and channel from oEmbed.

``lang`` picks the track: ``auto`` = the first manually made track, else the
auto-generated one; ``en`` / ``zh`` = that language (manual before
auto-generated), else YouTube's own translation of another track.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from epub_zh.metadata import safe_filename
from epub_zh.transcripts import TranscriptError, fmt_ts

LANGS = {"auto", "en", "zh"}
_ID = r"[A-Za-z0-9_-]{11}"
_ID_IN_URL = re.compile(rf"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)({_ID})")
_BARE_ID = re.compile(rf"^{_ID}$")
KEY_RE = re.compile(rf"^{_ID}\.[A-Za-z0-9-]{{1,20}}$")
_PREFERRED = {
    "en": ("en", "en-US", "en-GB"),
    "zh": ("zh-Hans", "zh-CN", "zh", "zh-SG", "zh-Hant", "zh-TW", "zh-HK"),
}
_LABEL = {"en": "英文", "zh": "中文"}
_SENTENCE_END = tuple(".!?。？！…\"”'’)")
_NO_SPACE_LANGS = ("zh", "ja", "ko")
INSTALL_HINT = "缺少 youtube-transcript-api。在 epub-zh 仓库目录运行 `uv tool install --reinstall -e .` 后重启 epub-zh serve"
PROXY_HINT = "在“设置”里填写 YouTube 代理（如 http://127.0.0.1:7890）后重试"


class YouTubeError(TranscriptError):
    pass


@dataclass
class VideoTranscript:
    video_id: str
    title: str
    channel: str
    language: str  # e.g. "English (auto-generated)" as YouTube names it
    language_code: str
    is_generated: bool
    translated: bool = False
    segments: list[dict[str, Any]] = field(default_factory=list)  # [{startMs, durMs, text}]
    fetched: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoTranscript":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def key(self) -> str:
        return f"{self.video_id}.{self.language_code}"

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def download_stem(self) -> str:
        return f"{safe_filename(self.title)}.{self.language_code}"

    @property
    def duration_seconds(self) -> int:
        if not self.segments:
            return 0
        last = self.segments[-1]
        return (int(last["startMs"]) + int(last.get("durMs", 0))) // 1000

    @property
    def track_label(self) -> str:
        name = re.sub(r"\s*\(auto-generated\)$", "", self.language)
        if self.translated:
            return f"{name}（YouTube 机翻）"
        return f"{name}（自动生成）" if self.is_generated else name

    def summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "subtitle": " · ".join(x for x in (self.channel, self.track_label) if x),
            "date": "",
            "duration_seconds": self.duration_seconds,
            "segment_count": len(self.segments),
            "fetched": self.fetched,
            "url": self.url,
        }

    def paragraphs(self) -> list[tuple[int, str]]:
        """Caption lines are fragments; join them into paragraphs of a few sentences.

        Auto-generated English captions often have no punctuation, so a
        paragraph also closes once it gets long.
        """
        sep = "" if self.language_code.startswith(_NO_SPACE_LANGS) else " "
        soft, hard = (150, 400) if sep == "" else (400, 900)
        out: list[tuple[int, str]] = []
        start: int | None = None
        buf = ""
        for seg in self.segments:
            text = " ".join(str(seg["text"]).split())
            if not text:
                continue
            if start is None:
                start, buf = int(seg["startMs"]), text
            else:
                buf = f"{buf}{sep}{text}"
            if (len(buf) >= soft and buf.endswith(_SENTENCE_END)) or len(buf) >= hard:
                out.append((start, buf))
                start, buf = None, ""
        if buf:
            out.append((start or 0, buf))
        return out

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        if self.channel:
            lines.append(f"- 频道：{self.channel}")
        lines += [
            f"- 字幕：{self.track_label}　时长：{fmt_ts(self.duration_seconds * 1000)}　字幕条数：{len(self.segments)}",
            f"- 视频：{self.url}",
            "",
            "> 字幕来自 YouTube（youtube-transcript-api），已合并为段落，时间码为该段起点。",
            "",
        ]
        for ms, text in self.paragraphs():
            lines += [f"**[{fmt_ts(ms)}]** {text}", ""]
        return "\n".join(lines)

    def to_timestamped(self) -> str:
        return "".join(f"[{fmt_ts(ms)}] {text}\n" for ms, text in self.paragraphs())


def parse_video_id(text: str) -> str:
    """Video id from a watch / youtu.be / shorts / embed / live link, or a bare 11-character id."""
    text = (text or "").strip()
    m = _ID_IN_URL.search(text)
    if m:
        return m.group(1)
    if _BARE_ID.match(text):
        return text
    raise YouTubeError("没认出 YouTube 链接，应形如 https://www.youtube.com/watch?v=… 或 https://youtu.be/…")


def library_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        return None
    try:
        return version("youtube-transcript-api")
    except PackageNotFoundError:
        return None


# ---- picking a track ----------------------------------------------------------


def pick_track(tracks: list[Any], lang: str) -> tuple[Any, bool]:
    """Returns ``(track, translated)``; ``tracks`` are the library's ``Transcript`` objects."""
    if not tracks:
        raise YouTubeError("这个视频没有任何字幕")
    if lang == "auto":
        manual = [t for t in tracks if not t.is_generated]
        return (manual or tracks)[0], False
    preferred = _PREFERRED[lang]
    for generated in (False, True):
        pool = [t for t in tracks if t.is_generated == generated]
        for code in preferred:
            for t in pool:
                if t.language_code == code:
                    return t, False
        for t in pool:
            if t.language_code.split("-")[0] == lang:
                return t, False
    # YouTube offers a per-video subset of targets (sometimes zh-Hant but not zh-Hans).
    for source in sorted((t for t in tracks if t.is_translatable), key=lambda t: t.is_generated):
        offered = {tl.language_code for tl in source.translation_languages}
        target = next((code for code in preferred if code in offered), None)
        if target:
            return source.translate(target), True
    have = "、".join(sorted({t.language for t in tracks}))
    raise YouTubeError(f"这个视频没有{_LABEL[lang]}字幕，YouTube 也不提供{_LABEL[lang]}机翻；可用的字幕：{have}")


# ---- network ------------------------------------------------------------------

Lister = Callable[[str, str | None], list[Any]]  # (video_id, proxy) -> tracks
Describer = Callable[[str, str | None], tuple[str, str]]  # (video_id, proxy) -> (title, channel)


def _list_tracks(video_id: str, proxy: str | None) -> list[Any]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.proxies import GenericProxyConfig
    except ImportError as exc:
        raise YouTubeError(INSTALL_HINT) from exc
    proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None
    return list(YouTubeTranscriptApi(proxy_config=proxy_config).list(video_id))


def _describe(video_id: str, proxy: str | None) -> tuple[str, str]:
    """Title and channel from YouTube's oEmbed endpoint; falls back to the id."""
    try:
        import requests

        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            proxies={"http": proxy, "https": proxy} if proxy else None,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return str(data.get("title") or video_id), str(data.get("author_name") or "")
    except Exception:  # noqa: BLE001 — the captions are what matter; a bare id is fine
        return video_id, ""


def _explain(exc: Exception) -> str:
    """Short Chinese reason for a library / network failure."""
    name = type(exc).__name__
    for cls in type(exc).__mro__:
        if cls.__name__ in _REASONS:
            return _REASONS[cls.__name__]
    if name in {"ConnectionError", "ProxyError", "ConnectTimeout", "ReadTimeout", "SSLError", "Timeout"}:
        return f"连不上 YouTube（{name}）。{PROXY_HINT}"
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else name
    return f"抓取失败：{first[:200]}"


_REASONS = {
    "TranscriptsDisabled": "这个视频关闭了字幕",
    "NoTranscriptFound": "这个视频没有所选语言的字幕",
    "InvalidVideoId": "视频 id 无效",
    "VideoUnavailable": "视频不存在或已下架",
    "AgeRestricted": "视频有年龄限制，无法匿名抓取字幕",
    "VideoUnplayable": "视频无法播放（可能是会员、私享或地区限制）",
    "IpBlocked": f"YouTube 限制了当前 IP。{PROXY_HINT}",
    "RequestBlocked": f"YouTube 拒绝了请求。{PROXY_HINT}",
    "PoTokenRequired": "YouTube 要求浏览器验证（PO token），这个视频暂时抓不了",
    "NotTranslatable": "这条字幕不能机翻",
    "TranslationLanguageNotAvailable": "YouTube 不支持翻译成这种语言",
}


def fetch_video(
    video_id: str,
    lang: str = "auto",
    *,
    proxy: str | None = None,
    lister: Lister = _list_tracks,
    describer: Describer = _describe,
) -> VideoTranscript:
    if lang not in LANGS:
        raise YouTubeError("字幕语言只能是 auto / en / zh")
    translated = False
    try:
        track, translated = pick_track(lister(video_id, proxy), lang)
        snippets = track.fetch()
    except YouTubeError:
        raise
    except Exception as exc:  # noqa: BLE001 — library and network errors become one message
        if translated and type(exc).__name__ in {"IpBlocked", "RequestBlocked", "PoTokenRequired"}:
            raise YouTubeError(
                f"这个视频没有{_LABEL[lang]}字幕，YouTube 又拒绝了机翻字幕请求（很常见）。请改选“原始字幕”"
            ) from exc
        raise YouTubeError(_explain(exc)) from exc
    segments = [
        {"startMs": int(round(s.start * 1000)), "durMs": int(round(s.duration * 1000)), "text": s.text}
        for s in snippets
        if str(s.text).strip()
    ]
    if not segments:
        raise YouTubeError("字幕是空的")
    title, channel = describer(video_id, proxy)
    return VideoTranscript(
        video_id=video_id,
        title=title,
        channel=channel,
        language=track.language,
        language_code=track.language_code,
        is_generated=bool(track.is_generated),
        translated=translated,
        segments=segments,
    )
