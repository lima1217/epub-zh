from __future__ import annotations

import os

import pytest

from epub_zh.translate import _sanitize_no_proxy, _sanitize_proxy_env


def test_sanitize_no_proxy_drops_bracketed_ipv6() -> None:
    # "[::1]" is redundant with the unbracketed "::1" already present.
    raw = "127.0.0.1,localhost,::1,[::1]"
    assert _sanitize_no_proxy(raw) == "127.0.0.1,localhost,::1"


def test_sanitize_no_proxy_keeps_plain_entries() -> None:
    raw = "127.0.0.1,localhost,::1,example.com"
    assert _sanitize_no_proxy(raw) == raw


def test_sanitize_no_proxy_handles_whitespace_and_empty() -> None:
    assert _sanitize_no_proxy(" , a.com ,, [fe80::1] ,") == "a.com"


def test_sanitize_proxy_env_cleans_uppercase_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1,[::1]")
    _sanitize_proxy_env()
    assert os.environ["NO_PROXY"] == "127.0.0.1,localhost,::1"


def test_sanitize_proxy_env_removes_when_all_bracketed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("no_proxy", "[::1]")
    _sanitize_proxy_env()
    assert "no_proxy" not in os.environ


def test_sanitize_proxy_env_leaves_clean_env_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _sanitize_proxy_env()
    assert os.environ["NO_PROXY"] == "127.0.0.1,localhost"
    assert os.environ["no_proxy"] == "127.0.0.1,localhost"
