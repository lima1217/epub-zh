from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from epub_zh.config import CONFIG_TEMPLATE, set_config_value


def test_set_uncomments_nothing_and_appends_when_only_comments(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    set_config_value(path, "model", "deepseek-other")

    text = path.read_text(encoding="utf-8")
    assert tomllib.loads(text)["model"] == "deepseek-other"
    # The header's commented example and the template's commented line stay intact.
    assert "#   model = " in text
    assert '# model = "' in text


def test_set_replaces_active_line(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('api_key = "sk-x"\nmodel = "old"\n', encoding="utf-8")

    set_config_value(path, "model", "new")
    set_config_value(path, "batch_size", "12")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data == {"api_key": "sk-x", "model": "new", "batch_size": 12}


def test_set_creates_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"
    set_config_value(path, "base_url", "https://example.test/v1")
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "base_url": "https://example.test/v1"
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [("nope", "x"), ("batch_size", "abc"), ("batch_size", "99")],
)
def test_set_rejects_bad_input_without_writing(tmp_path: Path, name: str, value: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "keep"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        set_config_value(path, name, value)
    assert path.read_text(encoding="utf-8") == 'model = "keep"\n'
