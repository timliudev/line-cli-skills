"""Read and validate media-digest's config.toml (stdlib only, Python >= 3.11).

Normalised shape returned by :func:`load_config`::

    {
      "path": "<config path>", "exists": bool,
      "order": ["faster_whisper", ...],            # default fallback chain
      "routes": {"zh": ["my-qwen3", ...], ...},    # per-language chains ("auto" allowed)
      "adapters": {"name": {"type": ..., ...}},    # every adapter that can be used
    }

Privacy rule: the default chain contains local adapters only. A cloud adapter (``openai_compat``
pointing at a non-local host) is only ever used when the user listed it in ``order`` or in
``[asr.routes]`` -- nothing in this module adds one on its own.
"""

from __future__ import annotations

import os
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BUILTIN_TYPES = ("faster_whisper", "whisper_cpp", "openai_compat")
ADAPTER_TYPES = (*BUILTIN_TYPES, "command")

# Local-only chain used when there is no config file (or no [asr] order).
DEFAULT_ORDER = ["faster_whisper", "whisper_cpp"]

# Presets for openai_compat. Verified 2026-10-12 against the vendors' docs; model names change
# often, so they are only defaults -- override ``model`` in your own config when needed.
PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-transcribe",
        "api_key_env": "OPENAI_API_KEY",
        "response_format": "json",
    },
    "groq": {
        "type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "whisper-large-v3-turbo",
        "api_key_env": "GROQ_API_KEY",
        "response_format": "verbose_json",
    },
    "vllm": {
        "type": "openai_compat",
        "base_url": "http://localhost:8000/v1",
        "model": "openai/whisper-large-v3-turbo",
        "response_format": "json",
    },
    "speaches": {
        "type": "openai_compat",
        "base_url": "http://localhost:8000/v1",
        "model": "Systran/faster-whisper-large-v3",
        "response_format": "verbose_json",
    },
}

_COMMON_KEYS = {"type", "preset", "timeout", "model"}
_ALLOWED_KEYS: dict[str, set[str]] = {
    "faster_whisper": _COMMON_KEYS | {"device", "compute_type"},
    "whisper_cpp": _COMMON_KEYS | {"model_path", "binary", "threads"},
    "openai_compat": _COMMON_KEYS
    | {"base_url", "api_key_env", "response_format", "language_field", "extra", "max_mb"},
    "command": _COMMON_KEYS | {"cmd", "probe_cmd", "install_hint"},
}


class ConfigError(ValueError):
    """The config file is unreadable or invalid. The message is safe to show to the user."""


def config_path() -> Path:
    """MEDIA_DIGEST_CONFIG, else the per-OS default location of config.toml."""
    override = os.environ.get("MEDIA_DIGEST_CONFIG")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "media-digest" / "config.toml"


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


def is_cloud(spec: dict[str, Any]) -> bool:
    """True when audio sent to this adapter leaves the machine (openai_compat, non-local URL)."""
    return spec.get("type") == "openai_compat" and not is_local_url(str(spec.get("base_url", "")))


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where} must be a list of non-empty strings")
    return list(value)


def _check_adapter(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    spec = deepcopy(raw)
    preset = spec.get("preset")
    if preset is not None:
        if preset not in PRESETS:
            raise ConfigError(
                f"adapter '{name}': unknown preset '{preset}' (known: {', '.join(PRESETS)})"
            )
        spec = {**PRESETS[preset], **{k: v for k, v in spec.items() if k != "preset"}}
    spec.setdefault("type", name if name in BUILTIN_TYPES else None)
    typ = spec.get("type")
    if typ not in ADAPTER_TYPES:
        raise ConfigError(
            f"adapter '{name}': 'type' must be one of {', '.join(ADAPTER_TYPES)} (got {typ!r})"
        )
    unknown = set(spec) - _ALLOWED_KEYS[typ]
    if unknown:
        raise ConfigError(
            f"adapter '{name}' ({typ}): unknown key(s) {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(sorted(_ALLOWED_KEYS[typ]))}"
        )
    for key in (
        "model",
        "device",
        "compute_type",
        "model_path",
        "binary",
        "base_url",
        "api_key_env",
        "response_format",
        "language_field",
        "install_hint",
    ):
        if key in spec and not isinstance(spec[key], str):
            raise ConfigError(f"adapter '{name}': '{key}' must be a string")
    for key in ("timeout", "max_mb", "threads"):
        if key in spec and (
            isinstance(spec[key], bool) or not isinstance(spec[key], int | float) or spec[key] <= 0
        ):
            raise ConfigError(f"adapter '{name}': '{key}' must be a positive number")
    if typ == "openai_compat":
        if not spec.get("base_url"):
            raise ConfigError(f"adapter '{name}': openai_compat needs 'base_url' (or a 'preset')")
        if spec.get("language_field", "language") not in ("language", "languages", "none"):
            raise ConfigError(
                f"adapter '{name}': 'language_field' must be language, languages or none"
            )
        extra = spec.get("extra", {})
        if not isinstance(extra, dict) or not all(
            isinstance(k, str) and isinstance(v, str | int | float | bool) for k, v in extra.items()
        ):
            raise ConfigError(f"adapter '{name}': 'extra' must be a table of scalar values")
    if typ == "command":
        if "cmd" not in spec:
            raise ConfigError(f"adapter '{name}': command adapter needs 'cmd' (a list)")
        spec["cmd"] = _str_list(spec["cmd"], f"adapter '{name}': cmd")
        if "probe_cmd" in spec:
            spec["probe_cmd"] = _str_list(spec["probe_cmd"], f"adapter '{name}': probe_cmd")
    return spec


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load and validate the config. A missing file is fine (local-only defaults apply)."""
    p = Path(path) if path is not None else config_path()
    data: dict[str, Any] = {}
    exists = p.is_file()
    if exists:
        try:
            with p.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{p}: invalid TOML: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"{p}: cannot read: {exc}") from exc

    asr = data.get("asr", {})
    if not isinstance(asr, dict):
        raise ConfigError("[asr] must be a table")
    raw_adapters = data.get("adapters", {})
    if not isinstance(raw_adapters, dict) or not all(
        isinstance(v, dict) for v in raw_adapters.values()
    ):
        raise ConfigError("[adapters.<name>] entries must be tables")

    order = _str_list(asr["order"], "[asr] order") if "order" in asr else list(DEFAULT_ORDER)
    raw_routes = asr.get("routes", {})
    if not isinstance(raw_routes, dict):
        raise ConfigError("[asr.routes] must be a table")
    routes = {
        str(lang).lower(): _str_list(chain, f"[asr.routes] {lang}")
        for lang, chain in raw_routes.items()
    }

    adapters: dict[str, dict[str, Any]] = {}
    for builtin in ("faster_whisper", "whisper_cpp"):  # local built-ins are always known
        adapters[builtin] = {"type": builtin}
    for name, raw in raw_adapters.items():
        adapters[name] = _check_adapter(name, raw)

    # A name in order/routes that has no [adapters.<name>] table may still be a built-in or a
    # preset (e.g. order = ["groq"]). Listing it there is the explicit opt-in for cloud use.
    wanted = [*order, *(n for chain in routes.values() for n in chain)]
    for name in wanted:
        if name in adapters:
            continue
        if name in PRESETS:
            adapters[name] = _check_adapter(name, {"preset": name})
        else:
            raise ConfigError(
                f"'{name}' is used in [asr] order/routes but no [adapters.{name}] is defined"
            )

    return {
        "path": str(p),
        "exists": exists,
        "order": order,
        "routes": routes,
        "adapters": adapters,
    }


def route_for(cfg: dict[str, Any], lang: str) -> list[str]:
    """Adapter chain for a language: exact route, then base language (zh-TW -> zh), else order."""
    key = (lang or "auto").strip().lower().replace("_", "-")
    routes = cfg["routes"]
    for candidate in (key, key.split("-")[0]):
        if candidate in routes:
            return list(routes[candidate])
    return list(cfg["order"])
