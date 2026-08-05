---
name: epub-zh-translate
description: 用 epub-zh CLI 把英文 EPUB 译成简体中文（含 dry-run / resume / LLM 配置）。长时 translate/resume 必须在本机系统终端跑，禁止 Cursor 内置/agent 终端。
disable-model-invocation: true
---

# epub-zh translate pipeline

Drive the `epub-zh` CLI end-to-end. The agent orchestrates the CLI; it does not reimplement translation in chat.

**Leading words:** *pipeline* (ordered job), *dry-run* (parse-only gate before API spend), *resume* (continue from `state.json`), *config* (persistent LLM API settings), *host terminal* (OS Terminal / iTerm — not Cursor).

Install, PATH, and LLM config: repo root [`AGENTS.md`](../../../AGENTS.md). Flag details, config checklist, failure recovery, host-terminal launch: [`cli-reference.md`](cli-reference.md) — open when `command not found`, choosing flags, wiring the API, diagnosing exit, or *resume*.

## Where to run (hard rule)

| Command class | Where |
|---------------|--------|
| Short / read-only (`version`, `config *`, `translate --dry-run`) | Agent shell OK |
| Long-running `translate` (*pipeline*) and `resume` | **Host OS terminal only** |

**Must not** run *pipeline* / *resume* in Cursor’s integrated terminal, agent Shell tool, or any IDE-owned PTY. Those sessions are often aborted when the chat/agent turn ends or the app backgrounds the job — progress survives in `state.json`, but the process dies.

**Do instead:** prepare the exact command, launch it in a real system terminal (macOS: Terminal.app or iTerm), tell the user to leave that window open, and monitor via `state.json` / output file — not by owning the process inside Cursor. Launch recipes: [`cli-reference.md`](cli-reference.md) § Host terminal.

## Branch

Pick one before acting:

| Branch | When |
|--------|------|
| *config* | User needs API setup, or `epub-zh` / key is missing |
| *dry-run* | User wants scope only, or a new book has not been gated yet |
| *pipeline* | Fresh translate to a new output EPUB |
| *resume* | Prior run failed/interrupted and `.epub-zh-state/<output-stem>/state.json` exists |

*config* before *pipeline* / *resume* when `command not found` or `api_key` is unset. *resume* skips step 2. *dry-run*-only jobs stop after step 2.

## Steps

### 1. Ready the toolchain + LLM config

**PATH:** If `epub-zh` is missing, install (venv or `uv tool install -e <repo>`) per `AGENTS.md`. Gate: `epub-zh version`.

**API (prefer file, not chat paste):**

```bash
epub-zh config init          # once; skip if config show already has api_key
epub-zh config path          # tell user this path to edit secrets
epub-zh config show          # redacted key + source column
```

User edits `api_key` / optional `base_url` / `model` in that TOML. OpenAI-compatible gateways: set vendor `base_url` + key + model. Precedence and fields: [`cli-reference.md`](cli-reference.md) § LLM API configuration.

**Done when:** `epub-zh version` works; for non-dry-run, `config show` reports `api_key` set (or env/CLI equivalent). Do not paste full secrets into the transcript.

### 2. Gate with *dry-run*

On *dry-run* or before a new *pipeline*, run parse-only (agent shell OK):

```bash
epub-zh translate <source.epub> --dry-run
```

**Done when:** output lists per-doc block counts and `Dry run complete`; no state dir and no output EPUB. For *pipeline*, report doc count and total blocks to the user before the paid run.

### 3. Run *pipeline* or *resume* (host terminal)

Build the command, then **start it outside Cursor** (see § Where to run). Do not `block_until_ms: 0` background it in the agent Shell.

**pipeline** — write a new EPUB (source file stays untouched):

```bash
epub-zh translate <source.epub> -o <output.epub> --mode zh
# or: --mode bilingual
```

State lands at `<output-dir>/.epub-zh-state/<output-stem>/state.json`.

**resume** — after interrupt/API failure (workdir must still exist):

```bash
epub-zh resume <path-to>/state.json
```

Mode/model/base URL/batch size reload from state; API key from config / env / `--api-key` only.

While it runs: poll `state.json` (`completed_docs`, `failed_doc`, `error`) and whether `-o` exists. Tell the user to keep the host terminal window open until `Wrote <output>` appears there.

**Done when:**
- *pipeline* / *resume* success: host terminal shows `Wrote <output.epub>` and that file exists and is non-empty.
- Failure: exit non-zero with `Failed on <doc>`; state.json retained — switch to *resume* in the **host terminal** again (see [`cli-reference.md`](cli-reference.md)), do not restart from scratch unless the user asks to wipe state.

### 4. Hand back the artifact

Confirm output path, mode used, and (if relevant) state dir for future *resume*.

**Done when:** user has the output EPUB path and knows whether the job finished or needs *resume*.
