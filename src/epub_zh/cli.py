from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Optional

import typer

from epub_zh import __version__
from epub_zh.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONCURRENCY,
    format_settings_rows,
    local_config_path,
    resolve_settings,
    set_config_value,
    user_config_path,
    write_config_template,
)
from epub_zh.epub_io import JobState
from epub_zh.pipeline import TranslationFailed, run_dry_run, run_resume, run_translate
from epub_zh.translate import list_models

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
        help=f"Max blocks per API call (default: config or {DEFAULT_BATCH_SIZE})",
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        "-j",
        min=1,
        max=32,
        help=f"API calls in flight at once (default: config or {DEFAULT_CONCURRENCY})",
    ),
    skip_refs: bool = typer.Option(
        False,
        "--skip-refs",
        help="Leave bibliography and index untranslated (kept in English)",
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

    settings = resolve_settings(
        cli_api_key=api_key,
        cli_base_url=base_url,
        cli_model=model,
        cli_batch_size=batch_size,
        cli_concurrency=concurrency,
    )
    if dry_run:
        run_dry_run(
            source=source,
            mode=mode,
            skip_refs=skip_refs,
            batch_size=settings.batch_size,
            batch_chars=settings.batch_chars,
        )
        return

    assert output is not None  # validated above
    _require_api_key(settings.api_key)
    try:
        run_translate(
            source=source, output=output, mode=mode, settings=settings, skip_refs=skip_refs
        )
    except TranslationFailed as exc:
        raise typer.Exit(1) from exc


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
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        "-j",
        min=1,
        max=32,
        help=f"API calls in flight at once (default: config or {DEFAULT_CONCURRENCY})",
    ),
) -> None:
    """Resume a failed or interrupted translation from state.json."""
    settings = resolve_settings(cli_api_key=api_key, cli_concurrency=concurrency)
    key = _require_api_key(settings.api_key)
    try:
        run_resume(state_path=state, api_key=key, concurrency=settings.concurrency)
    except TranslationFailed as exc:
        raise typer.Exit(1) from exc


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


@config_app.command("set")
def config_set_cmd(
    name: str = typer.Argument(..., help="Setting: api_key, base_url, model, batch_size, batch_chars, concurrency"),
    value: str = typer.Argument(..., help="New value"),
    local: bool = typer.Option(False, "--local", help="Write ./.epub-zh.toml instead of the user config"),
) -> None:
    """Set one value in the config file (comments and other lines kept)."""
    path = local_config_path() if local else user_config_path()
    try:
        stored = set_config_value(path, name, value)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    shown = "(redacted)" if name == "api_key" else stored
    typer.echo(f"{name} = {shown}  -> {path}")


@config_app.command("check")
def config_check_cmd() -> None:
    """Verify the API key works and the configured model is served (GET /models)."""
    settings = resolve_settings()
    key = _require_api_key(settings.api_key)
    try:
        models = list_models(api_key=key, base_url=settings.base_url)
    except Exception as exc:  # noqa: BLE001 — report any gateway/auth failure
        typer.echo(f"FAIL  could not list models at {settings.base_url}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if settings.model in models:
        typer.echo(f"OK    model {settings.model} is served by {settings.base_url}")
        return
    typer.echo(
        f"FAIL  model {settings.model} [{settings.sources.get('model')}] is not served by "
        f"{settings.base_url}; available: {', '.join(models) or '(none)'}",
        err=True,
    )
    raise typer.Exit(1)


@app.command("status")
def status_cmd(
    state: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="Path to state.json"
    ),
) -> None:
    """One-line job progress: running / complete / failed (exit 1 when failed)."""
    job = JobState.load(state)
    typer.echo(job.status_line())
    if job.failed_doc:
        raise typer.Exit(1)


@app.command("serve")
def serve_cmd(
    port: int = typer.Option(8765, "--port", "-p", min=1, max=65535, help="Port on 127.0.0.1 (next free port if taken)"),
    jobs_dir: Optional[Path] = typer.Option(
        None,
        "--jobs-dir",
        file_okay=False,
        help="Where uploads and translated books are kept (default: ~/.local/share/epub-zh/jobs)",
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page in your browser"),
) -> None:
    """Local web page: drag in an EPUB, translate it, save the result; or fetch a Xiaoyuzhou podcast / YouTube transcript."""
    import webbrowser

    from epub_zh.server import bind_server, default_jobs_dir

    directory = (jobs_dir or default_jobs_dir()).expanduser().resolve()
    try:
        server = bind_server(port=port, jobs_dir=directory)
    except OSError as exc:
        typer.echo(f"Could not start server: {exc}", err=True)
        raise typer.Exit(1) from exc
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    typer.echo(f"epub-zh web UI: {url}")
    typer.echo(f"Jobs: {directory}")
    typer.echo("Keep this terminal open while books translate. Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nStopped. Unfinished jobs can be resumed next time from the page.")
    finally:
        server.server_close()


@app.command("version")
def version_cmd() -> None:
    """Print version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
