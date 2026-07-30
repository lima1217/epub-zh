# epub-zh CLI reference

Disclosed reference for the [epub-zh-translate](SKILL.md) skill. Load when choosing flags, configuring the LLM API, reading state, or recovering a failed job.

## Install / PATH

`zsh: command not found: epub-zh` → package not on `PATH`. Do not run `config` / `translate` until fixed.

| Mode | Commands |
|------|----------|
| Dev venv | `cd <repo> && source .venv/bin/activate && uv pip install -e .` |
| Global-ish | `uv tool install -e <repo>` then ensure `$HOME/.local/bin` is on `PATH` |

Gate: `epub-zh version` prints a version string.

Full agent install + config narrative: repo root [`AGENTS.md`](../../../AGENTS.md).

## LLM API configuration

### Files

| File | Purpose |
|------|---------|
| `~/.config/epub-zh/config.toml` | User defaults (or `$XDG_CONFIG_HOME/epub-zh/config.toml`) |
| `./.epub-zh.toml` | Optional cwd override (gitignored) |

### Flow (agent checklist)

1. Ensure `epub-zh` is on `PATH` (see above).
2. If no key yet: `epub-zh config init` → tell user the path from `epub-zh config path` → user edits `api_key` (and optional `base_url` / `model`).
3. `epub-zh config show` — confirm `api_key` is set (redacted) and note the `[source]` column.
4. Proceed to *dry-run* / *pipeline*. Prefer config file over asking for a key in chat.

### TOML fields

```toml
api_key = "sk-..."
base_url = "https://api.openai.com/v1"   # optional
model = "gpt-4o-mini"                    # optional
batch_size = 8                           # optional, 1..40
```

OpenAI-compatible providers: set that vendor’s `base_url`, `api_key`, and `model`.

### Precedence (high → low)

CLI flags → `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` → `.epub-zh.toml` → user `config.toml` → defaults (`gpt-4o-mini`, batch 8).

### Config subcommands

| Subcommand | Role |
|------------|------|
| `init` | Write user config (`--local` → `.epub-zh.toml`; `--force` overwrites) |
| `show` | Resolved settings + source (`api_key` redacted) |
| `path` | Print path (`--local` for project file) |

`init` sets file mode `0600` when the OS allows.

### Secrets

Never commit config files with keys. Never echo full keys in chat/PR/issue text. Key is not stored in `state.json`; *resume* must resolve it again.

## Commands

### `epub-zh translate`

| Argument / flag | Role |
|-----------------|------|
| `SOURCE` | Input EPUB (required; DRM-free, text-based) |
| `-o` / `--output` | Output EPUB path — required unless `--dry-run` |
| `-m` / `--mode` | `zh` (default, Chinese only) or `bilingual` (EN block, then ZH sibling) |
| `--model` | Override model (else env / config / `gpt-4o-mini`) |
| `--base-url` | Override API base (else env / config / OpenAI default) |
| `--api-key` | Override key (else env / config) |
| `--batch-size` | Blocks per API call (1–40; else config / 8) |
| `--dry-run` | Parse + count blocks only; no API, no state dir, no output |

### `epub-zh resume`

| Argument / flag | Role |
|-----------------|------|
| `STATE` | Path to `state.json` from a prior run |
| `--api-key` | Same as translate |

Resume reloads `source`, `output`, `mode`, `model`, `base_url`, `batch_size`, `workdir`, and `completed_docs` from state. API key comes from config / env / flag only.

### `epub-zh version`

Print package version.

## State layout

For output `/path/to/book_zh.epub`:

```
/path/to/.epub-zh-state/book_zh/
  state.json
  work-<hash>/          # unpacked EPUB working tree
```

`state.json` fields the agent cares about: `completed_docs`, `failed_doc`, `error`, `workdir`, `output`, `mode`.

*dry-run* never creates this tree.

## Failure recovery

1. Read `failed_doc` / `error` from `state.json`.
2. Confirm `workdir` still exists (missing workdir → cannot *resume*; user must start a fresh *pipeline* to the same `-o` or a new output).
3. Fix transient cause (key, network, quota) if needed — `epub-zh config show` for key/source.
4. `epub-zh resume <state.json>` — skips docs already in `completed_docs`.

API layer retries rate limits, timeouts, 5xx, and unparseable numbered model output (up to 6 attempts with backoff) before failing the doc.

## Constraints (positive)

- Produce a **new** EPUB via `-o`; leave the source file unchanged.
- Prefer *dry-run* before first paid *pipeline* on an unfamiliar book.
- Prefer *resume* over re-running *translate* when state + workdir exist.
- Prefer config file for API credentials over chat paste or shell-only `export`.
- Expect `zh` mode to flatten inline markup inside a replaced block to plain text.
- Require text-based, DRM-free EPUB input (not scan-image books).
