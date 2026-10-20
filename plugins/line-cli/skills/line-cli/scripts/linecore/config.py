"""Locations and user configuration for the line-cli skill (stdlib only).

Precedence for the archive directory, highest first:
  1. environment variable ``LINE_SKILL_ARCHIVE``
  2. ``archive_root`` in ``config.toml``
  3. per-OS default
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The line-cli release this skill's references/ were written against, and the tag the
# installer is pinned to. Bump both together after re-checking docs/CLI.md upstream.
LINE_CLI_VERSION = "v0.5.0"
LINE_CLI_REPO = "kongesque/line-cli"

APP_DIRNAME = "line-skill"
CONFIG_FILENAME = "config.toml"
ARCHIVE_ENV = "LINE_SKILL_ARCHIVE"

DOWNLOAD_TYPES = ("image", "video", "audio", "file")
DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # 20971520, upstream's attachment cap
KNOWN_KEYS = {"line_path", "archive_root", "download", "render", "watch"}
KNOWN_RENDER_KEYS = {"quiet_gap_days"}
DEFAULT_QUIET_GAP_DAYS = 3
KNOWN_DOWNLOAD_KEYS = {"types", "max_bytes", "line_emoji"}
KNOWN_WATCH_KEYS = {"vip_chats", "archive_chats"}


class ConfigError(ValueError):
    """config.toml exists but cannot be used."""


def current_os() -> str:
    """Return ``"windows"``, ``"macos"`` or ``"linux"`` (any other Unix counts as linux)."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _home(env: Mapping[str, str], os_name: str) -> Path:
    key = "USERPROFILE" if os_name == "windows" else "HOME"
    value = env.get(key)
    return Path(value) if value else Path.home()


def _abs_env_dir(env: Mapping[str, str], key: str) -> Path | None:
    """XDG variables only count when set to an absolute path."""
    value = env.get(key)
    if value and os.path.isabs(value):
        return Path(value)
    return None


def config_dir(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Path:
    """Directory that holds ``config.toml`` (shared name with media-digest's sibling dir)."""
    env, os_name = _env(env), os_name or current_os()
    if os_name == "windows":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else _home(env, os_name) / "AppData" / "Roaming"
    else:
        base = _abs_env_dir(env, "XDG_CONFIG_HOME") or _home(env, os_name) / ".config"
    return base / APP_DIRNAME


def config_path(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Path:
    return config_dir(env, os_name) / CONFIG_FILENAME


def default_archive_dir(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Path:
    env, os_name = _env(env), os_name or current_os()
    home = _home(env, os_name)
    if os_name == "windows":
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / APP_DIRNAME / "archive"
    if os_name == "macos":
        return home / "Library" / "Application Support" / APP_DIRNAME / "archive"
    base = _abs_env_dir(env, "XDG_DATA_HOME") or home / ".local" / "share"
    return base / APP_DIRNAME / "archive"


@dataclass(frozen=True)
class Config:
    line_path: str | None
    archive_root: Path
    archive_source: str  # "env" | "config" | "default"
    download_types: tuple[str, ...]
    download_max_bytes: int
    config_path: Path
    config_exists: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    render_quiet_gap_days: int = DEFAULT_QUIET_GAP_DAYS
    download_line_emoji: bool = True
    watch_vip_chats: tuple[str, ...] = ()  # chat names or full IDs; never secrets
    watch_archive_chats: tuple[str, ...] = ()  # chats watch.py may archive (besides archived ones)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "config_exists": self.config_exists,
            "line_path": self.line_path,
            "archive_root": str(self.archive_root),
            "archive_source": self.archive_source,
            "download": {
                "types": list(self.download_types),
                "max_bytes": self.download_max_bytes,
                "line_emoji": self.download_line_emoji,
            },
            "render": {"quiet_gap_days": self.render_quiet_gap_days},
            "watch": {
                "vip_chats": list(self.watch_vip_chats),
                "archive_chats": list(self.watch_archive_chats),
            },
            "warnings": list(self.warnings),
        }


def _expand(value: str, base: Path | None) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute() and base is not None:
        path = base / path
    return path


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _str_key(raw: Mapping[str, Any], key: str, where: Path) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: `{key}` must be a non-empty string")
    return value.strip()


def load_config(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Config:
    """Load config.toml (absent file means defaults) and apply the env override.

    Raises ConfigError for unreadable/invalid files so callers can report it; scripts
    that must keep working should catch it and fall back to ``defaults()``.
    """
    env, os_name = _env(env), os_name or current_os()
    cfg_path = config_path(env, os_name)
    exists = cfg_path.is_file()
    raw: dict[str, Any] = _read_toml(cfg_path) if exists else {}
    warnings: list[str] = []

    for key in sorted(set(raw) - KNOWN_KEYS):
        warnings.append(f"unknown key `{key}` in {cfg_path} (ignored)")

    line_path = _str_key(raw, "line_path", cfg_path)

    download = raw.get("download", {})
    if not isinstance(download, dict):
        raise ConfigError(f"{cfg_path}: `download` must be a table")
    for key in sorted(set(download) - KNOWN_DOWNLOAD_KEYS):
        warnings.append(f"unknown key `download.{key}` in {cfg_path} (ignored)")

    types_raw = download.get("types", list(DOWNLOAD_TYPES))
    if not isinstance(types_raw, list) or not all(isinstance(t, str) for t in types_raw):
        raise ConfigError(f"{cfg_path}: `download.types` must be a list of strings")
    bad = [t for t in types_raw if t not in DOWNLOAD_TYPES]
    if bad:
        allowed = ", ".join(DOWNLOAD_TYPES)
        raise ConfigError(f"{cfg_path}: unknown download type(s) {bad}; allowed: {allowed}")
    types = tuple(dict.fromkeys(types_raw))  # de-duplicate, keep order

    max_bytes = download.get("max_bytes", DEFAULT_MAX_BYTES)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ConfigError(f"{cfg_path}: `download.max_bytes` must be a positive integer")

    line_emoji = download.get("line_emoji", True)
    if not isinstance(line_emoji, bool):
        raise ConfigError(f"{cfg_path}: `download.line_emoji` must be true or false")

    render = raw.get("render", {})
    if not isinstance(render, dict):
        raise ConfigError(f"{cfg_path}: `render` must be a table")
    for key in sorted(set(render) - KNOWN_RENDER_KEYS):
        warnings.append(f"unknown key `render.{key}` in {cfg_path} (ignored)")
    quiet = render.get("quiet_gap_days", DEFAULT_QUIET_GAP_DAYS)
    if isinstance(quiet, bool) or not isinstance(quiet, int) or quiet < 0:
        raise ConfigError(f"{cfg_path}: `render.quiet_gap_days` must be an integer >= 0")

    watch = raw.get("watch", {})
    if not isinstance(watch, dict):
        raise ConfigError(f"{cfg_path}: `watch` must be a table")
    for key in sorted(set(watch) - KNOWN_WATCH_KEYS):
        warnings.append(f"unknown key `watch.{key}` in {cfg_path} (ignored)")
    chat_lists: dict[str, tuple[str, ...]] = {}
    for key in ("vip_chats", "archive_chats"):
        raw_list = watch.get(key, [])
        if not isinstance(raw_list, list) or not all(
            isinstance(v, str) and v.strip() for v in raw_list
        ):
            raise ConfigError(f"{cfg_path}: `watch.{key}` must be a list of non-empty strings")
        chat_lists[key] = tuple(dict.fromkeys(v.strip() for v in raw_list))  # de-duplicate

    env_archive = (env.get(ARCHIVE_ENV) or "").strip()
    cfg_archive = _str_key(raw, "archive_root", cfg_path)
    if env_archive:
        archive, source = _expand(env_archive, Path.cwd()), "env"
    elif cfg_archive:
        archive, source = _expand(cfg_archive, cfg_path.parent), "config"
    else:
        archive, source = default_archive_dir(env, os_name), "default"

    return Config(
        line_path=line_path,
        archive_root=archive,
        archive_source=source,
        download_types=types,
        download_max_bytes=max_bytes,
        config_path=cfg_path,
        config_exists=exists,
        warnings=tuple(warnings),
        render_quiet_gap_days=quiet,
        download_line_emoji=line_emoji,
        watch_vip_chats=chat_lists["vip_chats"],
        watch_archive_chats=chat_lists["archive_chats"],
    )


def defaults(env: Mapping[str, str] | None = None, os_name: str | None = None) -> Config:
    """Config with no file applied (used when config.toml is broken)."""
    env, os_name = _env(env), os_name or current_os()
    env_archive = (env.get(ARCHIVE_ENV) or "").strip()
    if env_archive:
        archive, source = _expand(env_archive, Path.cwd()), "env"
    else:
        archive, source = default_archive_dir(env, os_name), "default"
    return Config(
        line_path=None,
        archive_root=archive,
        archive_source=source,
        download_types=DOWNLOAD_TYPES,
        download_max_bytes=DEFAULT_MAX_BYTES,
        config_path=config_path(env, os_name),
        config_exists=False,
    )


def load_config_safe(
    env: Mapping[str, str] | None = None, os_name: str | None = None
) -> tuple[Config, str | None]:
    """Like load_config but never raises: returns (config, error_message_or_None)."""
    try:
        return load_config(env, os_name), None
    except ConfigError as exc:
        return defaults(env, os_name), str(exc)
