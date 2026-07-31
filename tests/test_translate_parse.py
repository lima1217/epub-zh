from __future__ import annotations

import pytest

from epub_zh.translate import ParseError, _parse_numbered


def test_parse_numbered_contiguous() -> None:
    text = "1. 你好\n2. 世界"
    assert _parse_numbered(text, 2) == ["你好", "世界"]


def test_parse_numbered_gap_falls_back_or_errors() -> None:
    # 8 numbered lines but missing index 3 → must not KeyError
    text = "\n".join(f"{i}. t{i}" for i in (1, 2, 4, 5, 6, 7, 8, 9))
    with pytest.raises(ParseError, match="Could not parse"):
        _parse_numbered(text, 8)


def test_parse_numbered_line_fallback() -> None:
    text = "alpha\nbeta\ngamma"
    assert _parse_numbered(text, 3) == ["alpha", "beta", "gamma"]
