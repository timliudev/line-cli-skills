#!/usr/bin/env python3
"""Fail when behavior-affecting files change without a matching docs change.

Usage: check_docs_sync.py BASE HEAD [--labels a,b,c]

Code paths (plugins/**, .claude-plugin/**) require at least one docs path
(CHANGELOG.md, README*.md, docs/GUIDE*.md) in the same diff, unless the PR carries
the `no-docs` label. Stdlib only.
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys

CODE_GLOBS = ["plugins/*", ".claude-plugin/*"]
DOC_GLOBS = ["CHANGELOG.md", "README*.md", "docs/GUIDE*.md"]
BYPASS_LABEL = "no-docs"


def _matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def check(changed: list[str], labels: list[str] | None = None) -> tuple[bool, str]:
    """Return (ok, message) for a list of changed repo-relative paths."""
    changed = [p.replace("\\", "/") for p in changed]
    code = [p for p in changed if _matches(p, CODE_GLOBS)]
    docs = [p for p in changed if _matches(p, DOC_GLOBS)]
    if not code:
        return True, "No plugin/marketplace changes; nothing to check."
    if docs:
        return True, f"Docs touched alongside code: {', '.join(docs)}"
    if BYPASS_LABEL in (labels or []):
        return True, f"Skipped: PR has the `{BYPASS_LABEL}` label."
    return False, (
        "Plugin/marketplace files changed but no docs were updated. Update "
        "CHANGELOG.md (Unreleased), README*.md and docs/GUIDE*.md (all four languages) "
        f"in the same PR, or add the `{BYPASS_LABEL}` label.\nChanged: "
        + ", ".join(code)
    )


def changed_files(base: str, head: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base")
    ap.add_argument("head")
    ap.add_argument("--labels", default="", help="comma-separated PR labels")
    args = ap.parse_args(argv)
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    ok, msg = check(changed_files(args.base, args.head), labels)
    print(msg, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
