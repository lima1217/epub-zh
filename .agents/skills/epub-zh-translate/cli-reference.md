# epub-zh CLI reference

Disclosed reference for [epub-zh-translate](SKILL.md). Load on flag choice, API wiring, state layout, failure recovery, or *host terminal* launch.

## Host terminal

*pipeline* and *resume* run in a *host terminal* (macOS Terminal.app, iTerm). Short commands (`version`, `config *`, `translate --dry-run`) may use the agent shell.

### Launch (macOS Terminal.app)

Open a real Terminal window with the full command (quote paths that contain spaces):

```bash
osascript -e 'tell application "Terminal" to do script "epub-zh translate \"/path/to/book.epub\" -o \"/path/to/book_bilingual.epub\" --mode bilingual"'
```

Resume:

```bash
osascript -e 'tell application "Terminal" to do script "epub-zh resume \"/path/to/.epub-zh-state/book_bilingual/state.json\""'
```

If `epub-zh` is only on PATH inside a venv, put `source /path/to/repo/.venv/bin/activate &&` before the CLI in the same `do script` string (or use `uv tool install` + `~/.local/bin` on the login PATH).

Fallback: paste the exact command for the user to run in their system terminal.

### Monitor from the agent

Inspect — do not own the long process:

1. Host terminal output (`Wrote …` / `Failed on …`).
2. `state.json`: `completed_docs`, `failed_doc`, `error`, `workdir`.
3. Whether the `-o` EPUB exists and is non-empty.

If a prior job died inside Cursor but `workdir` remains, *resume* from the *host terminal*.

## Install / PATH

Gate: `epub-zh version`. On `command not found`, install per [`AGENTS.md`](../../../AGENTS.md) (venv or `uv tool install -e <repo>`), then re-gate.

## LLM API configuration

### Flow

1. `epub-zh` on `PATH` (above).
2. No key yet: `epub-zh config init` → path from `epub-zh config path` → user edits `api_key` (optional `base_url` / `model`).
3. `epub-zh config show` — `api_key` set (redacted); note `[source]`.
4. Proceed to *dry-run* / *pipeline*. Prefer config file over chat paste.

### Files and fields

| File | Purpose |
|------|---------|
| `~/.config/epub-zh/config.toml` | User defaults (`$XDG_CONFIG_HOME/epub-zh/config.toml`) |
| `./.epub-zh.toml` | Optional cwd override (gitignored) |

```toml
api_key = "sk-..."
base_url = "https://api.openai.com/v1"   # optional
model = "gpt-4o-mini"                    # optional
batch_size = 8                           # optional, 1..40
```

OpenAI-compatible providers: that vendor’s `base_url`, `api_key`, and `model`.

**Precedence (high → low):** CLI flags → `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` → `.epub-zh.toml` → user `config.toml` → defaults (`gpt-4o-mini`, batch 8).

| Subcommand | Role |
|------------|------|
| `init` | Write user config (`--local` → `.epub-zh.toml`; `--force` overwrites) |
| `show` | Resolved settings + source (`api_key` redacted) |
| `path` | Print path (`--local` for project file) |

`init` sets mode `0600` when the OS allows. Key is never in `state.json`; *resume* resolves it again. Keep keys out of commits, chat, and PR/issue text.

## Commands

### `epub-zh translate`

| Argument / flag | Role |
|-----------------|------|
| `SOURCE` | Input EPUB (required; DRM-free, text-based) |
| `-o` / `--output` | Output EPUB — required unless `--dry-run` |
| `-m` / `--mode` | `zh` (default) or `bilingual` (EN block, then ZH sibling) |
| `--model` | Override model (else env / config / `gpt-4o-mini`) |
| `--base-url` | Override API base (else env / config / OpenAI default) |
| `--api-key` | Override key (else env / config) |
| `--batch-size` | Blocks per API call (1–40; else config / 8) |
| `--dry-run` | Parse + count blocks only; no API, no state, no output |

### `epub-zh resume`

| Argument / flag | Role |
|-----------------|------|
| `STATE` | Path to `state.json` from a prior run |
| `--api-key` | Same as translate |

Reloads `source`, `output`, `mode`, `model`, `base_url`, `batch_size`, `workdir`, `completed_docs` from state. API key from config / env / flag only.

### `epub-zh version`

Print package version.

## State layout

For output `/path/to/book_zh.epub`:

```
/path/to/.epub-zh-state/book_zh/
  state.json
  work-<hash>/          # unpacked EPUB working tree
```

Fields that matter: `completed_docs`, `failed_doc`, `error`, `workdir`, `output`, `mode`.

*dry-run* never creates this tree.

## Failure recovery

1. Read `failed_doc` / `error` from `state.json`.
2. Confirm `workdir` still exists (missing → fresh *pipeline* to the same `-o` or a new output).
3. Fix transient cause (key, network, quota) if needed — `epub-zh config show`.
4. `epub-zh resume <state.json>` — skips docs in `completed_docs`.

API layer retries rate limits, timeouts, 5xx, and unparseable numbered output (up to 6 attempts with backoff) before failing the doc.

## Constraints

- Write a **new** EPUB via `-o`; source file stays unchanged.
- *dry-run* before first paid *pipeline* on an unfamiliar book.
- *resume* when state + workdir exist (over re-running *translate*).
- Config file for credentials (over chat paste or shell-only `export`).
- `zh` mode flattens inline markup inside a replaced block to plain text.
- Input: text-based, DRM-free EPUB (not scan-image books).
