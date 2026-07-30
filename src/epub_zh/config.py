from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BATCH_SIZE = 8

CONFIG_FILENAME = "config.toml"
LOCAL_CONFIG_NAME = ".epub-zh.toml"


@dataclass(frozen=True)
class FileConfig:
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    batch_size: int | None = None


@dataclass(frozen=True)
class ResolvedSettings:
    api_key: str | None
    base_url: str | None
    model: str
    batch_size: int
    sources: dict[str, str]  # field -> where it came from


def user_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "epub-zh" / CONFIG_FILENAME
    return Path.home() / ".config" / "epub-zh" / CONFIG_FILENAME


def local_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / LOCAL_CONFIG_NAME


def _parse_toml(path: Path) -> FileConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a table: {path}")

    api_key = data.get("api_key")
    base_url = data.get("base_url")
    model = data.get("model")
    batch_size = data.get("batch_size")

    if api_key is not None and not isinstance(api_key, str):
        raise ValueError(f"api_key must be a string in {path}")
    if base_url is not None and not isinstance(base_url, str):
        raise ValueError(f"base_url must be a string in {path}")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"model must be a string in {path}")
    if batch_size is not None and not isinstance(batch_size, int):
        raise ValueError(f"batch_size must be an integer in {path}")
    if isinstance(batch_size, int) and not (1 <= batch_size <= 40):
        raise ValueError(f"batch_size must be 1..40 in {path}")

    # Accept empty strings as unset
    return FileConfig(
        api_key=api_key or None,
        base_url=base_url or None,
        model=model or None,
        batch_size=batch_size,
    )


def load_file_config(path: Path) -> FileConfig | None:
    if not path.is_file():
        return None
    return _parse_toml(path)


def merge_file_configs(*configs: FileConfig | None) -> FileConfig:
    """Later non-None fields override earlier ones."""
    api_key = base_url = model = None
    batch_size = None
    for cfg in configs:
        if cfg is None:
            continue
        if cfg.api_key is not None:
            api_key = cfg.api_key
        if cfg.base_url is not None:
            base_url = cfg.base_url
        if cfg.model is not None:
            model = cfg.model
        if cfg.batch_size is not None:
            batch_size = cfg.batch_size
    return FileConfig(api_key=api_key, base_url=base_url, model=model, batch_size=batch_size)


def load_layered_file_config(cwd: Path | None = None) -> tuple[FileConfig, list[Path]]:
    """User config then local project config (local wins)."""
    paths: list[Path] = []
    user = user_config_path()
    local = local_config_path(cwd)
    configs: list[FileConfig | None] = []
    for path in (user, local):
        cfg = load_file_config(path)
        if cfg is not None:
            paths.append(path)
            configs.append(cfg)
    return merge_file_configs(*configs), paths


def resolve_settings(
    *,
    cli_api_key: str | None = None,
    cli_base_url: str | None = None,
    cli_model: str | None = None,
    cli_batch_size: int | None = None,
    cwd: Path | None = None,
    environ: dict[str, str] | None = None,
) -> ResolvedSettings:
    """
    Precedence (high → low):
      CLI flag > environment > .epub-zh.toml > ~/.config/epub-zh/config.toml > defaults
    """
    env = environ if environ is not None else os.environ
    file_cfg, _paths = load_layered_file_config(cwd)
    # Which file supplied each field (for show); local overrides user.
    field_file: dict[str, str] = {}
    user = load_file_config(user_config_path())
    local = load_file_config(local_config_path(cwd))
    for label, cfg in (
        (str(user_config_path()), user),
        (str(local_config_path(cwd)), local),
    ):
        if cfg is None:
            continue
        if cfg.api_key is not None:
            field_file["api_key"] = label
        if cfg.base_url is not None:
            field_file["base_url"] = label
        if cfg.model is not None:
            field_file["model"] = label
        if cfg.batch_size is not None:
            field_file["batch_size"] = label

    sources: dict[str, str] = {}

    def pick(
        name: str,
        cli: str | int | None,
        env_key: str | None,
        file_val: str | int | None,
        default: str | int | None = None,
    ) -> str | int | None:
        if cli is not None:
            sources[name] = "cli"
            return cli
        if env_key and env.get(env_key):
            sources[name] = f"env:{env_key}"
            return env[env_key]
        if file_val is not None:
            sources[name] = field_file.get(name, "config")
            return file_val
        if default is not None:
            sources[name] = "default"
            return default
        sources[name] = "unset"
        return None

    # batch_size CLI: typer always passes a value; callers should pass None to mean "not set"
    api_key = pick("api_key", cli_api_key, "OPENAI_API_KEY", file_cfg.api_key)
    base_url = pick("base_url", cli_base_url, "OPENAI_BASE_URL", file_cfg.base_url)
    model = pick("model", cli_model, "OPENAI_MODEL", file_cfg.model, DEFAULT_MODEL)
    batch_size = pick(
        "batch_size",
        cli_batch_size,
        None,
        file_cfg.batch_size,
        DEFAULT_BATCH_SIZE,
    )

    assert isinstance(model, str)
    assert isinstance(batch_size, int)
    return ResolvedSettings(
        api_key=api_key if isinstance(api_key, str) else None,
        base_url=base_url if isinstance(base_url, str) else None,
        model=model,
        batch_size=batch_size,
        sources=sources,
    )


CONFIG_TEMPLATE = """\
# epub-zh LLM settings
# Precedence: CLI > environment > .epub-zh.toml > this file > defaults

api_key = "sk-..."
# base_url = "https://api.openai.com/v1"
# model = "gpt-4o-mini"
# batch_size = 8
"""


def write_config_template(path: Path, *, force: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}")
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
