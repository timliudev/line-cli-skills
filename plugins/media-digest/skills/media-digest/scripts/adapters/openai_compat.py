#!/usr/bin/env python3
"""ASR adapter: any OpenAI-compatible /audio/transcriptions endpoint (stdlib urllib only).

Works with OpenAI, Groq, vLLM, Speaches and similar servers.

Usage:  openai_compat.py --audio PATH --lang CODE|auto [--prompt TEXT] [--model NAME]
            --base-url URL [--api-key-env ENV_NAME] [--response-format json|verbose_json|text]
            [--language-field language|languages|none] [--max-mb 25] [--extra key=value ...]
        openai_compat.py --probe --base-url URL [--api-key-env ENV_NAME]

The API key is read from the environment variable *named* by --api-key-env; it is never taken
from, nor echoed to, the command line. Files above --max-mb exit 2 with reason "too_large"
(the caller is expected to compress or split the audio and retry).
Exit codes: 0 ok, 2 environment unavailable (missing key, unreachable, 401/403, too large),
1 failure.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_MODEL = "whisper-1"
DEFAULT_MAX_MB = 25.0  # OpenAI and Groq free tier; counted in decimal MB to stay on the safe side
REQUEST_TIMEOUT = 900

_LANG_NAMES = {
    "english": "en",
    "chinese": "zh",
    "mandarin": "zh",
    "cantonese": "yue",
    "japanese": "ja",
    "korean": "ko",
    "thai": "th",
    "vietnamese": "vi",
    "indonesian": "id",
    "malay": "ms",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "arabic": "ar",
    "hindi": "hi",
    "turkish": "tr",
    "dutch": "nl",
    "polish": "pl",
    "ukrainian": "uk",
}


def emit(obj: dict) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


def base_lang(lang: str) -> str | None:
    lang = lang.strip().lower().replace("_", "-")
    return None if lang in ("", "auto") else lang.split("-")[0]


def normalise_language(value: str | None) -> str | None:
    if not value:
        return None
    return _LANG_NAMES.get(value.strip().lower(), value.strip().lower())


def default_response_format(model: str) -> str:
    """verbose_json (timestamps) for whisper-family models, plain json for the rest."""
    return "verbose_json" if "whisper" in model.lower() else "json"


def language_field(model: str, override: str | None) -> str:
    if override:
        return override
    return "languages" if model.lower().startswith("gpt-transcribe") else "language"


def build_multipart(fields: list[tuple[str, str]], file_field: str, path: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(f"{head}{value}\r\n".encode())
    filename = Path(path).name.replace('"', "_")
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
    )
    parts.append(Path(path).read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def parse_response(body: str) -> dict:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:  # response_format=text
        text = body.strip()
        return {
            "text": text,
            "language": None,
            "segments": [{"start": 0.0, "end": 0.0, "text": text}] if text else [],
        }
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    text = str(data.get("text", "")).strip()
    lang = data.get("language")
    if not isinstance(lang, str):
        langs = data.get("languages")  # gpt-transcribe: [{"code": "en"}, ...]
        first = langs[0] if isinstance(langs, list) and langs else None
        lang = first.get("code") if isinstance(first, dict) else first
    segments = []
    for seg in data.get("segments") or []:
        if isinstance(seg, dict) and str(seg.get("text", "")).strip():
            segments.append(
                {
                    "start": round(float(seg.get("start") or 0), 3),
                    "end": round(float(seg.get("end") or 0), 3),
                    "text": str(seg["text"]).strip(),
                }
            )
    if not segments and text:
        segments = [{"start": 0.0, "end": round(float(data.get("duration") or 0), 3), "text": text}]
    return {
        "text": text,
        "language": normalise_language(lang if isinstance(lang, str) else None),
        "segments": segments,
    }


def probe(args: argparse.Namespace) -> int:
    hint = (
        "Set base_url (or a preset: openai, groq, vllm, speaches) in the adapter table of "
        "config.toml and export the API key in the environment variable named by "
        "`api_key_env`."
    )
    if not args.base_url:
        emit(
            {
                "available": False,
                "reason": "no_base_url: base_url is not configured",
                "install_hint": hint,
            }
        )
    elif args.api_key_env and not os.environ.get(args.api_key_env):
        emit(
            {
                "available": False,
                "reason": f"missing_env: environment variable {args.api_key_env} is not set",
                "install_hint": f"Export {args.api_key_env} in the shell that launches Claude Code "
                f"(never put the key in config.toml).",
                "cloud": not is_local(args.base_url),
            }
        )
    else:
        cloud = not is_local(args.base_url)
        where = "cloud endpoint: audio leaves this machine" if cloud else "local endpoint"
        emit(
            {
                "available": True,
                "reason": f"{args.base_url} ({where}; not contacted by probe)",
                "install_hint": None,
                "cloud": cloud,
            }
        )
    return 0


def post(args: argparse.Namespace, fields: list[tuple[str, str]], key: str | None):
    body, ctype = build_multipart(fields, "file", args.audio)
    url = args.base_url.rstrip("/") + "/audio/transcriptions"
    headers = {"Content-Type": ctype, "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=args.timeout) as resp:  # noqa: S310 (user-set URL)
        return resp.read().decode("utf-8", errors="replace")


def run(args: argparse.Namespace) -> int:
    if not args.base_url:
        print("no_base_url: base_url is not configured", file=sys.stderr)
        return 2
    key = None
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key:
            print(
                f"missing_env: environment variable {args.api_key_env} is not set", file=sys.stderr
            )
            return 2
    if not Path(args.audio).is_file():
        print(f"audio file not found: {args.audio}", file=sys.stderr)
        return 1
    size = Path(args.audio).stat().st_size
    if size > args.max_mb * 1_000_000:
        print(
            f"too_large: {size / 1e6:.1f} MB exceeds the {args.max_mb:g} MB upload limit; "
            "compress or split the audio",
            file=sys.stderr,
        )
        return 2

    model = args.model or DEFAULT_MODEL
    explicit_format = args.response_format
    fmt = explicit_format or default_response_format(model)
    lang = base_lang(args.lang)

    def make_fields(response_format: str) -> list[tuple[str, str]]:
        fields = [("model", model), ("response_format", response_format)]
        field = language_field(model, args.language_field)
        if lang and field == "language":
            fields.append(("language", lang))
        elif lang and field == "languages":
            fields.append(("languages[]", lang))
        if args.prompt:
            fields.append(("prompt", args.prompt))
        for item in args.extra or []:
            k, _, v = item.partition("=")
            fields.append((k, v))
        return fields

    try:
        try:
            body = post(args, make_fields(fmt), key)
        except urllib.error.HTTPError as exc:
            # The server may not support verbose_json for this model; retry once with json.
            if exc.code in (400, 422) and not explicit_format and fmt == "verbose_json":
                body = post(args, make_fields("json"), key)
            else:
                raise
        result = parse_response(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip().replace("\n", " ")[:200]
        if exc.code in (401, 403):
            print(
                f"auth_failed: HTTP {exc.code} (check the key in {args.api_key_env})",
                file=sys.stderr,
            )
            return 2
        if exc.code == 413:
            print("too_large: server rejected the upload (HTTP 413)", file=sys.stderr)
            return 2
        print(f"http_error: HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"unreachable: {exc.reason}", file=sys.stderr)
        return 2
    except (TimeoutError, OSError, ValueError) as exc:
        print(f"request_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    emit(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenAI-compatible ASR adapter")
    ap.add_argument("--audio")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--prompt")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key-env")
    ap.add_argument("--response-format")
    ap.add_argument("--language-field", choices=["language", "languages", "none"])
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB)
    ap.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT)
    ap.add_argument("--extra", action="append", metavar="KEY=VALUE")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args(argv)
    if args.probe:
        return probe(args)
    if not args.audio:
        ap.error("--audio is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
