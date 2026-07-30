from __future__ import annotations

from pathlib import Path

import pytest

from epub_zh.config import (
    resolve_settings,
    user_config_path,
    write_config_template,
)


def test_user_config_path_respects_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert user_config_path() == tmp_path / "xdg" / "epub-zh" / "config.toml"


def test_resolve_precedence_cli_over_env_over_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    user = user_config_path()
    user.parent.mkdir(parents=True)
    user.write_text(
        'api_key = "sk-file-key-xxxxx"\nmodel = "file-model"\nbase_url = "https://file.example/v1"\n',
        encoding="utf-8",
    )
    local = tmp_path / "proj"
    local.mkdir()
    (local / ".epub-zh.toml").write_text('model = "local-model"\n', encoding="utf-8")

    # file only
    s = resolve_settings(cwd=local, environ={})
    assert s.api_key == "sk-file-key-xxxxx"
    assert s.model == "local-model"  # local overrides user
    assert s.base_url == "https://file.example/v1"
    assert s.sources["model"].endswith(".epub-zh.toml")

    # env beats file
    s = resolve_settings(
        cwd=local,
        environ={"OPENAI_MODEL": "env-model", "OPENAI_API_KEY": "sk-env"},
    )
    assert s.model == "env-model"
    assert s.api_key == "sk-env"
    assert s.sources["model"] == "env:OPENAI_MODEL"

    # cli beats env
    s = resolve_settings(
        cwd=local,
        cli_model="cli-model",
        cli_api_key="sk-cli",
        environ={"OPENAI_MODEL": "env-model", "OPENAI_API_KEY": "sk-env"},
    )
    assert s.model == "cli-model"
    assert s.api_key == "sk-cli"
    assert s.sources["api_key"] == "cli"


def test_write_config_template_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config_template(path)
    assert path.exists()
    with pytest.raises(FileExistsError):
        write_config_template(path)
    write_config_template(path, force=True)
