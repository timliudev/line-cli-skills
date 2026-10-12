"""Docs must exist in all four languages, carry the disclaimer, and keep the same structure."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_docs_sync  # noqa: E402

SUFFIXES = ["", ".zh-TW", ".ja", ".th"]
READMES = [ROOT / f"README{s}.md" for s in SUFFIXES]
GUIDES = [ROOT / "docs" / f"GUIDE{s}.md" for s in SUFFIXES]
ALL = READMES + GUIDES


def h2_count(path: Path) -> int:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    return len(re.findall(r"^## ", text, flags=re.M))


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_exists_with_disclaimer_anchor(path):
    assert path.is_file(), f"missing {path}"
    assert '<a id="disclaimer"></a>' in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_language_switcher(path):
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:5])
    for suf in SUFFIXES:
        stem = "README" if path in READMES else "GUIDE"
        if (stem == "README") == (path in READMES) and path.name == f"{stem}{suf}.md":
            continue  # current language is plain text, not a link
        assert f"({stem}{suf}.md)" in head


@pytest.mark.parametrize("group", [READMES, GUIDES], ids=["README", "GUIDE"])
def test_structure_parity(group):
    counts = {p.name: h2_count(p) for p in group}
    assert len(set(counts.values())) == 1, f"## heading counts differ: {counts}"


@pytest.mark.parametrize("name", ["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"])
def test_contributor_docs_exist(name):
    assert (ROOT / name).is_file()


def test_readme_disclaimer_text_mentions_key_points():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ["Unofficial", "suspension", "LINE Corporation", "never commit"]:
        assert needle.lower() in text.lower()


# ---- tools/check_docs_sync.py ----
def test_sync_no_code_changes_ok():
    assert check_docs_sync.check(["tests/test_x.py"])[0]


def test_sync_code_without_docs_fails():
    ok, msg = check_docs_sync.check(["plugins/line-cli/skills/line-cli/SKILL.md"])
    assert not ok and "no-docs" in msg


def test_sync_code_with_docs_ok():
    assert check_docs_sync.check(["plugins/x/a.py", "CHANGELOG.md"])[0]
    assert check_docs_sync.check([".claude-plugin/marketplace.json", "docs/GUIDE.ja.md"])[0]


def test_sync_label_bypass():
    assert check_docs_sync.check(["plugins/x/a.py"], ["no-docs"])[0]
    assert not check_docs_sync.check(["plugins/x/a.py"], ["bug"])[0]


def test_sync_windows_paths():
    assert not check_docs_sync.check(["plugins\\x\\a.py"])[0]
