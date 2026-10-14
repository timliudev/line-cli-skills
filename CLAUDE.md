# Repo rules for AI agents and contributors

Claude Code plugin marketplace (`line-cli-skills`) with two plugins: `line-cli` and `media-digest`.
Internal planning doc: `docs/PLAN.md` (zh-TW, do not translate or rewrite casually).

## Docs never drift from code (hard rule)

Every commit that changes behavior must update the user docs **in the same commit**:

- Behavior = anything under `plugins/**/scripts`, `SKILL.md`, `references/`, or the config format.
- Update the README and `docs/GUIDE` in **all four languages** (en, zh-TW, ja, th):
  `README.md`, `README.zh-TW.md`, `README.ja.md`, `README.th.md`,
  `docs/GUIDE.md`, `docs/GUIDE.zh-TW.md`, `docs/GUIDE.ja.md`, `docs/GUIDE.th.md`.
- Add an entry under `CHANGELOG.md` -> `Unreleased`.
- Keep section structure identical across languages (tests enforce equal `##` counts).
- CI job `docs-sync` fails PRs touching `plugins/**` or `.claude-plugin/**` with no docs change
  (label `no-docs` bypasses it for genuinely doc-irrelevant changes).

## Conventions

- Commit author email: `27921307+timliudev@users.noreply.github.com`. Never any other email.
- SKILL.md and `references/` are written in English.
- Skills must stay agent-neutral (open Agent Skills format): never write `${CLAUDE_SKILL_DIR}` in commands, use `<SKILL_DIR>/scripts/...` (the one `SKILL_DIR:` line per SKILL.md is the only place the variable appears; `tests/test_skill_portability.py` enforces it), and name Claude Code tools only as a parenthetical ("Claude Code: Read").
- Python is stdlib-only, `requires-python >=3.11`; ruff + pytest must pass.
- Never put private chat data (archives, `*.digest/`, screenshots of chats) in the repo.
- Never commit secrets; config stores environment variable names only.
- Write actions toward LINE (send/react/unsend) always need explicit user confirmation, never auto-retry.
- Verify with: `python -m pytest -q`, `ruff check .`, `claude plugin validate . --strict`.
