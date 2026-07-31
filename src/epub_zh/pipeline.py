from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from epub_zh.config import ResolvedSettings
from epub_zh.epub_io import (
    JobState,
    list_content_docs,
    pack_epub,
    state_dir_for,
    unpack_epub,
)
from epub_zh.translate import (
    Translator,
    apply_translations,
    collect_blocks,
    parse_xhtml,
    serialize_xhtml,
)

console = Console(stderr=True)


def _rel_key(workdir: Path, doc: Path) -> str:
    return doc.resolve().relative_to(workdir.resolve()).as_posix()


def _default_workdir(source: Path, output: Path) -> Path:
    digest = hashlib.sha1(f"{source.resolve()}|{output.resolve()}".encode()).hexdigest()[:12]
    return state_dir_for(output) / f"work-{digest}"


def run_translate(
    *,
    source: Path,
    output: Path,
    mode: str,
    settings: ResolvedSettings,
) -> Path:
    """Fresh translation job. ``settings.api_key`` must be set."""
    source = source.resolve()
    output = output.resolve()
    api_key = settings.api_key
    if not api_key:
        raise ValueError("api_key is required for translation")

    workdir = _default_workdir(source, output)
    unpack_epub(source, workdir)
    state_path = state_dir_for(output) / "state.json"
    state = JobState(
        source=str(source),
        output=str(output),
        mode=mode,
        model=settings.model,
        base_url=settings.base_url or "",
        workdir=str(workdir),
        batch_size=settings.batch_size,
    )
    state.save(state_path)
    return _run_job(state=state, state_path=state_path, api_key=api_key)


def run_resume(*, state_path: Path, api_key: str) -> Path:
    """Resume from ``state.json``. Job params come from state; only the key is passed in."""
    if not api_key:
        raise ValueError("api_key is required for resume")
    state_path = state_path.resolve()
    state = JobState.load(state_path)
    workdir = Path(state.workdir)
    if not workdir.exists():
        raise FileNotFoundError(f"Workdir missing for resume: {workdir}")
    return _run_job(state=state, state_path=state_path, api_key=api_key)


def run_dry_run(*, source: Path, mode: str) -> None:
    """Parse-only: unpack to a temp dir, report block counts, leave no state."""
    source = source.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="epub-zh-dry-"))
    try:
        unpack_epub(source, tmp)
        docs = list_content_docs(tmp)
        console.print(
            f"[bold]Source[/]: {source}\n"
            f"[bold]Mode[/]: {mode}\n"
            f"[bold]Docs[/]: {len(docs)} total (dry-run, read-only)"
        )
        for doc in docs:
            tree = parse_xhtml(str(doc))
            units = collect_blocks(tree.getroot())
            console.print(f"  {_rel_key(tmp, doc)}: {len(units)} blocks")
        console.print(
            "[green]Dry run complete (no API calls, no state, no output written).[/]"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_job(*, state: JobState, state_path: Path, api_key: str) -> Path:
    source = Path(state.source)
    output = Path(state.output)
    workdir = Path(state.workdir)
    mode = state.mode
    batch_size = state.batch_size

    docs = list_content_docs(workdir)
    pending = [d for d in docs if _rel_key(workdir, d) not in set(state.completed_docs)]

    console.print(
        f"[bold]Source[/]: {source}\n"
        f"[bold]Output[/]: {output}\n"
        f"[bold]Mode[/]: {mode}\n"
        f"[bold]Docs[/]: {len(docs)} total, {len(pending)} pending"
    )

    translator = Translator(
        api_key=api_key,
        base_url=state.base_url or None,
        model=state.model,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Translating", total=len(pending))
        for doc in pending:
            key = _rel_key(workdir, doc)
            progress.update(task, description=key)
            try:
                _translate_doc(
                    doc=doc,
                    translator=translator,
                    mode=mode,
                    batch_size=batch_size,
                )
            except Exception as exc:  # noqa: BLE001 — persist failure for resume
                state.failed_doc = key
                state.error = str(exc)
                state.save(state_path)
                console.print(f"[red]Failed on {key}: {exc}[/]")
                raise SystemExit(1) from exc

            state.completed_docs.append(key)
            state.failed_doc = None
            state.error = None
            state.save(state_path)
            progress.advance(task)

    pack_epub(workdir, output)
    console.print(f"[green]Wrote[/] {output}")
    return state_path


def _translate_doc(
    *,
    doc: Path,
    translator: Translator,
    mode: str,
    batch_size: int,
) -> None:
    tree = parse_xhtml(str(doc))
    units = collect_blocks(tree.getroot())
    if not units:
        return

    translations: list[str] = []
    for i in range(0, len(units), batch_size):
        batch = units[i : i + batch_size]
        translations.extend(translator.translate_batch([u.source_text for u in batch]))

    apply_translations(units, translations, mode)
    doc.write_bytes(serialize_xhtml(tree))
