# ASR adapters

An adapter is a small program that turns one audio file into text. media-digest ships three
built-in adapters, and any program that follows the contract below can be plugged in as a
`command` adapter. Configuration is in [config.md](config.md); installing the engines is in
[install.md](install.md).

## Contents

- Built-in adapters
- The adapter contract
- Check what works
- Writing a custom adapter
- Example: a task-queue style service

## Built-in adapters

| name | where it runs | notes |
| --- | --- | --- |
| `faster_whisper` | local | Runs through `uv run --script` (dependency `faster-whisper>=1.2.1` is installed on demand; falls back to the current Python if `uv` is missing). Default model `large-v3-turbo`, about 1.6 GB downloaded once. Voice-activity filter on. |
| `whisper_cpp` | local | Calls `whisper-cli`. Needs `model_path` (a ggml `.bin`). Input must be 16 kHz 16-bit WAV, which the digest pipeline produces; anything else exits 2 with `needs_16khz_wav`. |
| `openai_compat` | cloud or self-hosted | Standard-library multipart POST to `<base_url>/audio/transcriptions`. Presets: `openai`, `groq`, `vllm`, `speaches` (see [config.md](config.md)). |
| `command` | anything | Your own program, from a `cmd` list with `{audio} {lang} {prompt} {model}` placeholders. |

`openai_compat` details:

- `response_format` defaults to `verbose_json` for models whose name contains `whisper` (gives
  timestamps) and `json` otherwise (`gpt-transcribe`, `gpt-4o-*`). If the server rejects an
  implicit `verbose_json` with 400/422 it retries once with `json`. An explicitly configured format
  is never retried.
- Language: nothing is sent for `auto`. `gpt-transcribe*` models get `languages[]=<code>` (OpenAI
  replaced `language` with `languages` for them); everything else gets `language=<code>`. The exact
  request encoding of `languages[]` has not been verified against a real key yet; use
  `language_field = "none"` to send neither.
- Size: above `max_mb` (default 25, decimal MB) it exits 2 with `too_large` and uploads nothing;
  a server 413 maps to the same code. Splitting or compressing long audio before upload is not
  wired in yet, so the next adapter in the chain takes over.
- Exit mapping: missing key variable 2; HTTP 401/403 2 (`auth_failed`); connection refused 2
  (`unreachable`); other HTTP errors and malformed responses 1.

## The adapter contract

```
<adapter> --audio PATH --lang CODE|auto [--prompt TEXT] [--model NAME] [config flags...]
  stdout  {"text": "...", "language": "zh", "segments": [{"start": 0.0, "end": 3.2, "text": "..."}]}
  stderr  diagnostics; the LAST non-empty line becomes `reason` in the attempt log
  exit    0 ok | 2 environment unavailable (missing dependency, key or model; unreachable; too large)
          | 1 failed
<adapter> --probe  ->  stdout {"available": bool, "reason": "...", "install_hint": "..."}
```

Conventions:

- Start the last stderr line with a short code (`too_large:`, `missing_env:`, `not_found:`,
  `needs_16khz_wav:`, `unreachable:`) so people can act on it.
- `--lang` is `auto` or an ISO 639-1 style code. It arrives unchanged (`zh-TW` is possible), so
  reduce it to the base language yourself.
- Segment times are seconds (float). If the service has no timestamps, return one segment (0.0 to
  0.0 is fine). For `command` adapters, plain text on stdout (not JSON) is accepted and becomes one
  segment.
- `--probe` must be cheap: no heavy imports, no network calls.
- Exit 2 means "not set up here, try the next adapter"; exit 1 means "tried and broke". Both let the
  chain continue, and both are recorded.
- Never take secrets as command-line values. Read them from an environment variable.
- Built-in adapters receive config values as flags: `faster_whisper` `--device --compute-type`;
  `whisper_cpp` `--model-path --binary --threads`; `openai_compat` `--base-url --api-key-env
  --response-format --language-field --max-mb --extra K=V`.

## Check what works

```
cd "${CLAUDE_SKILL_DIR}/scripts"
python -m mdcore.asr list                       # probes every adapter, nothing is downloaded
python -m mdcore.asr run AUDIO.wav --lang zh    # run the chain on one file
```

`list` rows: `name`, `type`, `available`, `reason`, `install_hint`, `cloud`; `faster_whisper` rows
also have `model_cached` and `download_size`. `run` returns `ok`, `adapter`, `text`, `language`,
`segments`, `attempts` (each tried adapter with `exit_code` and `reason`) and `error`.

## Writing a custom adapter

Any executable that follows the contract works. Register it as a `command` adapter. Python scripts
run through `uv run` with PEP 723 metadata keep their dependencies out of the user's environment.
Minimal skeleton:

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
import argparse
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--audio")
ap.add_argument("--lang", default="auto")
ap.add_argument("--prompt")
ap.add_argument("--model")
ap.add_argument("--probe", action="store_true")
a = ap.parse_args()

if a.probe:
    ok = bool(os.environ.get("MY_ASR_TOKEN"))
    print(json.dumps({"available": ok, "reason": "ready" if ok else "MY_ASR_TOKEN not set",
                      "install_hint": None if ok else "export MY_ASR_TOKEN=..."}))
    sys.exit(0)
if not os.environ.get("MY_ASR_TOKEN"):
    print("missing_env: MY_ASR_TOKEN is not set", file=sys.stderr)
    sys.exit(2)  # environment problem -> the dispatcher tries the next adapter
try:
    text = my_transcribe(a.audio, a.lang, a.prompt)  # your code
except Exception as exc:
    print(f"failed: {exc}", file=sys.stderr)
    sys.exit(1)
print(json.dumps({"text": text, "language": None,
                  "segments": [{"start": 0.0, "end": 0.0, "text": text}]}))
```

Register it:

```toml
[adapters.my-asr]
type = "command"
cmd = ["uv", "run", "~/asr/my_asr.py", "--audio", "{audio}", "--lang", "{lang}"]
probe_cmd = ["uv", "run", "~/asr/my_asr.py", "--probe"]
```

and list `"my-asr"` in `order` or in a language route.

## Example: a task-queue style service

Hypothetical service, documentation only (it is not shipped as an adapter). Typical for a
self-hosted ASR server that queues jobs:

- `POST {base}/v2/asr` with multipart `file`, `language`, `context` returns `{"task_id": "..."}`
- `GET {base}/v2/tasks/{id}` until `status` is `completed` or `success` (failure: `failed`, `error`)
- the result is at `result.segments[{text,start,end}]`
- auth: `Authorization: Bearer <token>` from an environment variable

Save as `~/asr/queue_asr.py`:

```python
#!/usr/bin/env python3
"""media-digest adapter for a task-queue ASR service (stdlib only)."""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("QUEUE_ASR_URL", "http://asr.internal:8080").rstrip("/")
TOKEN_ENV = "QUEUE_ASR_TOKEN"
POLL_SECONDS, MAX_WAIT = 2.0, 3600


def call(req, timeout=60):
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def submit(audio, lang, prompt, token):
    boundary = uuid.uuid4().hex
    fields = {"language": "" if lang == "auto" else lang, "context": prompt or ""}
    body = b""
    for k, v in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
    name = Path(audio).name
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
             f"Content-Type: {ctype}\r\n\r\n").encode()
    body += Path(audio).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/v2/asr", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return call(req)["task_id"]


def wait(task_id, token):
    deadline = time.monotonic() + MAX_WAIT
    while time.monotonic() < deadline:
        req = urllib.request.Request(f"{BASE}/v2/tasks/{task_id}",
                                     headers={"Authorization": f"Bearer {token}"})
        data = call(req)
        status = str(data.get("status", "")).lower()
        if status in ("completed", "success"):
            return data["result"]
        if status in ("failed", "error"):
            raise RuntimeError(f"task {task_id} {status}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"task {task_id} still running after {MAX_WAIT}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--prompt")
    ap.add_argument("--model")  # unused by this service
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    token = os.environ.get(TOKEN_ENV)
    if a.probe:
        ok = bool(token)
        print(json.dumps({"available": ok,
                          "reason": "token present" if ok else f"missing_env: {TOKEN_ENV}",
                          "install_hint": None if ok else f"export {TOKEN_ENV}=..."}))
        return 0
    if not token:
        print(f"missing_env: {TOKEN_ENV} is not set", file=sys.stderr)
        return 2
    lang = "auto" if a.lang == "auto" else a.lang.split("-")[0].lower()
    try:
        result = wait(submit(a.audio, lang, a.prompt, token), token)
    except urllib.error.URLError as exc:
        print(f"unreachable: {exc.reason}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    segs = [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
             "text": str(s.get("text", "")).strip()} for s in result.get("segments", [])]
    print(json.dumps({"text": " ".join(s["text"] for s in segs),
                      "language": result.get("language"), "segments": segs}))
    # json.dumps escapes non-ASCII by default, which is safe on any console encoding
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Wire it in `config.toml` (route Chinese to it first, keep a local fallback):

```toml
[asr]
order = ["faster_whisper"]

[asr.routes]
zh = ["my-queue", "faster_whisper"]

[adapters.my-queue]
type = "command"
cmd = ["uv", "run", "~/asr/queue_asr.py", "--audio", "{audio}", "--lang", "{lang}", "--prompt", "{prompt}"]
probe_cmd = ["uv", "run", "~/asr/queue_asr.py", "--probe"]
timeout = 3600
```

Export `QUEUE_ASR_TOKEN` (and `QUEUE_ASR_URL`) in the environment, never in `config.toml`. Whether
a self-hosted or internal service counts as "cloud" for the privacy notice is the user's call:
`command` adapters are never flagged `cloud`, so tell the user where the audio goes.
