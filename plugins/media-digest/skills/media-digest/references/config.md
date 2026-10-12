# media-digest configuration

Applies to media-digest as implemented in this repo (`mdcore/config.py`, `mdcore/asr.py`).
Config is optional: without it, speech-to-text (ASR) tries `faster_whisper`, then `whisper_cpp`,
both local.

## Contents

- Location
- Minimal examples
- Fallback order and language routes
- Adapter tables
- Presets for cloud and self-hosted services
- API keys (environment variables only)
- Validation and errors
- Other settings (environment variables)

## Location

`config.toml` in the first of these that applies:

1. `$MEDIA_DIGEST_CONFIG` (a full file path)
2. Windows: `%APPDATA%\media-digest\config.toml`
3. macOS and Linux: `$XDG_CONFIG_HOME/media-digest/config.toml`, or `~/.config/media-digest/config.toml`

A missing file is fine. A file with invalid TOML, unknown keys or wrong types is an error: ASR
returns `config: ...` with the reason and the digest falls back to frames only. Fix the file and
re-run. Check with `cd "${CLAUDE_SKILL_DIR}/scripts" && python -m mdcore.asr list`.

## Minimal examples

Local only, a smaller and faster model:

```toml
[adapters.faster_whisper]
model = "small"
```

Local first, then Groq as a fallback (the user must have said yes to cloud use):

```toml
[asr]
order = ["faster_whisper", "groq"]
```

## Fallback order and language routes

```toml
[asr]
order = ["faster_whisper", "whisper_cpp", "groq"]   # default chain

[asr.routes]            # per language; replaces `order` for that language
zh = ["my-qwen3", "faster_whisper"]
ja = ["faster_whisper"]
```

- Adapters run in order. The first one that exits 0 with usable output wins. Exit 2
  (environment not ready: missing dependency, key or model) and exit 1 (tried and failed) both move
  on to the next adapter; both are listed in `attempts`.
- The chain for a language is `[asr.routes]` entry: exact code, then the base language
  (`zh-TW` uses `zh`), else `[asr] order`. A route **replaces** `order`, it is not appended: put
  your fallbacks inside the route. Use the key `auto` for `--lang auto`.
- With `--lang auto` the language is not detected first, so `zh`/`ja` routes do not apply. Pass
  `--lang zh` to `digest.py` to use a route.
- Every name used in `order` or `routes` must be a built-in (`faster_whisper`, `whisper_cpp`), a
  preset name (`openai`, `groq`, `vllm`, `speaches`) or have an `[adapters.<name>]` table.
- **Privacy rule:** a cloud adapter is only ever used when it is listed in `order` or `routes`. An
  `[adapters.groq]` table that nobody lists is never run.

## Adapter tables

`[adapters.<name>]`. The `type` is the name itself for built-ins; otherwise set it.

```toml
[adapters.faster_whisper]      # optional tuning of the built-in
model = "large-v3-turbo"       # alias, Hugging Face repo id, or a local CT2 model directory
device = "auto"                # auto | cpu | cuda
compute_type = "auto"          # auto = float16 on CUDA, int8 on CPU

[adapters.whisper_cpp]
model_path = "~/models/ggml-large-v3-turbo.bin"   # required (ggml .bin file)
binary = "whisper-cli"
threads = 8

[adapters.groq]                # cloud: used only because it is listed in `order`
preset = "groq"                # fills base_url / model / api_key_env; any key can be overridden
# model = "whisper-large-v3-turbo"
# response_format = "verbose_json"   # json | verbose_json | text
# language_field = "language"        # language | languages | none
# max_mb = 25                        # upload limit before the adapter gives up
# extra = { temperature = "0" }      # extra form fields
# timeout = 3600                     # seconds for the whole adapter run

[adapters.my-qwen3]            # any program that follows the adapter contract
type = "command"
cmd = ["uv", "run", "~/asr/qwen3_adapter.py", "--audio", "{audio}", "--lang", "{lang}"]
# model = "..."                       # value for {model}
# probe_cmd = ["uv", "run", "~/asr/qwen3_adapter.py", "--probe"]
# install_hint = "..."                # shown when `cmd` cannot be found
```

Allowed keys per type (anything else is rejected):

| type | keys |
| --- | --- |
| all | `type`, `preset`, `timeout`, `model` |
| `faster_whisper` | `device`, `compute_type` |
| `whisper_cpp` | `model_path`, `binary`, `threads` |
| `openai_compat` | `base_url`, `api_key_env`, `response_format`, `language_field`, `extra`, `max_mb` |
| `command` | `cmd`, `probe_cmd`, `install_hint` |

`cmd` placeholders: `{audio}`, `{lang}`, `{prompt}`, `{model}`. A leading `~` in an argument is
expanded; an unset prompt or model becomes an empty string.

A Breeze-ASR-25 style community CT2 conversion for Chinese is just a `model` value (repo id or
local directory) on `faster_whisper`; it is never selected automatically.

## Presets for cloud and self-hosted services

Verified against vendor docs on 2026-10-12. Model names change often: they are defaults, so
override `model` when needed.

| preset | base_url | default model | key variable |
| --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | `gpt-transcribe` (also `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`) | `OPENAI_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `whisper-large-v3-turbo` (also `whisper-large-v3`) | `GROQ_API_KEY` |
| `vllm` | `http://localhost:8000/v1` | `openai/whisper-large-v3-turbo` (set `model` to what you serve) | none |
| `speaches` | `http://localhost:8000/v1` | `Systran/faster-whisper-large-v3` | none |

An adapter whose `base_url` is not localhost counts as **cloud** (`cloud: true` in
`python -m mdcore.asr list`). `command` adapters are never flagged, even if the program talks to a
remote service: that is for the user to judge.

Upload limit is 25 MB for OpenAI and for Groq's free tier (Groq dev tier: 100 MB, set
`max_mb = 100`). Bigger audio is not split automatically yet; the adapter fails with `too_large`
and the next adapter in the chain runs.

## API keys (environment variables only)

Store the **name** of the variable, never the key:

```toml
[adapters.groq]
preset = "groq"
api_key_env = "GROQ_API_KEY"     # already the preset default
```

A literal `api_key = "..."` is rejected. The adapter reads the variable itself, so the secret never
appears on a command line or in the config. The user exports it in the shell that launches Claude
Code, for example:

- macOS/Linux: `export GROQ_API_KEY=...` (in the shell profile)
- Windows PowerShell: `setx GROQ_API_KEY "..."` then open a new terminal

Do not ask the user to paste a key into the chat.

## Validation and errors

`python -m mdcore.asr list` shows config errors as a row named `(config)` with the reason. Typical
messages: unknown key for the adapter type, unknown preset, name used in `order`/`routes` without a
table, `cmd` not a list, non-positive `timeout`/`max_mb`/`threads`.

## Other settings (environment variables)

| Variable | Effect |
| --- | --- |
| `MEDIA_DIGEST_CONFIG` | Full path to the config file |
| `MEDIA_DIGEST_FFMPEG_DIR` | Extra folder to look for `ffmpeg` and `ffprobe` |
| `HF_HOME`, `HF_HUB_CACHE` | Where the faster-whisper model cache is looked up and stored |
