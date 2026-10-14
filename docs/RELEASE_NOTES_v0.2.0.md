# line-cli-skills v0.2.0

Broadcast and live watch, and the skills now work in other agents.

## Highlights

- **Broadcast** (`broadcast.py`): send one message to a list of chats. Dry run by default; sending
  needs the dry run's `plan` hash and an explicit yes, goes out one by one, stops at the first
  failure and never retries.
- **Live archive** (`watch.py`): keeps the archive current in the background, only for chats you
  already archived or listed, and can show VIP notifications (Windows toast, no message text in
  logs). Plain text messages need no `line` call, so `send` is rarely blocked.
- **Other agents**: both skills follow the open Agent Skills format and install with
  `npx skills add timliudev/line-cli-skills` (Codex, Gemini CLI, Cursor, Copilot, OpenCode...).
  Commands now use a portable `<SKILL_DIR>/scripts/...` path instead of a Claude Code-only variable,
  and instructions no longer name Claude Code tools. Claude Code plugin and user-skill installs work
  as before.
- **Install docs**: README and guide (4 languages) explain Option C, global vs project scope,
  `npx skills update`, and the `/plugin` vs `claude plugin` difference.

## Known limits

- Do not install a skill both as a Claude Code plugin and with `npx skills`: Claude Code would see
  two copies.
- Outside Claude Code the skills are only tested by structure (paths, frontmatter), not run inside
  each agent. If an agent does not fill in the skill folder, tell it where the skill lives.
- Still untested live: reacting, unsending, `--reply-to` on its own, macOS and
  Linux.
- `line` and its upstream limits are unchanged: unofficial client, only the latest 100 messages per
  chat, some peers cannot be sent to, no @mentions.
