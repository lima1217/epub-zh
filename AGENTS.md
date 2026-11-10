# AGENTS.md

Instructions for coding agents working in this repository or driving the `epub-zh` CLI.

## What this project is

Translates an English EPUB to Simplified Chinese (`zh`) or English/Chinese (`bilingual`) via an OpenAI-compatible LLM API. Package `epub-zh`, entrypoint `epub_zh.cli:app`. Two front ends over one pipeline: the CLI (`translate`, `resume`, `status`, `config`) and a local web page (`serve`). The web page also has a 播客字幕 tab (Xiaoyuzhou podcast transcripts through the separately installed `xyz` CLI) and a YouTube 字幕 tab (captions through `youtube-transcript-api`). Human-facing overview: [`README.md`](README.md).

## Driving translations

*pipeline* / *dry-run* / *resume* / *serve* / *config*: [`.agents/skills/epub-zh-translate/SKILL.md`](.agents/skills/epub-zh-translate/SKILL.md). Flags, config fields, state, *host terminal* launch, failure recovery: [`cli-reference.md`](.agents/skills/epub-zh-translate/cli-reference.md).

*pipeline*, *resume*, and *serve* are long-running: run them in a *host terminal* (OS Terminal / iTerm), where they outlive the agent's turn. Short checks (`version`, `config *`, `status`, `--dry-run`) may use the agent shell.

## Install so `epub-zh` is on PATH

`command not found: epub-zh` means the package is not on the current shell `PATH`. Fix before any config or translate step.

**Project venv (development):**

```bash
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
epub-zh version
```

The venv works only while activated in that shell.

**User tool install (any directory, any terminal):**

```bash
uv tool install <repo>              # copies the code into ~/.local/bin/epub-zh
uv tool install --reinstall <repo>  # after repo changes
which epub-zh && epub-zh version
```

`uv tool install -e <repo>` (editable) picks up repo changes without reinstalling, but reads the repo at runtime: with the repo under `~/Desktop`, `~/Documents`, or `~/Downloads`, macOS privacy rules can make it fail in Terminal.app. Use the copying install for *host terminal* runs. If `which` is empty, `export PATH="$HOME/.local/bin:$PATH"` (persist it in the shell rc only if the user asks).

## LLM API configuration

Keys live in a config file the user edits, never in chat:

```bash
epub-zh config init      # once → ~/.config/epub-zh/config.toml
epub-zh config path      # give the user this path to set api_key
epub-zh config show      # api_key redacted; source column per field
epub-zh config check     # OK = key authenticates and model is served
```

Built-in default: DeepSeek official, `base_url = "https://api.deepseek.com/v1"`, `model = "deepseek-flash"`. Change non-secret fields with `epub-zh config set <name> <value>`. Fields, ranges, precedence: [`cli-reference.md`](.agents/skills/epub-zh-translate/cli-reference.md) § LLM API configuration.

The API key is never stored in `state.json`; *resume* resolves it again from config / env / `--api-key`.

Secrets: keep `config.toml`, `.epub-zh.toml`, and keys out of commits, PR/issue text, and transcripts; print keys only redacted.

## Verification

```bash
python -m pytest -q                        # from the activated dev venv
epub-zh translate <book.epub> --dry-run    # parse-only, no API
epub-zh config check                       # live key check
```

Real translation spends the user's API credits. To exercise the pipeline or web page, monkeypatch `epub_zh.pipeline.Translator` with a fake (as `tests/` does); use the real API only for a deliberate, small check. Podcast tests pass a fake `runner` to `PodcastStore` / `fetch_transcript` instead of calling `xyz`; YouTube tests pass a fake `lister` / `describer` to `YouTubeStore` / `fetch_video` instead of calling YouTube.

`tests/test_docs_defaults.py` requires `README.md`, `AGENTS.md`, and `cli-reference.md` to name the current default model and base URL: update all three when `config.py` defaults change.

`serve` reads `web/index.html` on every request, so page edits show on reload; Python changes need a server restart.

## Layout

| Path | Role |
|------|------|
| `src/epub_zh/cli.py` | Typer CLI |
| `src/epub_zh/config.py` | Config fields, load, precedence (single source for settings) |
| `src/epub_zh/pipeline.py` | Translate / dry-run / resume: parse all docs, skip + dedupe blocks, batch by chars, concurrent API calls, per-doc completion |
| `src/epub_zh/translate.py` | XHTML blocks, apply translations, prompt, LLM batch + retries |
| `src/epub_zh/backmatter.py` | Bibliography / index detection for `--skip-refs` |
| `src/epub_zh/metadata.py` | Translated title, identifier suffix, language in OPF / NCX |
| `src/epub_zh/epub_io.py` | Unpack/pack EPUB, `JobState`, zip-slip guards |
| `src/epub_zh/server.py` | `epub-zh serve`: stdlib HTTP server, job queue, podcast transcript store, Host/Origin checks |
| `src/epub_zh/transcripts.py` | Shared transcript store (one JSON per item), `TranscriptError`, timestamps |
| `src/epub_zh/podcast.py` | Xiaoyuzhou transcripts: episode-link parsing, `xyz` subprocess calls, Markdown / timestamped rendering |
| `src/epub_zh/youtube.py` | YouTube captions: link parsing, track choice, `youtube-transcript-api` + oEmbed title, rendering |
| `src/epub_zh/web/index.html` | Drag-and-drop page served by `serve` |
| `tests/` | Unit tests (fake translator, no network) |
