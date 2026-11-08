"""Docs restate the built-in defaults; keep them equal to config.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from epub_zh.config import CONFIG_TEMPLATE, DEFAULT_BASE_URL, DEFAULT_MODEL

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / ".agents/skills/epub-zh-translate/cli-reference.md",
]
MODEL_ID = re.compile(r"\bdeepseek-[a-z0-9][a-z0-9.-]*")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_doc_model_ids_match_default(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert DEFAULT_MODEL in text
    assert DEFAULT_BASE_URL in text
    assert set(MODEL_ID.findall(text)) == {DEFAULT_MODEL}


def test_config_template_matches_default() -> None:
    assert set(MODEL_ID.findall(CONFIG_TEMPLATE)) == {DEFAULT_MODEL}
    assert DEFAULT_BASE_URL in CONFIG_TEMPLATE
