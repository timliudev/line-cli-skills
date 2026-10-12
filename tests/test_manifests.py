"""Structural checks for the marketplace and both plugins."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NOREPLY = "27921307+timliudev@users.noreply.github.com"
PLUGINS = ["line-cli", "media-digest"]
ALLOWED_TOP = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def frontmatter(path: Path) -> dict:
    """Top-level (unindented) frontmatter keys only."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert m, f"{path} has no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        if re.match(r"^[A-Za-z_-]+:", line):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def test_marketplace():
    mp = load(ROOT / ".claude-plugin" / "marketplace.json")
    assert mp["name"] == "line-cli-skills"
    assert mp["owner"]["name"]
    assert sorted(p["name"] for p in mp["plugins"]) == sorted(PLUGINS)
    for p in mp["plugins"]:
        assert p["source"] == f"./plugins/{p['name']}"
        assert (ROOT / p["source"]).is_dir()


@pytest.mark.parametrize("name", PLUGINS)
def test_plugin_manifest(name):
    pj = load(ROOT / "plugins" / name / ".claude-plugin" / "plugin.json")
    assert pj["name"] == name
    assert pj["author"]["email"] == NOREPLY
    assert pj["license"] == "MIT"
    assert pj["version"]


@pytest.mark.parametrize("name", PLUGINS)
def test_skill_frontmatter(name):
    skill = ROOT / "plugins" / name / "skills" / name / "SKILL.md"
    fm = frontmatter(skill)
    assert fm["name"] == name == skill.parent.name
    assert fm["description"]
    assert len(fm["description"]) <= 1024
    assert set(fm) <= ALLOWED_TOP


def test_no_foreign_emails():
    for f in ROOT.rglob("*.json"):
        if {".git", ".venv", "node_modules"} & set(f.parts):
            continue
        for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", f.read_text(encoding="utf-8")):
            assert email == NOREPLY
