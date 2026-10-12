# line-cli-skills: Claude Code skills for LINE and media attachments

English | [繁體中文（台灣）](README.zh-TW.md) | [日本語](README.ja.md) | [ภาษาไทย](README.th.md)

Claude Code skills for working with a **personal LINE account** and the media people send you. Status: **early development (v0.0.x)** - reading, sending and media digestion work; local sync/archive is planned.

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

```
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

### Quick start

1. In Claude Code say, for example: "Check my LINE group Family for new messages."
2. The first time, Claude checks for `line`, explains in plain words what it would install, and asks for your OK. Then it asks to log you in: a new terminal window opens where you scan a QR code with your phone. **Logging in signs the LINE Chrome extension out.**
3. After that, ask normally: read a chat, find a contact, "what was the video Anna sent about?" (needs `media-digest`), or "reply OK to Anna". Anything that sends, reacts or unsends is confirmed with you first, every time.

See the [user guide](docs/GUIDE.md) for details, configuration and troubleshooting.

## Status

Early development. Implemented: guided install and login, reading and searching chats, downloading attachments, confirmed send/react/unsend, media digestion and speech-to-text backends. Planned: local sync and archive of chat history (`sync.py`) so history older than LINE's latest 100 messages per chat is kept. See the [user guide](docs/GUIDE.md) and the [changelog](CHANGELOG.md).

## Credits

Built on top of [kongesque/line-cli](https://github.com/kongesque/line-cli) (MIT). This repo ships no upstream code; you install `line` yourself.

## License

MIT - see [LICENSE](LICENSE).
