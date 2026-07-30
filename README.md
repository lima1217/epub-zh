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

Environment variables still work:

```bash
export OPENAI_API_KEY=sk-...
# optional
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_MODEL=gpt-4o-mini
```

Example config.toml:

```toml
api_key = "sk-..."
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
batch_size = 8
```

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
