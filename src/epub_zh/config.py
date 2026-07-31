from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BATCH_SIZE = 8

CONFIG_FILENAME = "config.toml"
LOCAL_CONFIG_NAME = ".epub-zh.toml"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: Literal["str", "int"] = "str"
    env_key: str | None = None
    default: str | int | None = None
    int_range: tuple[int, int] | None = None


# Single source of truth for config fields (parse / merge / resolve / show).
SETTING_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("api_key", env_key="OPENAI_API_KEY"),
    FieldSpec("base_url", env_key="OPENAI_BASE_URL"),
    FieldSpec("model", env_key="OPENAI_MODEL", default=DEFAULT_MODEL),
    FieldSpec(
        "batch_size",
        kind="int",
        default=DEFAULT_BATCH_SIZE,
        int_range=(1, 40),
    ),
)


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


def _coerce_field(spec: FieldSpec, raw: Any, *, path: Path) -> str | int | None:
    if raw is None:
        return None
    if spec.kind == "str":
        if not isinstance(raw, str):
            raise ValueError(f"{spec.name} must be a string in {path}")
        return raw or None
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"{spec.name} must be an integer in {path}")
    if spec.int_range is not None:
        lo, hi = spec.int_range
        if not (lo <= raw <= hi):
            raise ValueError(f"{spec.name} must be {lo}..{hi} in {path}")
    return raw


def _parse_toml(path: Path) -> FileConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a table: {path}")

    values: dict[str, str | int | None] = {}
    for spec in SETTING_FIELDS:
        values[spec.name] = _coerce_field(spec, data.get(spec.name), path=path)
    return FileConfig(**values)  # type: ignore[arg-type]


def load_file_config(path: Path) -> FileConfig | None:
    if not path.is_file():
        return None
    return _parse_toml(path)


def merge_file_configs(*configs: FileConfig | None) -> FileConfig:
    """Later non-None fields override earlier ones."""
    values: dict[str, str | int | None] = {f.name: None for f in SETTING_FIELDS}
    for cfg in configs:
        if cfg is None:
            continue
        for spec in SETTING_FIELDS:
            val = getattr(cfg, spec.name)
            if val is not None:
                values[spec.name] = val
    return FileConfig(**values)  # type: ignore[arg-type]


def load_layered_file_config(
    cwd: Path | None = None,
) -> tuple[FileConfig, dict[str, str]]:
    """User config then local project config (local wins).

    Returns merged FileConfig and a map of field → config path that supplied it.
    """
    field_file: dict[str, str] = {}
    configs: list[FileConfig] = []
    for path in (user_config_path(), local_config_path(cwd)):
        cfg = load_file_config(path)
        if cfg is None:
            continue
        configs.append(cfg)
        for spec in SETTING_FIELDS:
            if getattr(cfg, spec.name) is not None:
                field_file[spec.name] = str(path)
    return merge_file_configs(*configs), field_file


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
    file_cfg, field_file = load_layered_file_config(cwd)

    cli_overrides: dict[str, str | int | None] = {
        "api_key": cli_api_key,
        "base_url": cli_base_url,
        "model": cli_model,
        "batch_size": cli_batch_size,
    }

    sources: dict[str, str] = {}
    resolved: dict[str, str | int | None] = {}

    for spec in SETTING_FIELDS:
        cli = cli_overrides.get(spec.name)
        if cli is not None:
            sources[spec.name] = "cli"
            resolved[spec.name] = cli
            continue
        if spec.env_key and env.get(spec.env_key):
            sources[spec.name] = f"env:{spec.env_key}"
            raw = env[spec.env_key]
            if spec.kind == "int":
                resolved[spec.name] = int(raw)
            else:
                resolved[spec.name] = raw
            continue
        file_val = getattr(file_cfg, spec.name)
        if file_val is not None:
            sources[spec.name] = field_file.get(spec.name, "config")
            resolved[spec.name] = file_val
            continue
        if spec.default is not None:
            sources[spec.name] = "default"
            resolved[spec.name] = spec.default
            continue
        sources[spec.name] = "unset"
        resolved[spec.name] = None

    model = resolved["model"]
    batch_size = resolved["batch_size"]
    assert isinstance(model, str)
    assert isinstance(batch_size, int)
    api_key = resolved["api_key"]
    base_url = resolved["base_url"]
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


def format_settings_rows(settings: ResolvedSettings) -> list[tuple[str, str, str]]:
    """Display rows for ``config show``: (name, value, source)."""
    rows: list[tuple[str, str, str]] = []
    for spec in SETTING_FIELDS:
        src = settings.sources.get(spec.name, "?")
        if spec.name == "api_key":
            key = settings.api_key
            if not key:
                display = "(unset)"
            elif len(key) > 10:
                display = f"{key[:4]}…{key[-4:]}"
            else:
                display = "(set)"
        elif spec.name == "base_url":
            display = settings.base_url or "(OpenAI default)"
        else:
            display = str(getattr(settings, spec.name))
        rows.append((spec.name, display, src))
    return rows


# Keep dataclass field names aligned with SETTING_FIELDS (dev assert).
assert {f.name for f in fields(FileConfig)} == {f.name for f in SETTING_FIELDS}
