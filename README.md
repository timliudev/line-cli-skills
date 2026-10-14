# line-cli-skills: Claude Code skills for LINE and media attachments

English | [繁體中文（台灣）](README.zh-TW.md) | [日本語](README.ja.md) | [ภาษาไทย](README.th.md)

Claude Code skills for working with a **personal LINE account** and the media people send you. Status: **v0.1.2 (early software)** - reading, local sync/archive and sending a text message were tested live on Windows, media digestion on synthetic media; sending fails only for some peers with old Letter Sealing keys. Details under [Status](#status).

<a id="disclaimer"></a>

## Disclaimer

- **Unofficial project.** It is not affiliated with, endorsed by, or sponsored by LINE Corporation / LY Corporation. "LINE" is a trademark of its owner and is used here only to describe what the software works with.
- It relies on the **unofficial** [kongesque/line-cli](https://github.com/kongesque/line-cli) client, which talks to LINE outside the official apps. **Using it may put your LINE account at risk of suspension.** Use at your own risk, preferably with an account you can afford to lose.
- Archived chats are **private conversations**. Keep the archive outside this repository and **never commit or publish it**.

## What is in this repo

Two independent Claude Code plugins:

- **line-cli** - read and search your **personal LINE** chats, groups and contacts, download attachments, and (with explicit confirmation every time) send, react to or unsend messages, through the [kongesque/line-cli](https://github.com/kongesque/line-cli) client.
  - Guided first-time setup in plain language: installs `line` for you after you agree (pinned release, checksum-verified, no admin rights), then walks you through login.
  - Safe by design: Claude shows the chat, the exact text and the action and waits for your "yes". Writes are never retried automatically.
  - Chat names are resolved to IDs; ambiguous names are put to you, never guessed.
  - Local archive: `sync.py` saves each chat's messages (a `.jsonl` record plus a readable `.md`) in a folder on your machine and downloads new attachments right away, so Claude can tell you what is new and your history is kept from the day you start syncing, not just LINE's latest 100 messages.
  - Who replied or reacted: `thread.py` lists the replies and reactions to one archived message, and `emoji.py` keeps short labels for the LINE emoji reactions that sync caches once per emoji (shared across chats).
  - Broadcast, live archive and VIP alerts: `broadcast.py` sends one message to many chats (dry run first, sends only after your yes, stops at the first failure, never retries); `watch.py` keeps the archive of the chats you chose live in the background (other chats are ignored), catches up after missed events and shows a Windows notification for new messages in your VIP chats; a sync now reports a permanent `gap` when more than 100 messages arrived between two syncs.
- **media-digest** - turn videos and audio into keyframes plus a timestamped transcript Claude can read. Works with any local file, not only LINE.
  - Videos: keyframes by scene detection (with timestamps) and a transcript, merged into one `digest.md` timeline.
  - Audio and voice messages: transcript with timestamps.
  - Pluggable speech-to-text: local `faster-whisper` and `whisper.cpp`, any OpenAI-compatible service (OpenAI, Groq, vLLM, Speaches), or your own program. Local first; a cloud service is only used if you configure it.
- Images and PDFs need no processing; Claude reads them directly.

`line-cli` does not depend on `media-digest`. If both are installed, Claude uses `media-digest` for LINE videos and voice messages.

### Requirements

- Claude Code, and Python 3.11 or newer on your PATH (Windows, macOS or Linux).
- `line-cli` plugin: the `line` program. You do not install it by hand; Claude offers to do it.
- `media-digest` plugin (all optional, Claude offers to help install each when needed): `ffmpeg` for video and audio, `uv` plus a speech-to-text engine for transcripts (a local model download of about 1.6 GB by default).

## Install

**Option A (recommended): inside Claude Code.** Open Claude Code (the terminal app, or the Code tab of the Claude desktop app) and type these into its chat box, one line at a time, pressing Enter after each:

```text
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

Then start a new Claude Code session so the plugins load.

**Option B: from a terminal.** If the `claude` command is installed, open PowerShell, Terminal or bash and run:

```bash
claude plugin marketplace add timliudev/line-cli-skills
claude plugin install line-cli@line-cli-skills
claude plugin install media-digest@line-cli-skills
```

**Important:** lines that start with `/` are **not** terminal commands. Typing them into PowerShell, Terminal or bash gives "'/plugin' is not recognized". They only work inside Claude Code. In a terminal, use the `claude plugin ...` form of Option B.

**Option C: other agents (Codex, Gemini CLI, Cursor, Copilot, OpenCode…).** Both skills follow the open [Agent Skills](https://agentskills.io) format, so any agent that supports it can use them. In a terminal (needs Node.js):

```bash
npx skills add timliudev/line-cli-skills
```

- It finds both skills (`line-cli`, `media-digest`), asks which agents to install for, and installs them under `.agents/skills/` (with links into each agent's own skills folder, for Claude Code `.claude/skills/`).
- Scope: by default the skills go into the **current project folder** only; add `-g` (`npx skills add -g timliudev/line-cli-skills`) to install for **all your projects** (`~/.agents/skills/`).
- Update later with `npx skills update`.
- **Do not install the same skill both ways on one machine.** If you used Option A or B and also Option C, Claude Code sees two `line-cli` skills. Pick one route; to switch, remove the other first (`claude plugin uninstall line-cli@line-cli-skills`, or `npx skills remove line-cli`).

### Quick start

1. In Claude Code say, for example: "Check my LINE group Family for new messages."
2. The first time, Claude checks for `line`, explains in plain words what it would install, and asks for your OK. Then it asks to log you in: a new terminal window opens where you scan a QR code with your phone. **Logging in signs the LINE Chrome extension out.**
3. After that, ask normally: read a chat, find a contact, "what was the video Anna sent about?" (needs `media-digest`), or "reply OK to Anna". Anything that sends, reacts or unsends is confirmed with you first, every time.

See the [user guide](docs/GUIDE.md) for details, configuration and troubleshooting.

## Status

**v0.1.2 is early software.** How far each part has been tested:

- **Tested live** (a real LINE account, **Windows only**): guided install, login, chat-name resolution, sync and archive, attachment downloads, group names.
- **macOS and Linux:** unit tests only. These have not been tried on a real macOS or Linux machine yet.
- **Sending works** (verified live on Windows with line-cli v0.4.0: a text message to a direct chat was delivered, end-to-end encrypted). It fails only for some peers whose Letter Sealing key is an old version (LINE rejects it with TalkException 86; upstream does not fall back). Then nothing is delivered, Claude tells you the message was not sent, and you send it from the LINE app. Replying (`--reply-to`) uses the same send path but was not separately verified live. Reacting and unsending are implemented (confirmed every time) but have not been tried live.
- **media-digest:** keyframe extraction was tested on synthetic media made with ffmpeg, not on real recordings. The speech-to-text adapters (faster-whisper, whisper.cpp, OpenAI-compatible, custom command) have **not** been run against a real engine or service yet; their unit tests use fakes.
- **Evals:** the 7 plugin evals ([docs/EVALS.md](docs/EVALS.md)) were run once each (3 runs per case) and all 7 passed. They check skill triggering and ask-before-writing behavior, not LINE itself.
- **Known limit:** if upstream `chats --search` fails, name search covers active chats only (the user guide's troubleshooting table has this and two other fixes).
- **Known limit:** @mentions are not supported (upstream has no mention support): "@name" is sent as plain text, not highlighted, and the person is not notified.

See the [user guide](docs/GUIDE.md) and the [changelog](CHANGELOG.md).

## Credits

Built on top of [kongesque/line-cli](https://github.com/kongesque/line-cli) (MIT). This repo ships no upstream code; you install `line` yourself.

## License

MIT - see [LICENSE](LICENSE).
