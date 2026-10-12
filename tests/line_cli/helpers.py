"""Helpers shared by the line-cli tests."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "line-cli" / "skills" / "line-cli" / "scripts"
FAKE = Path(__file__).with_name("fake_line.py")
IS_WINDOWS = sys.platform.startswith("win")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    """Import scripts/<name>.py under a private module name (avoids `setup` clashes)."""
    spec = importlib.util.spec_from_file_location(f"linecli_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeLine:
    """Installs a fake `line` into <tmp>/bin and records how it was called."""

    def __init__(self, bin_dir: Path, work: Path, monkeypatch):
        self.bin_dir = bin_dir
        self.scenario_path = work / "scenario.json"
        self.monkeypatch = monkeypatch
        self.path: Path | None = None

    def install(self, directory: Path | None = None, name: str | None = None) -> Path:
        directory = directory or self.bin_dir
        directory.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            target = directory / (name or "line.cmd")
            target.write_text(
                f'@echo off\r\n"{sys.executable}" "{FAKE}" %*\r\nexit /b %errorlevel%\r\n',
                encoding="utf-8",
            )
        else:
            target = directory / (name or "line")
            target.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8"
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.path = target
        return target

    def scenario(self, rules: list[dict]) -> FakeLine:
        self.scenario_path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
        self.monkeypatch.setenv("FAKE_LINE_SCENARIO", str(self.scenario_path))
        for suffix in (".log", ".state"):
            Path(str(self.scenario_path) + suffix).unlink(missing_ok=True)
        return self

    def calls(self) -> list[dict]:
        log = Path(str(self.scenario_path) + ".log")
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def argv_calls(self) -> list[list[str]]:
        return [c["args"] for c in self.calls()]


def rule(args=None, *, prefix=None, exit=0, stdout="", stderr="", responses=None, sleep=0):
    """Build a scenario rule. `responses` is a list of {exit, stdout, stderr} dicts."""
    out: dict = {}
    if args is not None:
        out["args"] = list(args)
    if prefix is not None:
        out["prefix"] = list(prefix)
    if responses is not None:
        out["responses"] = responses
    else:
        out.update({"exit": exit, "stdout": stdout, "stderr": stderr, "sleep": sleep})
    return out


def resp(exit=0, stdout="", stderr=""):
    return {"exit": exit, "stdout": stdout, "stderr": stderr}


# Handy canned outputs --------------------------------------------------------

BUSY = resp(75, "", "line: another line command is using the session; retry when it finishes")
NOT_LOGGED_IN = resp(1, "", "line: no saved LINE session; run line login")
WHOAMI_OK = {"mid": "u" + "a" * 32, "displayName": "Tim", "userid": "tim"}
UID = "U" + "A" * 43
GID = "C" + "B" * 43
