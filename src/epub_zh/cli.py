from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from epub_zh import __version__
from epub_zh.config import (
    DEFAULT_BATCH_SIZE,
    format_settings_rows,
    local_config_path,
    resolve_settings,
    user_config_path,
    write_config_template,
)
from epub_zh.pipeline import run_dry_run, run_resume, run_translate

app = typer.Typer(
    name="epub-zh",
    help="Translate English EPUB books to Simplified Chinese via LLM APIs.",
    add_completion=False,
    no_args_is_help=True,
)

config_app = typer.Typer(
    name="config",
    help="Manage persistent LLM settings (~/.config/epub-zh/config.toml).",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


def _require_api_key(api_key: Optional[str]) -> str:
    if not api_key:
        raise typer.BadParameter(
            "Set api_key in ~/.config/epub-zh/config.toml, "
            "or export OPENAI_API_KEY, or pass --api-key "
            "(run: epub-zh config init)",
            param_hint="--api-key",
        )
    return api_key


@app.command("translate")
def translate_cmd(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Input EPUB"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output EPUB path (required unless --dry-run)",
    ),
    mode: str = typer.Option(
        "zh",
        "--mode",
        "-m",
        help="zh = Chinese only; bilingual = English then Chinese block",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model id (overrides config / OPENAI_MODEL)",
    ),
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="API base URL (overrides config / OPENAI_BASE_URL)",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key (overrides config / OPENAI_API_KEY)",
    ),
    batch_size: Optional[int] = typer.Option(
        None,
        "--batch-size",
        min=1,
        max=40,
        help=f"Blocks per API call (default: config or {DEFAULT_BATCH_SIZE})",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse EPUB and count blocks only; no API, state, or output",
    ),
) -> None:
    """Import an EPUB, translate content, export a new EPUB."""
    if mode not in {"zh", "bilingual"}:
        raise typer.BadParameter("mode must be 'zh' or 'bilingual'", param_hint="--mode")
    if not dry_run and output is None:
        raise typer.BadParameter("--output is required unless --dry-run", param_hint="--output")

    if dry_run:
        run_dry_run(source=source, mode=mode)
        return

    assert output is not None  # validated above
    settings = resolve_settings(
        cli_api_key=api_key,
        cli_base_url=base_url,
        cli_model=model,
        cli_batch_size=batch_size,
    )
    _require_api_key(settings.api_key)
    run_translate(source=source, output=output, mode=mode, settings=settings)


@app.command("resume")
def resume_cmd(
    state: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to state.json from a previous run",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key (overrides config / OPENAI_API_KEY)",
    ),
) -> None:
    """Resume a failed or interrupted translation from state.json."""
    settings = resolve_settings(cli_api_key=api_key)
    key = _require_api_key(settings.api_key)
    run_resume(state_path=state, api_key=key)


@config_app.command("path")
def config_path_cmd(
    local: bool = typer.Option(False, "--local", help="Show project .epub-zh.toml path"),
) -> None:
    """Print the config file path."""
    typer.echo(local_config_path() if local else user_config_path())


@config_app.command("init")
def config_init_cmd(
    local: bool = typer.Option(
        False,
        "--local",
        help="Create .epub-zh.toml in the current directory instead of the user config",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file"),
) -> None:
    """Create a config template (chmod 600 when possible)."""
    path = local_config_path() if local else user_config_path()
    try:
        write_config_template(path, force=force)
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Wrote {path}")
    typer.echo("Edit api_key (and optional base_url / model), then run translate.")


@config_app.command("show")
def config_show_cmd() -> None:
    """Show resolved settings and where each value came from (api_key redacted)."""
    settings = resolve_settings()
    for name, value, src in format_settings_rows(settings):
        typer.echo(f"{name:12} {value}  [{src}]")
    typer.echo(f"user_path    {user_config_path()}")
    typer.echo(f"local_path   {local_config_path()}")


@app.command("version")
def version_cmd() -> None:
    """Print version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
