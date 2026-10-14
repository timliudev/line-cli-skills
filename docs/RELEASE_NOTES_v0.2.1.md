# line-cli-skills v0.2.1

Fixes from a live install on a second computer (via `npx skills add`).

### Fixed
- `login.py`: the login window no longer stays open after "Signed in as ...". On success (exit 0)
  it prints "Login complete" and closes after 3 seconds; on failure or cancel it stays open with
  the error and waits for Enter. Applies to the Windows (PowerShell, no more `-NoExit`), macOS
  Terminal and Linux terminal launchers.

### Changed
- One device at a time is now documented (README known limits, GUIDE troubleshooting row in all four
  languages, `references/setup.md`, `references/errors.md`). The stderr "LINE session was
  invalidated or replaced by another client" is classified as `session_replaced` (not retryable,
  fatal for watch/sync, `setup.py --check` reports `next_step: session_replaced`), and SKILL.md
  tells the agent to explain it plainly and ask before logging in again, since that signs the other
  device out.
- `setup.py --install` returns a `verification` object (`installer_script_sha256`,
  `release_archive`) stating exactly what was checked, and SKILL.md has the agent report it after
  install. Homebrew installs are reported as not pinned or verified by this skill.

