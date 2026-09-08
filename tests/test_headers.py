from __future__ import annotations

from epub_zh import __version__
from epub_zh.translate import _build_default_headers


def test_default_headers_always_self_identify() -> None:
    headers = _build_default_headers(base_url=None, session_id=None)
    assert headers["User-Agent"].startswith(f"epub-zh/{__version__}")


def test_opencode_endpoint_gets_session_header() -> None:
    headers = _build_default_headers(
        base_url="https://opencode.ai/zen/go/v1", session_id="abc123"
    )
    assert headers["x-opencode-session"] == "abc123"


def test_opencode_without_session_sends_no_session_header() -> None:
    headers = _build_default_headers(base_url="https://opencode.ai/zen/go/v1", session_id=None)
    assert "x-opencode-session" not in headers


def test_other_gateways_never_get_session_header() -> None:
    headers = _build_default_headers(
        base_url="https://api.deepseek.com/v1", session_id="abc123"
    )
    assert "x-opencode-session" not in headers
    assert headers["User-Agent"].startswith(f"epub-zh/{__version__}")
