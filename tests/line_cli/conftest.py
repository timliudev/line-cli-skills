"""Shared fixtures: isolated env, a fake `line` on PATH, script loader."""

from __future__ import annotations

import pytest

from .helpers import FakeLine

ENV_TO_CLEAR = (
    "LINE_SKILL_ARCHIVE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "SSH_CONNECTION",
    "SSH_TTY",
    "FAKE_LINE_SCENARIO",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No real home, config, archive, or `line` binary can leak into a test."""
    home = tmp_path / "home"
    (tmp_path / "bin").mkdir()
    for sub in ("local", "roaming"):
        (tmp_path / sub).mkdir()
    home.mkdir()
    for key in ENV_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    return tmp_path


@pytest.fixture
def fake_line(isolated_env, monkeypatch):
    fake = FakeLine(isolated_env / "bin", isolated_env, monkeypatch)
    fake.install()
    fake.scenario([])
    return fake
