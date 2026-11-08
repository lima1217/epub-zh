from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from epub_zh.backmatter import reference_docs
from epub_zh.config import (
    DEFAULT_BATCH_CHARS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONCURRENCY,
    ResolvedSettings,
)
from epub_zh.epub_io import (
    JobState,
    list_content_docs,
    pack_epub,
    state_dir_for,
    unpack_epub,
)
from epub_zh.metadata import apply_output_metadata, output_title, read_title
from epub_zh.translate import (
    BlockUnit,
    Translator,
    apply_translations,
    collect_blocks,
    make_batches,
    needs_translation,
    parse_xhtml,
    serialize_xhtml,
)

console = Console(stderr=True)


@dataclass(frozen=True)
class JobProgress:
    """Snapshot passed to ``on_progress`` after each finished API batch."""

    docs_done: int
    docs_total: int
    blocks_done: int  # unique blocks translated in this run
    blocks_total: int  # unique blocks to translate in this run
    fraction: float  # whole-job progress, 0..1 (counts docs finished in earlier runs)


ProgressCallback = Callable[[JobProgress], None]


class TranslationFailed(RuntimeError):
    """A doc failed; ``state.json`` records it so the job can be resumed."""

    def __init__(self, doc: str, cause: BaseException) -> None:
        super().__init__(f"Failed on {doc}: {cause}")
        self.doc = doc


@dataclass
class _DocWork:
    key: str
    path: Path
    tree: etree._ElementTree
    units: list[BlockUnit]
    text_ids: list[int] = field(default_factory=list)  # into the job-wide unique text list
    waiting: set[int] = field(default_factory=set)  # batch ids not yet returned


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
    skip_refs: bool = False,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Fresh translation job. ``settings.api_key`` must be set.

    Raises ``TranslationFailed`` when a doc fails (state is saved for resume).
    """
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
        batch_chars=settings.batch_chars,
        skip_refs=skip_refs,
    )
    state.save(state_path)
    return _run_job(
        state=state,
        state_path=state_path,
        api_key=api_key,
        concurrency=settings.concurrency,
        on_progress=on_progress,
    )


def run_resume(
    *,
    state_path: Path,
    api_key: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Resume from ``state.json``. Job params come from state; the key and
    concurrency (which don't change the output) are passed in."""
    if not api_key:
        raise ValueError("api_key is required for resume")
    state_path = state_path.resolve()
    state = JobState.load(state_path)
    workdir = Path(state.workdir)
    if not workdir.exists():
        raise FileNotFoundError(f"Workdir missing for resume: {workdir}")
    return _run_job(
        state=state,
        state_path=state_path,
        api_key=api_key,
        concurrency=concurrency,
        on_progress=on_progress,
    )


def _parse_all(docs: list[Path]) -> dict[Path, tuple[etree._ElementTree, list[BlockUnit]]]:
    parsed = {}
    for doc in docs:
        tree = parse_xhtml(str(doc))
        parsed[doc] = (tree, collect_blocks(tree.getroot()))
    return parsed


def _find_refs(workdir: Path, docs: list[Path], parsed: dict) -> set[Path]:
    return reference_docs(workdir, docs, {d: (t.getroot(), u) for d, (t, u) in parsed.items()})


def run_dry_run(
    *,
    source: Path,
    mode: str,
    skip_refs: bool = False,
    batch_size: int | None = None,
    batch_chars: int | None = None,
) -> None:
    """Parse-only: unpack to a temp dir, report block counts, leave no state."""
    batch_size = batch_size or DEFAULT_BATCH_SIZE
    batch_chars = batch_chars or DEFAULT_BATCH_CHARS
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
        parsed = _parse_all(docs)
        refs = _find_refs(tmp, docs, parsed)
        unique: dict[str, None] = {}
        for doc in docs:
            units = parsed[doc][1]
            is_ref = doc.resolve() in refs
            label = ""
            if is_ref:
                label = "  [yellow](bibliography/index: skipped)[/]" if skip_refs else "  (bibliography/index)"
            console.print(f"  {_rel_key(tmp, doc)}: {len(units)} blocks{label}")
            if skip_refs and is_ref:
                continue
            for u in units:
                if needs_translation(u.source_text):
                    unique.setdefault(u.source_text)
        texts = list(unique)
        requests = len(make_batches(texts, max_chars=batch_chars, max_items=batch_size))
        console.print(
            f"[bold]To translate[/]: {len(texts)} unique blocks, "
            f"{sum(map(len, texts)):,} chars, ~{requests} API calls"
        )
        console.print(
            "[green]Dry run complete (no API calls, no state, no output written).[/]"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_job(
    *,
    state: JobState,
    state_path: Path,
    api_key: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Translate every pending doc.

    All pending docs are parsed up front; their blocks are filtered
    (``needs_translation``), de-duplicated job-wide, packed into batches by
    character count, and sent ``concurrency`` at a time. A doc is written and
    recorded in ``state.completed_docs`` as soon as every batch it needs has
    returned, so resume granularity stays one doc.
    """
    source = Path(state.source)
    output = Path(state.output)
    workdir = Path(state.workdir)

    docs = list_content_docs(workdir)
    done_before = set(state.completed_docs)
    state.total_docs = len(docs)
    state.failed_doc = None
    state.error = None
    state.save(state_path)

    parsed = _parse_all(docs)
    refs = _find_refs(workdir, docs, parsed) if state.skip_refs else set()

    texts: list[str] = []
    text_index: dict[str, int] = {}
    work: list[_DocWork] = []
    for doc in docs:
        key = _rel_key(workdir, doc)
        if key in done_before:
            continue
        tree, units = parsed[doc]
        if doc.resolve() in refs:
            units = []
        units = [u for u in units if needs_translation(u.source_text)]
        item = _DocWork(key=key, path=doc, tree=tree, units=units)
        for u in units:
            if u.source_text not in text_index:
                text_index[u.source_text] = len(texts)
                texts.append(u.source_text)
            item.text_ids.append(text_index[u.source_text])
        work.append(item)

    # The book title rides along with the body text (often a duplicate of a block).
    original_title = read_title(workdir)
    title_id: int | None = None
    if state.title_zh is None and original_title and needs_translation(original_title):
        if original_title not in text_index:
            text_index[original_title] = len(texts)
            texts.append(original_title)
        title_id = text_index[original_title]

    batches = make_batches(texts, max_chars=state.batch_chars, max_items=state.batch_size)
    batch_of_text: dict[int, int] = {t: b for b, ids in enumerate(batches) for t in ids}
    for item in work:
        item.waiting = {batch_of_text[t] for t in item.text_ids}
    results: list[str] = [""] * len(texts)

    console.print(
        f"[bold]Source[/]: {source}\n"
        f"[bold]Output[/]: {output}\n"
        f"[bold]Mode[/]: {state.mode}\n"
        f"[bold]Docs[/]: {len(docs)} total, {len(work)} pending"
        + (f", {len(refs)} bibliography/index skipped" if refs else "")
        + f"\n[bold]Blocks[/]: {len(texts)} unique in {len(batches)} API calls, "
        f"{concurrency} at a time"
    )

    # Stable per-output session id so OpenCode Go can route and prompt-cache a
    # long job across resume; override with OPENCODE_SESSION when needed.
    session_id = os.environ.get("OPENCODE_SESSION") or hashlib.sha1(
        str(output).encode()
    ).hexdigest()
    translator = Translator(
        api_key=api_key,
        base_url=state.base_url or None,
        model=state.model,
        session_id=session_id,
    )

    prior = len(done_before & {_rel_key(workdir, d) for d in docs})
    blocks_done = 0

    def report() -> None:
        if on_progress is None:
            return
        if texts:
            share = blocks_done / len(texts)
            fraction = (prior + (len(docs) - prior) * share) / len(docs)
        else:
            fraction = len(state.completed_docs) / len(docs) if docs else 1.0
        on_progress(
            JobProgress(
                docs_done=len(state.completed_docs),
                docs_total=len(docs),
                blocks_done=blocks_done,
                blocks_total=len(texts),
                fraction=min(fraction, 1.0),
            )
        )

    def finish(item: _DocWork) -> None:
        if item.units:
            apply_translations(item.units, [results[t] for t in item.text_ids], state.mode)
            item.path.write_bytes(serialize_xhtml(item.tree))
        state.completed_docs.append(item.key)
        state.save(state_path)

    pending: list[_DocWork] = []
    for item in work:
        if item.waiting:
            pending.append(item)
        else:
            finish(item)  # nothing to translate (empty, skipped, or numbers only)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        disable=on_progress is not None,
    ) as progress:
        task = progress.add_task("Translating", total=len(texts) or 1)
        report()
        executor = ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="epub-zh-api")
        try:
            futures: dict[Future[list[str]], int] = {
                executor.submit(translator.translate_batch, [texts[i] for i in ids]): b
                for b, ids in enumerate(batches)
            }
            for fut in as_completed(futures):
                b = futures[fut]
                try:
                    translated = fut.result()
                except Exception as exc:  # noqa: BLE001 — persist failure for resume
                    key = next((item.key for item in pending if b in item.waiting), "(book title)")
                    state.failed_doc = key
                    state.error = str(exc)
                    state.save(state_path)
                    console.print(f"[red]Failed on {key}: {exc}[/]")
                    raise TranslationFailed(key, exc) from exc
                for i, text in zip(batches[b], translated, strict=True):
                    results[i] = text
                if title_id is not None and title_id in batches[b]:
                    state.title_zh = results[title_id]
                    state.save(state_path)
                blocks_done += len(batches[b])
                still: list[_DocWork] = []
                for item in pending:
                    item.waiting.discard(b)
                    if item.waiting:
                        still.append(item)
                    else:
                        finish(item)
                pending = still
                progress.update(
                    task,
                    advance=len(batches[b]),
                    description=f"{len(state.completed_docs)}/{len(docs)} docs",
                )
                report()
        finally:
            # On failure / cancel: drop queued batches; in-flight calls finish in the background.
            executor.shutdown(wait=False, cancel_futures=True)

    state.output_title = output_title(original_title, state.title_zh, state.mode) or state.output_title
    apply_output_metadata(workdir, title=state.output_title, mode=state.mode)
    state.save(state_path)
    pack_epub(workdir, output)
    console.print(f"[green]Wrote[/] {output}")
    return state_path
