"""Every eval case has a prompt.md, at least one grader, and valid grader frontmatter."""
import re
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"
CASES = sorted(p.parent for p in PLUGINS.glob("*/evals/*/prompt.md"))
TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}
EXPECTED = {
    "line-cli": {"trigger-line-read", "no-trigger-oa", "safety-send-confirm",
                 "safety-unsend-confirm", "safety-no-retry", "login-warning"},
    "media-digest": {"trigger-media"},
}


def fm(path: Path) -> str:
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", path.read_text(encoding="utf-8"), re.S)
    assert m, f"{path} has no frontmatter"
    return m.group(1)


def test_expected_cases_exist():
    for plugin, names in EXPECTED.items():
        have = {c.name for c in CASES if c.parent.parent.name == plugin}
        assert names <= have, f"{plugin} missing {names - have}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.parent.parent.name}/{c.name}")
def test_case(case: Path):
    text = fm(case / "prompt.md")
    assert "plugins:" not in text, "cases live inside the plugin; it is the default target"
    assert (case.parent.parent / ".claude-plugin" / "plugin.json").is_file()
    body = (case / "prompt.md").read_text(encoding="utf-8").split("---", 2)[2]
    assert body.strip()
    graders = sorted((case / "graders").glob("*.md"))
    assert graders, "no graders"
    for g in graders:
        t = re.search(r"^type:\s*(\S+)", fm(g), re.M)
        assert t and t.group(1) in TYPES, f"{g} bad type"
