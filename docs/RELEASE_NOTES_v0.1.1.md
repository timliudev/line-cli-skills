# line-cli-skills v0.1.1

Documentation correction release. No script changes.

## Changed

- Sending is verified working. On 2026-10-13 (Windows, line-cli v0.4.0) a text message sent through
  the skill (`lineq.py run --yes -- send <id> --stdin --json`) to a direct chat was delivered,
  end-to-end encrypted.
- The earlier "sending is blocked by an upstream bug" claim was too broad. The TalkException 86 /
  error 10051 failure only affects some peers whose Letter Sealing key is the old version
  (`e2eeVersion` 0). Upstream does not fall back and shows a generic error; nothing is delivered, and
  the skill tells you so and suggests sending from the LINE app.
- New in SKILL.md: a "Replying to a specific message" flow (sync, find the target in the local archive,
  confirmation shows the quoted original). Any message type can be the target, a reply can be a file,
  and the latest-100 limit does not apply to replies. Known limit: @mentions are not supported (sent as
  plain text, no highlight or notification). `--reply-to` uses the same send path as plain send but was
  not separately verified live.

## Still untested live

- Reacting and unsending.
- `--reply-to` on its own.
- macOS and Linux (unit tests only).
