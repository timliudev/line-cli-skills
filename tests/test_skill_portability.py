"""Skills must work in any Agent Skills-compatible agent, not only Claude Code.

Only Claude Code substitutes ${CLAUDE_SKILL_DIR}, and only in the SKILL.md body. So the variable
appears exactly once per skill (the `SKILL_DIR:` definition line in SKILL.md) and every command
uses the portable `<SKILL_DIR>` placeholder instead.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ["line-cli", "media-digest"]
VAR = "${CLAUDE_SKILL_DIR}"


def skill_dir(name: str) -> Path:
    return ROOT / "plugins" / name / "skills" / name


def md_files(name: str):
    d = skill_dir(name)
    return [d / "SKILL.md", *sorted((d / "references").glob("*.md"))]


@pytest.mark.parametrize("name", SKILLS)
def test_skill_dir_defined_once_in_skill_md(name):
    text = (skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")
    assert text.count(VAR) == 1
    assert re.search(r"^SKILL_DIR: \$\{CLAUDE_SKILL_DIR\}$", text, re.M)
    assert "<SKILL_DIR>" in text


@pytest.mark.parametrize("name", SKILLS)
def test_no_bare_variable_elsewhere(name):
    for f in md_files(name):
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line == f"SKILL_DIR: {VAR}" and f.name == "SKILL.md":
                continue
            assert VAR not in line, f"{f.relative_to(ROOT)}: use <SKILL_DIR>: {line}"
            assert "CLAUDE_SKILL_DIR" not in line, f"{f.relative_to(ROOT)}: {line}"


@pytest.mark.parametrize("name", SKILLS)
def test_referenced_scripts_exist(name):
    d = skill_dir(name)
    found = set()
    for f in md_files(name):
        text = f.read_text(encoding="utf-8")
        found |= set(re.findall(r"<SKILL_DIR>/(scripts/[\w./-]+\.py)", text))
    for rel in found:
        assert (d / rel).is_file(), f"{rel} referenced but missing in {name}"
    if name == "line-cli":
        assert {"scripts/setup.py", "scripts/lineq.py", "scripts/sync.py"} <= found
    else:
        assert "scripts/digest.py" in found


@pytest.mark.parametrize("name", SKILLS)
def test_commands_use_portable_form(name):
    """Every `scripts/x.py` mention in a command must be prefixed with <SKILL_DIR>/."""
    for f in md_files(name):
        for line in f.read_text(encoding="utf-8").splitlines():
            if re.search(r"\bpython3?\b.*\bscripts/\w+\.py", line):
                assert re.search(r'python3?\s+"<SKILL_DIR>/scripts/', line), (
                    f"{f.relative_to(ROOT)}: {line}"
                )


@pytest.mark.parametrize("name", SKILLS)
def test_no_claude_only_wording_in_description(name):
    text = (skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")
    desc = re.search(r"^description: (.*)$", text, re.M).group(1)
    assert "Claude" not in desc
    assert len(desc) <= 1024
