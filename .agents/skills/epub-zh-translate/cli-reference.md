# epub-zh CLI reference

Disclosed reference for [epub-zh-translate](SKILL.md). Load on flag choice, API wiring, state layout, failure recovery, or *host terminal* launch.

## Host terminal

*pipeline* and *resume* run in a *host terminal* (macOS Terminal.app, iTerm). Short commands (`version`, `config *`, `translate --dry-run`) may use the agent shell.

### Binary

A fresh Terminal window has no venv activated and macOS privacy (TCC) may block it from `~/Desktop`, `~/Documents`, `~/Downloads`. Launch the user-tool install by absolute path: `uv tool install <repo>` (no `-e`, so it copies the code and never reads the repo at runtime) → `~/.local/bin/epub-zh`. Re-run `uv tool install --reinstall <repo>` after repo changes.

### Preflight (macOS)

Terminal must *read* the source EPUB and the output dir — `ls -d` passes even when TCC blocks reads, so test with `head`:

```bash
osascript -e 'tell application "Terminal" to do script "head -c1 \"/path/to/book.epub\" >/dev/null 2>/tmp/epubzh-preflight.txt; echo rc=$? >>/tmp/epubzh-preflight.txt; exit"'
```

`rc=0` → launch. `Operation not permitted` → either the user grants Terminal *Files and Folders* access (System Settings → Privacy & Security; Terminal restart required), or copy the EPUB to an unprotected dir such as `~/epubzh-work` and run there.

### Launch (macOS Terminal.app)

Write the command to a script (avoids nested quoting with long book names), then open it in Terminal:

```bash
cat > /tmp/epubzh-run.sh <<'EOF'
#!/bin/zsh
~/.local/bin/epub-zh translate "/path/to/book.epub" -o "/path/to/book_bilingual.epub" --mode bilingual
echo "[epub-zh exited with code $?]"
EOF
chmod +x /tmp/epubzh-run.sh
osascript -e 'tell application "Terminal" to do script "/tmp/epubzh-run.sh"'
```

*resume*: same script with `~/.local/bin/epub-zh resume "/path/to/.epub-zh-state/book_bilingual/state.json"`.

Fallback: paste the exact command for the user to run in their system terminal.

### Monitor from the agent

Inspect — do not own the long process:

1. `epub-zh status <state.json>` — one line: `running 12/30`, `complete 30/30 -> <output>`, or `failed 12/30 on <doc>: <error>` (exit 1). Poll it from a background loop; it covers every terminal state.
2. Host terminal output (`Wrote …` / `Failed on …`): `osascript -e 'tell application "Terminal" to get history of selected tab of front window'`.

If a prior job died inside Cursor but `workdir` remains, *resume* from the *host terminal*.

## Install / PATH

Gate: `epub-zh version`. On `command not found`, install per [`AGENTS.md`](../../../AGENTS.md) (venv or `uv tool install -e <repo>`), then re-gate.

`.venv/bin/epub-zh` exists but fails → the venv was built at another path (repo moved; scripts hard-code it in their first line, `head -1 .venv/bin/epub-zh`). Rebuild: `uv venv --clear --python 3.11 .venv && uv pip install -e ".[dev]"`.

## LLM API configuration

### Flow

1. `epub-zh` on `PATH` (above).
2. No key yet: `epub-zh config init` → path from `epub-zh config path` → user edits `api_key`. Change other fields with `epub-zh config set <name> <value>`.
3. `epub-zh config show` — `api_key` set (redacted); note `[source]`.
4. `epub-zh config check` — `OK` means the key authenticates and the gateway serves `model`; `FAIL` lists the served model ids.
5. Proceed to *dry-run* / *pipeline*. Prefer config file over chat paste.

### Files and fields

| File | Purpose |
|------|---------|
| `~/.config/epub-zh/config.toml` | User defaults (`$XDG_CONFIG_HOME/epub-zh/config.toml`) |
| `./.epub-zh.toml` | Optional cwd override (gitignored) |

```toml
api_key = "sk-..."
base_url = "https://api.deepseek.com/v1"   # optional; built-in default is DeepSeek official
model = "deepseek-flash"                   # optional
batch_size = 8                           # optional, 1..40
```

OpenAI-compatible providers: that vendor’s `base_url`, `api_key`, and `model`. Built-in defaults point at **DeepSeek official** (`base_url=https://api.deepseek.com/v1`, `model=deepseek-flash`).

**Precedence (high → low):** CLI flags → `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` → `.epub-zh.toml` → user `config.toml` → defaults (`deepseek-flash`, DeepSeek official base, batch 8).

| Subcommand | Role |
|------------|------|
| `init` | Write user config (`--local` → `.epub-zh.toml`; `--force` overwrites) |
| `show` | Resolved settings + source (`api_key` redacted) |
| `path` | Print path (`--local` for project file) |
| `set NAME VALUE` | Edit one field in place (`--local` for project file); validates before writing |
| `check` | `GET /models`: key works and `model` is served (exit 1 otherwise) |

`init` sets mode `0600` when the OS allows. Key is never in `state.json`; *resume* resolves it again. Keep keys out of commits, chat, and PR/issue text.

## Commands

### `epub-zh translate`

| Argument / flag | Role |
|-----------------|------|
| `SOURCE` | Input EPUB (required; DRM-free, text-based) |
| `-o` / `--output` | Output EPUB — required unless `--dry-run` |
| `-m` / `--mode` | `zh` (default) or `bilingual` (EN block, then ZH sibling) |
| `--model` | Override model (else env / config / `deepseek-flash`) |
| `--base-url` | Override API base (else env / config / DeepSeek official `https://api.deepseek.com/v1`) |
| `--api-key` | Override key (else env / config) |
| `--batch-size` | Blocks per API call (1–40; else config / 8) |
| `--dry-run` | Parse + count blocks only; no API, no state, no output |

### `epub-zh resume`

| Argument / flag | Role |
|-----------------|------|
| `STATE` | Path to `state.json` from a prior run |
| `--api-key` | Same as translate |

Reloads `source`, `output`, `mode`, `model`, `base_url`, `batch_size`, `workdir`, `completed_docs` from state. API key from config / env / flag only.

### `epub-zh status`

`STATE` → one-line progress (see § Monitor from the agent). Exit 1 when the job failed.

### `epub-zh serve`

Local drag-and-drop web page for humans (`--port 8765`, `--jobs-dir`, `--open/--no-open`). Long-running like `translate`: launch it in a *host terminal*, not the agent shell. Binds 127.0.0.1; jobs persist under `~/.local/share/epub-zh/jobs/<id>/` (`meta.json`, `source.epub`, `output.epub`, `.epub-zh-state/output/state.json`), so `epub-zh status` works on a web job's `state.json` too.

### `epub-zh version`

Print package version.

## State layout

For output `/path/to/book_zh.epub`:

```
/path/to/.epub-zh-state/book_zh/
  state.json
  work-<hash>/          # unpacked EPUB working tree
```

Fields that matter: `completed_docs` (list of doc paths), `total_docs`, `failed_doc`, `error`, `workdir`, `output`, `mode`.

*dry-run* never creates this tree.

## Failure recovery

1. Read `failed_doc` / `error` from `state.json`.
2. Confirm `workdir` still exists (missing → fresh *pipeline* to the same `-o` or a new output).
3. Fix transient cause (key, network, quota) if needed — `epub-zh config check`.
4. `epub-zh resume <state.json>` — skips docs in `completed_docs`.

API layer retries rate limits, timeouts, 5xx, and unparseable numbered output (up to 6 attempts with backoff) before failing the doc.

## Constraints

- Write a **new** EPUB via `-o`; source file stays unchanged.
- *dry-run* before first paid *pipeline* on an unfamiliar book.
- *resume* when state + workdir exist (over re-running *translate*).
- Config file for credentials (over chat paste or shell-only `export`).
- `zh` mode flattens inline markup inside a replaced block to plain text.
- Input: text-based, DRM-free EPUB (not scan-image books).
