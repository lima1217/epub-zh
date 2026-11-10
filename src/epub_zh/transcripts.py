"""Shared pieces for fetched transcripts (Xiaoyuzhou podcasts, YouTube videos).

A transcript type provides ``key`` (store file stem), ``fetched``,
``to_dict`` / ``from_dict``, ``summary()``, ``to_markdown()``,
``to_timestamped()`` and ``download_stem``. ``TranscriptStore`` keeps one
``<key>.json`` per transcript; fetching the same thing again overwrites it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol


class TranscriptError(Exception):
    """A user-facing reason a transcript could not be fetched."""


class StoredTranscript(Protocol):
    fetched: float

    @property
    def key(self) -> str: ...

    def to_dict(self) -> dict[str, Any]: ...

    def summary(self) -> dict[str, Any]: ...

    def to_markdown(self) -> str: ...

    def to_timestamped(self) -> str: ...

    @property
    def download_stem(self) -> str: ...


def fmt_ts(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


class TranscriptStore:
    """``fetch(params)`` → transcript; ``claim(params)`` names the request so
    two clicks on the same link don't fetch it twice at once."""

    def __init__(
        self,
        directory: Path,
        *,
        key_re: re.Pattern[str],
        load: Callable[[dict[str, Any]], StoredTranscript],
        fetch: Callable[[dict[str, Any]], StoredTranscript],
        claim: Callable[[dict[str, Any]], str],
    ) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key_re = key_re
        self._load = load
        self._fetch = fetch
        self._claim = claim
        self.lock = threading.Lock()
        self.inflight: set[str] = set()

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> StoredTranscript | None:
        if not self.key_re.match(key):
            return None
        try:
            return self._load(json.loads(self._path(key).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def list(self) -> list[dict[str, Any]]:
        found = [t for t in (self.get(p.stem) for p in self.dir.glob("*.json")) if t is not None]
        return [t.summary() for t in sorted(found, key=lambda t: t.fetched, reverse=True)]

    def fetch(self, params: dict[str, Any]) -> StoredTranscript:
        claim = self._claim(params)  # raises TranscriptError on a bad link
        with self.lock:
            if claim in self.inflight:
                raise TranscriptError("这个链接正在抓取中")
            self.inflight.add(claim)
        try:
            t = self._fetch(params)
            t.fetched = time.time()
            self._path(t.key).write_text(json.dumps(t.to_dict(), ensure_ascii=False), encoding="utf-8")
            return t
        finally:
            with self.lock:
                self.inflight.discard(claim)

    def delete(self, key: str) -> bool:
        if not self.key_re.match(key) or not self._path(key).is_file():
            return False
        self._path(key).unlink()
        return True
