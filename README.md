# epub-zh

CLI that translates an English EPUB into Simplified Chinese (or bilingual) using an OpenAI-compatible LLM API.

Agent-oriented setup (install PATH, persistent LLM config, translate workflow): see [`AGENTS.md`](AGENTS.md).

## Install

**Dev (venv):**

```bash
cd epub-zh
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e .
```

**Anywhere on your machine** (puts `epub-zh` on PATH via `~/.local/bin`):

```bash
uv tool install -e /path/to/epub-zh
```

If you see `command not found: epub-zh`, the shell is not using the venv and the tool is not on `PATH` — use one of the installs above, then `epub-zh version`.

## Config

Persistent settings (recommended):

```bash
epub-zh config init          # writes ~/.config/epub-zh/config.toml
# edit api_key / base_url / model in that file
epub-zh config show          # resolved values + source
```

Optional project override in the current directory: `epub-zh config init --local` → `.epub-zh.toml`.

**Precedence:** CLI flags > environment variables > `.epub-zh.toml` > `~/.config/epub-zh/config.toml` > defaults.

**Built-in default gateway is DeepSeek official** (OpenAI-compatible): `base_url=https://api.deepseek.com/v1`, `model=deepseek-flash`. Any OpenAI-compatible gateway works — just set `base_url`/`model` to your vendor's.

Environment variables still work:

```bash
export OPENAI_API_KEY=sk-...
# optional (DeepSeek default already points here)
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export OPENAI_MODEL=deepseek-flash
```

Example config.toml:

```toml
api_key = "sk-..."
base_url = "https://api.deepseek.com/v1"
model = "deepseek-flash"
batch_size = 8
```

## Web UI (drag & drop)

```bash
epub-zh serve
```

Opens `http://127.0.0.1:8765/` in your browser. Drag one or more EPUB files onto the page, pick **仅中文** or **中英对照**, and click **保存到本地** when a book finishes. API key / base URL / model can be set from the page's **设置** dialog (writes the same `~/.config/epub-zh/config.toml` the CLI uses).

- The server listens on 127.0.0.1 only. Keep its terminal open while books translate; closing the browser tab does not stop a job.
- Uploads and results live in `~/.local/share/epub-zh/jobs/` (`--jobs-dir` to change). After a restart, unfinished jobs show **继续** and resume where they stopped.
- Books translate one at a time, in upload order. `--port` picks another port; `--no-open` skips opening the browser.

## Usage

```bash
# Chinese only
epub-zh translate book.epub -o book_zh.epub --mode zh

# Bilingual (English block, then Chinese block)
epub-zh translate book.epub -o book_bi.epub --mode bilingual

# Parse only — count blocks; no API, no state dir, no output
epub-zh translate book.epub --dry-run

# Resume after interrupt / API failure
epub-zh resume .epub-zh-state/book_zh/state.json
```

Progress is stored under `.epub-zh-state/<output-stem>/` next to the output file (not created for `--dry-run`).

API calls retry on rate limits, timeouts, 5xx, and unparseable model output (exponential backoff, up to 6 attempts).

## Notes

- Input must be DRM-free, text-based EPUB (not scanned images).
- Original file is never modified; only a new EPUB is written.
- Inline markup inside a block is flattened to plain text on replace (zh mode) for reliability.
