---
name: epub-zh-translate
description: 用 epub-zh CLI 把英文 EPUB 译成简体中文（config / dry-run / pipeline / resume）；长时任务在本机系统终端跑。
disable-model-invocation: true
---

# epub-zh translate pipeline

Drive the `epub-zh` CLI end-to-end. Orchestrate the CLI; translation happens inside it.

**Leading words:** *pipeline* (paid translate job), *dry-run* (parse-only gate), *resume* (continue from `state.json`), *config* (persistent LLM settings), *host terminal* (OS Terminal / iTerm).

Install / PATH / LLM config: [`AGENTS.md`](../../../AGENTS.md). Flags, state, failure recovery, host-terminal launch: [`cli-reference.md`](cli-reference.md) — open on `command not found`, flag choice, API wiring, exit diagnosis, or *resume*.

## Where to run

| Command class | Where |
|---------------|--------|
| Short / read-only (`version`, `config *`, `translate --dry-run`) | Agent shell OK |
| *pipeline* and *resume* | *host terminal* only |

Prepare the exact command, launch it in Terminal.app or iTerm, leave that window open, and monitor via `state.json` / the output EPUB — the agent does not own the process. Launch recipes: [`cli-reference.md`](cli-reference.md) § Host terminal.

Cursor/agent PTYs end with the chat turn; `state.json` keeps progress, the process does not.

## Branch

Pick one before acting:

| Branch | When |
|--------|------|
| *config* | API setup needed, or `epub-zh` / key missing |
| *dry-run* | Scope only, or a new book not gated yet |
| *pipeline* | Fresh translate to a new output EPUB |
| *resume* | Prior run failed/interrupted; `.epub-zh-state/<output-stem>/state.json` exists |

*config* before *pipeline* / *resume* when `command not found` or `api_key` unset. *resume* skips step 2. *dry-run*-only jobs stop after step 2.

## Steps

### 1. Ready the toolchain + LLM config

**PATH:** If `epub-zh` is missing, install (venv or `uv tool install -e <repo>`) per `AGENTS.md`. Gate: `epub-zh version`.

**API (prefer file):**

```bash
epub-zh config init          # once; skip if config show already has api_key
epub-zh config path          # user edits secrets at this path
epub-zh config show          # redacted key + source column
```

User edits `api_key` / optional `base_url` / `model` in that TOML. OpenAI-compatible gateways: vendor `base_url` + key + model. Fields and precedence: [`cli-reference.md`](cli-reference.md) § LLM API configuration.

**Done when:** `epub-zh version` works; for paid runs, `config show` reports `api_key` set (or env/CLI equivalent). Keep full secrets out of the transcript.

### 2. Gate with *dry-run*

On *dry-run* or before a new *pipeline* (agent shell OK):

```bash
epub-zh translate <source.epub> --dry-run
```

**Done when:** output lists per-doc block counts and `Dry run complete`; no state dir and no output EPUB. For *pipeline*, report doc count and total blocks before the paid run.

### 3. Run *pipeline* or *resume* (*host terminal*)

Build the command, then start it outside Cursor (see § Where to run).

**pipeline** — new EPUB (source untouched):

```bash
epub-zh translate <source.epub> -o <output.epub> --mode zh
# or: --mode bilingual
```

State: `<output-dir>/.epub-zh-state/<output-stem>/state.json`.

**resume** — after interrupt/API failure (`workdir` must still exist):

```bash
epub-zh resume <path-to>/state.json
```

Mode/model/base URL/batch size reload from state; API key from config / env / `--api-key` only.

While it runs: poll `state.json` (`completed_docs`, `failed_doc`, `error`) and whether `-o` exists. User keeps the *host terminal* open until `Wrote <output>` appears there.

**Done when:**
- Success: host terminal shows `Wrote <output.epub>` and that file exists and is non-empty.
- Failure: exit non-zero with `Failed on <doc>`; state retained — *resume* again in the *host terminal* ([`cli-reference.md`](cli-reference.md)), wipe state only if the user asks.

### 4. Hand back the artifact

Confirm output path, mode, and (if relevant) state dir for future *resume*.

**Done when:** user has the output EPUB path and knows whether the job finished or needs *resume*.
