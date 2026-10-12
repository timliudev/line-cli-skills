# Contributing

## The docs rule

Every change that alters behavior (`plugins/**/scripts`, `SKILL.md`, `references/`, config
format) must update the user documentation **in the same commit/PR**:

- `README.md`, `README.zh-TW.md`, `README.ja.md`, `README.th.md`
- `docs/GUIDE.md`, `docs/GUIDE.zh-TW.md`, `docs/GUIDE.ja.md`, `docs/GUIDE.th.md`
- `CHANGELOG.md` under `Unreleased`

Keep the `##` section structure identical across languages. CI (`docs-sync` job and
`tests/test_docs.py`) enforces this. If a change truly needs no docs, a maintainer can add the
`no-docs` label to the PR.

## Conventions

- Git author email: `27921307+timliudev@users.noreply.github.com`.
- SKILL.md and references are English. Python is stdlib-only, 3.11+.
- Never commit private chat data, archives, `*.digest/` output or secrets.

## Definition of done

- [ ] `python -m pytest -q` passes
- [ ] `ruff check .` passes
- [ ] `claude plugin validate . --strict` passes (and each `plugins/*` dir)
- [ ] README + GUIDE updated in all four languages (en, zh-TW, ja, th)
- [ ] CHANGELOG `Unreleased` entry added
- [ ] `references/commands.md` version note updated if the supported line-cli version changed
- [ ] No private data, secrets or foreign emails in the diff
