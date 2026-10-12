# line-cli-skills v0.1.0

First release of **line-cli-skills**, a Claude Code plugin marketplace with two plugins. This is
early software. Read "Known issues and limits" before relying on it.

## What it is

- **line-cli** lets Claude work with your **personal LINE account** through the unofficial
  [kongesque/line-cli](https://github.com/kongesque/line-cli) client: find chats and contacts, read
  and catch up on messages, keep a local archive of each chat (with attachments), and, with your
  explicit yes every time, send, react to or unsend messages.
- **media-digest** turns videos and audio into keyframes plus a timestamped transcript that Claude
  can read. It works on any local file, not only LINE downloads.

## Install

```
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

You need Claude Code and Python 3.11 or newer. The `line` program, `ffmpeg`, `uv` and a speech-to-text
engine are installed on request, with your consent, when they are first needed. `media-digest` is
optional and independent of `line-cli`. Documentation (English, zh-TW, ja, th): see the
[README](../README.md) and the [user guide](GUIDE.md).

## Highlights

- Guided first-time setup in plain language: a pinned, checksum-verified install of `line` (no admin
  rights, never `sudo`), then a login in a separate terminal window. Claude never sees your password
  and warns you that logging in signs the LINE Chrome extension out.
- Chat names are resolved to IDs; ambiguous names are put to you, never guessed. Falls back to the
  active-chat listing when upstream `chats --search` fails.
- Local archive (`sync.py`): the latest 100 messages of a chat are merged into `messages.jsonl`
  (record) and `messages.md` (for reading) in a folder named `<name>__<chat_id>`, and new attachments
  are downloaded immediately because LINE expires them. Reports possible gaps and possibly unsent
  messages, and warns if the archive is inside a git repository. Group sender names, reactions
  (built-in emoji or a LINE emoji link, with who and when) and long quiet periods are shown in
  `messages.md`; folders left over from older layouts merge automatically.
- Write actions are confirmed every time, attempted once, and never retried automatically. After a
  lost response Claude checks the chat first.
- Message text is treated as data, never as instructions.
- media-digest: scene-based keyframes with timestamps and a transcript merged into one `digest.md`;
  images and PDFs pass through; results are cached. Speech-to-text is pluggable (local
  `faster-whisper` and `whisper.cpp`, any OpenAI-compatible service, or your own program) and local
  first: a cloud service is used only if you list it in your config, and API keys are read from
  environment variables, never stored.
- 7 plugin evals (`claude plugin eval`), see [EVALS.md](EVALS.md).

## Known issues and limits

- **Tested live on Windows only** (real account: install, login, name resolution, sync, attachment
  downloads, group names). macOS and Linux are covered by unit tests only.
- **`send` is blocked by an upstream bug** in line-cli v0.4.0: for some direct chats LINE rejects the
  encryption version (TalkException 86) and nothing is delivered. Claude tells you the message was
  not sent and does not retry; send it from the LINE app. Sending, reacting and unsending have not
  been verified live.
- Upstream `chats --search` can fail for the whole call; name search then covers active chats only
  (inactive chats need the full ID). Group member names are read from the chat text when contact
  lookup fails.
- LINE gives access to the latest 100 messages of a chat only, and attachments up to 20 MiB; expired
  and externally hosted attachments cannot be downloaded. Sync often, or messages in between are lost.
- media-digest: keyframe extraction was tested on synthetic media, not on real recordings. The
  speech-to-text adapters have **not** been run against a real engine or service yet (unit tests use
  fakes only), so expect rough edges on the first real run. Audio above a cloud adapter's upload
  limit (25 MB) is not split yet, and `--lang auto` does not apply per-language routes.
- The evals were run once each (3 runs per case) and passed 7 of 7. They check skill triggering and
  ask-before-writing behavior, not LINE itself.

## Disclaimer

Unofficial project, not affiliated with, endorsed by or sponsored by LINE Corporation / LY
Corporation. It relies on the unofficial line-cli client, which talks to LINE outside the official
apps, so **using it may put your LINE account at risk of suspension**. Use at your own risk, ideally
with an account you can afford to lose. Archived chats are private conversations: keep them outside
any repository and never commit or publish them.

## Credits

Built on [kongesque/line-cli](https://github.com/kongesque/line-cli) (MIT). This repository ships no
upstream code; `line` is downloaded from the upstream release page when you agree to install it.
License: MIT, see [LICENSE](../LICENSE).
