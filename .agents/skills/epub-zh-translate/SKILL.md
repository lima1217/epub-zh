---
name: epub-zh-translate
description: 用 epub-zh 把英文 EPUB 译成简体中文（config / dry-run / pipeline / resume / serve 网页）；长时任务在本机系统终端跑。
disable-model-invocation: true
---

# epub-zh translate pipeline

Drive the `epub-zh` CLI end-to-end. Orchestrate the CLI; translation happens inside it.

**Leading words:** *pipeline* (paid translate job), *dry-run* (parse-only gate), *resume* (continue from `state.json`), *serve* (drag-and-drop web page for the user), *config* (persistent LLM settings), *host terminal* (OS Terminal / iTerm).

Install / PATH / LLM config: [`AGENTS.md`](../../../AGENTS.md). Flags, config fields, state, failure recovery, host-terminal launch: [`cli-reference.md`](cli-reference.md) — open on `command not found`, flag choice, API wiring, *host terminal* launch, exit diagnosis, or *resume*.

## Where to run

| Command class | Where |
|---------------|--------|
| Short / read-only (`version`, `config *`, `status`, `translate --dry-run`) | Agent shell OK |
| *pipeline*, *resume*, *serve* | *host terminal* only |

Run the preflight, launch the exact command in Terminal.app or iTerm, leave that window open, and monitor from the agent — the agent does not own the process. Binary, preflight, launch recipes: [`cli-reference.md`](cli-reference.md) § Host terminal.

Cursor/agent PTYs end with the chat turn; `state.json` keeps progress, the process does not.

## Branch

Pick one before acting:

| Branch | When |
|--------|------|
| *config* | API setup needed, or `epub-zh` / key missing |
| *dry-run* | Scope only, or a new book not gated yet |
| *pipeline* | Fresh translate to a new output EPUB |
| *resume* | Prior run failed/interrupted; `.epub-zh-state/<output-stem>/state.json` exists |
| *serve* | User wants the web page to drag books in themselves |

*config* before *pipeline* / *resume* / *serve* when `command not found` or `api_key` unset. *resume* skips step 2. *dry-run*-only jobs stop after step 2. *serve* goes from step 1 to § Serve.

## Steps

### 1. Ready the toolchain + LLM config

**PATH:** If `epub-zh` is missing, install per `AGENTS.md`. Gate: `epub-zh version`.

**API:** `epub-zh config show`; if `api_key` is unset, `epub-zh config init`, give the user the path from `epub-zh config path` to fill in, then `epub-zh config check`. Fields and precedence: [`cli-reference.md`](cli-reference.md) § LLM API configuration.

**Done when:** `epub-zh version` works; for paid runs, `config check` prints `OK`. Secrets stay redacted in the transcript.

### 2. Gate with *dry-run*

On *dry-run* or before a new *pipeline* (agent shell OK):

```bash
epub-zh translate <source.epub> --dry-run
```

**Done when:** output ends with `Dry run complete`, and you have reported to the user: doc count, unique blocks, characters, estimated API calls (`To translate:` line), and any docs labelled `(bibliography/index)` — offering `--skip-refs` when there are. No state dir and no output EPUB exist.

### 3. Run *pipeline* or *resume* (*host terminal*)

Build the command, then start it outside the agent shell (§ Where to run).

**pipeline** — new EPUB (source untouched):

```bash
epub-zh translate <source.epub> -o <output.epub> --mode zh
# --mode bilingual   English block, then Chinese block
# --skip-refs        leave bibliography / index in English (if the user chose it)
# -j N               concurrency override; on 429s, lower it
```

State: `<output-dir>/.epub-zh-state/<output-stem>/state.json`.

**resume** — after interrupt/API failure (`workdir` must still exist):

```bash
epub-zh resume <path-to>/state.json
```

Job settings reload from state (list: [`cli-reference.md`](cli-reference.md) § `epub-zh resume`).

While it runs: poll `epub-zh status <state.json>` until it prints `complete` or `failed`.

**Done when:**
- Success: `status` prints `complete N/N -> <output>`, and that file exists and is non-empty.
- Failure: `status` prints `failed … on <doc>: <error>`; state retained — fix the cause and *resume* in the *host terminal* ([`cli-reference.md`](cli-reference.md) § Failure recovery); wipe state only if the user asks.

### 4. Hand back the artifact

Confirm output path, mode, the translated title (`output_title` in `state.json`), and the state dir if a *resume* may follow.

**Done when:** the user has the output EPUB path and knows whether the job finished or needs *resume*.

## Serve

Launch `epub-zh serve --no-open` in a *host terminal* (§ Where to run); it prints `epub-zh web UI: http://127.0.0.1:<port>/`.

**Done when:** that URL answers (`curl -s http://127.0.0.1:<port>/api/jobs` returns JSON) and the user has the URL. The user drives the page; jobs and results persist in `~/.local/share/epub-zh/jobs/`. To stop the server, send it Ctrl+C after checking `/api/jobs` shows no `running` job.
