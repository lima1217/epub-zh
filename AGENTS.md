# AGENTS.md

Instructions for coding agents working in this repository or driving the `epub-zh` CLI.

## What this project is

CLI that translates an English EPUB to Simplified Chinese (or bilingual) via an OpenAI-compatible LLM API. Package: `epub-zh`. Entrypoint: `epub_zh.cli:app`.

Do not reimplement translation in chat — run the CLI.

## Agent skill

End-to-end translate / dry-run / resume workflow:

- [`.agents/skills/epub-zh-translate/SKILL.md`](.agents/skills/epub-zh-translate/SKILL.md)
- Flags, state layout, LLM config details: [`cli-reference.md`](.agents/skills/epub-zh-translate/cli-reference.md)

## Install so `epub-zh` is on PATH

`command not found: epub-zh` means the package is not on the current shell `PATH`. Fix before any config or translate step.

### Option A — project venv (dev)

```bash
cd <repo>
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"   # or: uv pip install -e .
epub-zh version
```

The venv must stay activated in that shell. Leaving the repo directory is fine; deactivating is not.

### Option B — user tool install (any directory)

```bash
uv tool install -e <repo>
# ensure ~/.local/bin is on PATH (uv usually handles this)
which epub-zh && epub-zh version
```

If `which` is empty after install:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Persist that line in the user’s shell rc only if they ask.

## LLM API configuration (persistent)

Preferred path for open-source / multi-session use: **config file**, not pasting keys into chat or one-off `export`.

### One-time setup

```bash
epub-zh config init
epub-zh config path          # usually ~/.config/epub-zh/config.toml
```

Edit the file (agent: open the path from `config path`; user fills secrets — do not echo full keys back in chat):

```toml
api_key = "sk-..."
base_url = "https://api.openai.com/v1"   # optional; omit for OpenAI default
model = "gpt-4o-mini"                    # optional
batch_size = 8                           # optional, 1..40
```

Any OpenAI-compatible gateway works (`base_url` + that vendor’s `api_key` / `model`).

Verify without printing the raw key:

```bash
epub-zh config show
```

`api_key` appears redacted; `sources` column shows `cli` / `env:…` / config path / `default`.

### Project-local override (optional)

```bash
epub-zh config init --local   # → ./.epub-zh.toml
```

`.epub-zh.toml` is gitignored. Use for per-repo model/base_url; prefer user config for the key when possible.

### Precedence (high → low)

1. CLI flags (`--api-key`, `--base-url`, `--model`, `--batch-size`)
2. Environment (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`)
3. `./.epub-zh.toml`
4. `~/.config/epub-zh/config.toml` (or `$XDG_CONFIG_HOME/epub-zh/config.toml`)
5. Defaults (`model=gpt-4o-mini`, `batch_size=8`)

### Resume and keys

`epub-zh resume` reloads `model` / `base_url` / `batch_size` from `state.json`. The API key is **never** stored in state — resolve it again via config / env / `--api-key`.

### Secrets

- Do not commit `config.toml`, `.epub-zh.toml`, or keys.
- Do not paste full API keys into PR descriptions, issues, or agent transcripts.
- Prefer `epub-zh config init` + user edit over asking the user to `export` in every session.

## Verification

```bash
epub-zh version
epub-zh config show
epub-zh translate <book.epub> --dry-run
python -m pytest -q          # from repo with dev deps
```

## Layout

| Path | Role |
|------|------|
| `src/epub_zh/cli.py` | Typer CLI |
| `src/epub_zh/config.py` | Config load / precedence |
| `src/epub_zh/pipeline.py` | Translate / dry-run / resume orchestration |
| `src/epub_zh/translate.py` | XHTML blocks + LLM batch + retries |
| `src/epub_zh/epub_io.py` | Unpack/pack EPUB, state, zip-slip guards |
| `tests/` | Unit tests |

Human-oriented short README: [`README.md`](README.md).
