# epub-zh

[![test](https://github.com/lima1217/epub-zh/actions/workflows/test.yml/badge.svg)](https://github.com/lima1217/epub-zh/actions/workflows/test.yml)

Translate English EPUB books into Simplified Chinese — Chinese only or English/Chinese side by side — with any OpenAI-compatible LLM API. Use it from a drag-and-drop web page or the command line.

把英文 EPUB 电子书译成简体中文（仅中文或中英对照）。拖进网页即可翻译，也可以用命令行。

- **Drag and drop**: `epub-zh serve` opens a local page; drop books in, pick 仅中文 / 中英对照, save the result.
- **Fast**: requests run concurrently. With DeepSeek at `concurrency = 32`, a ~400-page book translates in roughly 2–3 minutes.
- **Resumable**: progress is saved per chapter file; after a failure or restart, only unfinished files are translated again.
- **Easy to tell apart**: the translated copy gets a Chinese title (e.g. 梦境机器) and its own identifier, so reading apps list it separately from the original.
- **Any OpenAI-compatible API**: DeepSeek official is the default; point `base_url` / `model` at any other gateway.
- **Optional**: leave the bibliography and index in English (`--skip-refs`), which saves requests.
- **Podcast transcripts**: the same page has a **播客字幕** tab that saves a Xiaoyuzhou (小宇宙) episode's official transcript as Markdown — see [Podcast transcripts](#podcast-transcripts).
- **YouTube captions**: a **YouTube 字幕** tab does the same for YouTube videos — see [YouTube captions](#youtube-captions).

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- An API key for an OpenAI-compatible LLM (DeepSeek by default)
- DRM-free, text-based EPUB (not scanned images)

## Install

```bash
git clone https://github.com/lima1217/epub-zh.git
uv tool install ./epub-zh        # puts `epub-zh` on PATH (~/.local/bin)
epub-zh version
```

After `git pull`, run `uv tool install --reinstall ./epub-zh` to pick up changes. For development, see [Development](#development).

## Quick start

```bash
epub-zh config init      # creates ~/.config/epub-zh/config.toml
# open that file and set api_key = "sk-..."
epub-zh config check     # OK = key works and the model is served
epub-zh serve            # opens http://127.0.0.1:8765/
```

## Web page

`epub-zh serve` starts a local server and opens the page in your browser.

1. Drag one or more EPUB files onto the page (or click to choose).
2. Choose **仅中文** or **中英对照**; optionally tick **跳过参考文献和索引**.
3. Watch progress; click **保存到本地** when a book is done. The file is named after the Chinese title, e.g. `梦境机器.epub`.

- **设置** (top right) edits API key, base URL, model, and concurrency — the same `config.toml` the CLI uses. The key is never shown back in full.
- The page is a window onto the server: closing the tab does not stop a job. Stop the server with Ctrl+C in its terminal.
- Jobs live in `~/.local/share/epub-zh/jobs/` (`--jobs-dir` to change). A job interrupted by a server restart shows **继续** and resumes where it stopped.
- Books translate one at a time, in the order you add them. Options: `--port`, `--no-open`.
- The server listens on 127.0.0.1 only and rejects requests from other sites.

### Podcast transcripts

The **播客字幕** tab fetches the official transcript of a Xiaoyuzhou (小宇宙) episode. It shells out to the separate [`xyz` CLI](https://github.com/lima1217/xiaoyuzhou), so it needs that tool installed and logged in; it does not use the LLM API.

```bash
uv tool install git+https://github.com/lima1217/xiaoyuzhou   # provides `xyz`
xyz send-code <phone>            # then, with the SMS code:
xyz login <phone> <code>
```

1. Paste an episode link (`https://www.xiaoyuzhoufm.com/episode/<id>`) and click **抓取字幕**.
2. Preview or copy it, or download **Markdown** (paragraphs with `[mm:ss]` start times) or a timestamped **TXT**.

- **设置** shows whether `xyz` was found and links to the setup instructions; the tab shows a banner when it is missing.
- Transcripts live in `~/.local/share/epub-zh/podcasts/` (next to `jobs/`).
- The server finds `xyz` on `PATH`, then in `~/.local/bin`; set `EPUB_ZH_XYZ=/path/to/xyz` to override.
- Episodes without an official 文稿 report “还没有官方字幕”.

### YouTube captions

The **YouTube 字幕** tab saves a video's captions with [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api) (installed with epub-zh). It does not use the LLM API.

1. Paste a video link (`youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/…`) and pick a language:
   - **原始字幕**: the first manually made track, else the auto-generated one.
   - **英文** / **中文**: that language (manual before auto-generated); if the video has none, YouTube's own machine translation. YouTube often refuses translated captions — then pick 原始字幕.
2. Preview, copy, or download Markdown / timestamped TXT, as for podcasts.

- Captions live in `~/.local/share/epub-zh/youtube/`, one file per video and language.
- If YouTube can't be reached or reports the IP as blocked, set a proxy in **设置** (`youtube_proxy`, e.g. `http://127.0.0.1:7890`). It applies to the next fetch, no restart.
- Upgrading an existing install: the new dependency needs one `uv tool install --reinstall ./epub-zh` (add `-e` for an editable install); 设置 shows whether it is installed.

## Command line

```bash
# Chinese only (default mode)
epub-zh translate book.epub -o book_zh.epub

# English paragraph followed by its Chinese translation
epub-zh translate book.epub -o book_bi.epub --mode bilingual

# Leave bibliography and index in English
epub-zh translate book.epub -o book_zh.epub --skip-refs

# Preview: per-file block counts, bibliography/index files, estimated API calls.
# No API calls, nothing written.
epub-zh translate book.epub --dry-run

# Continue after a failure or interruption
epub-zh resume .epub-zh-state/book_zh/state.json

# One-line progress of a running job: running / complete / failed
epub-zh status .epub-zh-state/book_zh/state.json
```

Progress is kept in `.epub-zh-state/<output-name>/` next to the output file. The source EPUB is never modified.

## Configuration

`epub-zh config init` writes `~/.config/epub-zh/config.toml`:

```toml
api_key = "sk-..."
base_url = "https://api.deepseek.com/v1"   # default: DeepSeek official
model = "deepseek-flash"                   # default
concurrency = 8                            # API calls in flight, 1–32
batch_size = 40                            # max paragraphs per API call, 1–40
batch_chars = 3000                         # target characters per API call
```

| Command | Does |
|---------|------|
| `epub-zh config show` | Resolved values and where each came from (key redacted) |
| `epub-zh config set <name> <value>` | Change one field, keeping the rest of the file |
| `epub-zh config check` | Verify the key and that `model` is served |
| `epub-zh config init --local` | Per-directory override in `./.epub-zh.toml` |

**Precedence:** CLI flags > environment (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`) > `./.epub-zh.toml` > `~/.config/epub-zh/config.toml` > defaults.

To use another provider, set its `base_url`, `api_key`, and `model`; any OpenAI-compatible chat completions API works.

### Speed

Time is almost all spent waiting for the model, so `concurrency` is the main knob. Measured with DeepSeek on two chapters (260K characters, 102 requests):

| concurrency | time |
|-------------|------|
| one at a time (old behaviour) | ~8 min, estimated from the old rate |
| 8 (default) | 60 s |
| 32 | 23 s |
| 64 | 29 s — each request slowed from ~5 s to ~10 s |

For DeepSeek, `epub-zh config set concurrency 32`. If your provider returns 429 (rate limited), lower it. Before calling the API, epub-zh skips paragraphs with no English letters, URLs, and exact duplicates, and packs short paragraphs (such as index entries) together.

## How it works

1. Unpack the EPUB and collect text blocks (paragraphs, headings, list items) from each chapter file in reading order.
2. Send blocks to the model in numbered batches, several batches at once; malformed replies are retried (up to 6 attempts with backoff), and a batch the model keeps mis-numbering is split in half.
3. Write each chapter file as soon as all its batches are back, and record it in `state.json`.
4. Set the Chinese title, a `-zh` identifier suffix, and `zh-CN` language (Chinese-only mode); repack as a new EPUB.

Notes:

- Foreign personal names and proper nouns stay in English inside the Chinese text.
- In Chinese-only mode, inline formatting inside a translated paragraph (italics, links) is replaced by plain text.
- The book title is the model's translation and may differ from an official Chinese edition's title.

## Development

```bash
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
python -m pytest -q
```

CI runs the tests on every push to `main`. Notes for coding agents: [`AGENTS.md`](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE).
