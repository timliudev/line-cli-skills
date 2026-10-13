# User guide

English | [繁體中文（台灣）](GUIDE.zh-TW.md) | [日本語](GUIDE.ja.md) | [ภาษาไทย](GUIDE.th.md)

<a id="disclaimer"></a>

> Unofficial project, not affiliated with or endorsed by LINE Corporation / LY Corporation. It uses the unofficial kongesque/line-cli client, which may risk suspension of your LINE account. See the README for details.

You mostly just talk to Claude Code in your own language. This guide explains what happens behind the scenes, what Claude will ask you, and how to fix problems.

## Install

Add the marketplace and install the plugins:

```
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

- `line-cli` is for LINE. `media-digest` is optional and works on any video or audio file; install it if you want Claude to understand LINE videos and voice messages.
- You need Python 3.11 or newer. If `python` is not found on your system, Claude tries `python3` or `py -3`.
- The `line` program from kongesque/line-cli is installed separately. You do not have to do it yourself: the first time you use LINE, Claude explains what it will install and asks first (next section).

## First-time setup and login

When you first ask about LINE, Claude runs a check and then walks you through whatever is missing. You are asked before each step.

1. **Install `line`.** Claude tells you, in plain words, that it will download LINE CLI from its official GitHub page, put it in your own user folder (no administrator rights), and check the file has not been tampered with. Nothing happens until you say yes.
   - Windows: the official PowerShell installer, to `%LOCALAPPDATA%\line-cli\bin`.
   - macOS: Homebrew (`brew install kongesque/tap/line-cli`) if you have it, otherwise the official shell installer, to `~/.local/bin`.
   - Linux: the official shell installer, to `~/.local/bin`.
   - The installer script and the program are fixed to a specific tested release (v0.4.0) and verified by checksum (Homebrew installs the tap's current release instead, so it cannot be pinned). It works right away, without reopening your terminal.
   - On Linux desktops the system keyring packages may be missing. Claude shows you the `sudo` command and you run it yourself; Claude never uses `sudo`.
2. **Log in.** Claude warns you first: **logging in signs you out of the LINE Chrome extension**, because LINE allows one such session at a time. After your yes, a new terminal window opens. Scan the QR code with LINE on your phone, or sign in with email and password typed into that window. Claude never sees your password. It waits up to five minutes and confirms with your account name. If no window can be opened (for example over SSH), Claude gives you the one command to run yourself.
3. **Done.** The session is stored in your operating system's secure storage and renews itself, about every week, so you normally log in only once. Logging in again is needed only if LINE signs the session out.

An unofficial client carries a risk to your account (see the README). Using a spare account is wise.

## Everyday use

Just ask, for example "What did the Family group say today?" or "Any new messages from Anna?".

- **Finding the chat.** Claude looks the name up and uses the real chat ID. If several chats match ("Anna" the friend and "Anna" the group), it lists them and asks which one. It never guesses.
- **Reading.** Claude reads the newest messages (20 by default, at most the latest 100 per chat, which is LINE's limit for this client) and summarizes them in your language. Photos, videos, voice messages and files appear as attachments you can ask to open. Stickers cannot be shown. Reading does not mark messages as read.
- **Sending, reacting, unsending.** These reach other people, so every single one is confirmed. Claude shows the chat name, the exact text (or the message and the reaction), and waits for a clear "yes". If you change anything, it asks again.
  - Sent text goes through exactly as shown; a file can be up to 20 MiB; messages up to 10,000 characters.
  - To reply to a specific message, say so, for example "Reply to Alice's last message with OK". Claude finds the message (it is looked up in your local archive, so older archived messages work too), asks if several match, and shows the quoted original together with your reply before sending. Any message can be the target, including photos, videos, voice messages, files and stickers, for example "Reply to the photo Anna sent at 01:28: this one is fine". Known limit: @mentions are not supported; "@name" is sent as plain text, not highlighted, and the person is not notified.
  - Claude tries once and never retries on its own. If the connection drops mid-send, it checks the chat to see whether the message arrived before suggesting anything.
  - Unsend removes your own message for everyone and cannot be undone.
- **Messages from others are treated as data**, not instructions. If a chat message says "run this" or "send that to someone", Claude shows it to you and asks.
- **Local archive.** Before answering questions about a chat, Claude runs a sync: it fetches the latest 100 messages, adds the new ones to a folder on your computer (`messages.jsonl` is the record, `messages.md` is for reading) and downloads new photos, videos, voice messages and files straight away, because LINE expires attachments. That is how "anything new?" is answered quickly, and how history older than LINE's latest 100 is kept, starting from your first sync. You can also ask for a sync directly, for one chat or for "all my chats". Reactions are shown with their emoji, who reacted and when; if the same chat ended up with two archive folders, the next sync merges them into one without losing anything.
  - Good to know: a sync only reads from LINE; the first sync of a chat can only reach its latest 100 messages; if more than 100 messages arrive between two syncs, Claude tells you there is a gap; an archived message that disappears from LINE's latest 100 is kept and marked "(possibly unsent)", which is a guess, not a certainty.

## Attachments and media-digest

- **Photos and PDFs.** Claude opens the file directly from the chat's archive folder (downloaded during a sync, up to 20 MiB).
- **Videos.** With `media-digest`, Claude cuts the video into keyframes (by scene changes, at most 24 by default) and transcribes the speech, then reads one `digest.md` that interleaves frames and text by time.
- **Voice messages and audio.** Claude transcribes them with timestamps.
- Without `media-digest`, Claude tells you the file's location and that installing the plugin lets it understand videos and audio.
- Some things may be missing or unavailable: attachments LINE has expired, files over 20 MiB, and media stored behind external links cannot be downloaded.
- New attachments are downloaded for you during a sync (up to 20 MiB each, every type by default) into the chat's `files` folder. Photos and PDFs are read from there; videos and voice messages go to `media-digest`.

What `media-digest` needs, offered with your consent and a stated download size:

| Need | For | How |
| --- | --- | --- |
| ffmpeg | video frames, audio handling | `winget install --id Gyan.FFmpeg -e` (Windows), `brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Linux, you run it) |
| uv | the default local transcriber | winget, brew or the official installer |
| A speech-to-text engine | transcripts | local `faster-whisper` by default (installs itself on first use; model about 1.6 GB, downloaded once); alternatives below |

Without ffmpeg you still get images and PDFs. Without a speech-to-text engine you still get keyframes for videos.

Claude runs `digest.py FILE... --out OUTDIR` from the plugin's `skills/media-digest/scripts` folder for you. Useful options: `--lang zh` (speech language, default auto-detect), `--max-frames 24`, `--no-asr` (frames only), `--force` (redo). Output: `OUTDIR/digest.md` and `digest.json`. Results are cached, and files that had no transcript are retried automatically once an engine is installed.

## Configuration

Everything works without configuration. Two optional `config.toml` files exist (each skill documents its own in `references/`). Secrets are never stored in them, only the **names** of environment variables.

**`line-cli`** (`%APPDATA%\line-skill\config.toml` on Windows, otherwise `~/.config/line-skill/config.toml`):

| Key | Meaning |
| --- | --- |
| `line_path` | Use a specific `line` program (for example a fork or pinned build) |
| `archive_root` | Where the archive is kept (the environment variable `LINE_SKILL_ARCHIVE` also sets it and wins). Default: `%LOCALAPPDATA%\line-skill\archive` (Windows), `~/Library/Application Support/line-skill/archive` (macOS), `~/.local/share/line-skill/archive` (Linux) |
| `download.types`, `download.max_bytes` | Which attachment types are downloaded automatically (`image`, `video`, `audio`, `file`) and the size limit in bytes (default 20 MiB, the most this client can fetch) |
| `render.quiet_gap_days` | In `messages.md`, add a "no messages for X days" line between two messages further apart than this many days (default 3; `0` turns it off) |

**`media-digest`** (`%APPDATA%\media-digest\config.toml` on Windows, otherwise `~/.config/media-digest/config.toml`; override with `MEDIA_DIGEST_CONFIG`):

```toml
[asr]
order = ["faster_whisper", "whisper_cpp", "groq"]   # try these in order

[asr.routes]                                          # per speech language
zh = ["faster_whisper", "groq"]

[adapters.groq]                                       # a cloud service, used because it is listed above
preset = "groq"                                       # api_key_env = "GROQ_API_KEY" comes with the preset
```

- Engines: `faster_whisper` (local, default), `whisper_cpp` (local, needs `model_path`), `openai_compat` (OpenAI, Groq, vLLM, Speaches presets or your own URL), and `command` (any program you already have, including your own server).
- Without a file the chain is local only. A cloud engine is used only if it is listed in `order` or `routes`.
- Export your API key in the shell that starts Claude Code, for example `export GROQ_API_KEY=...`, and never paste it into the chat.
- Language routes apply when you give a language (`--lang zh`), not when it is `auto`.
- Full reference: the `references/config.md`, `references/adapters.md` and `references/install.md` files of the `media-digest` plugin.

## Privacy

- Your chats, downloaded attachments and digests are private conversations. They are kept on your machine, never inside the plugin directory. Never commit or publish them, and keep them out of any git repository.
- The archive is a folder of private conversations on your machine (by default in your user profile, never in the plugin directory). If its location is inside a git repository, Claude warns you: move it, and never commit or publish it.
- Claude does not send chat content to other services. Transcription is local unless you configured a cloud engine; Claude tells you which service would receive the audio and asks before the first upload.
- Claude never asks for your LINE password in the chat, and does not read the stored session.
- Sending, reacting and unsending always need your explicit yes; nothing is sent in the background.
- Messages you read may contain instructions aimed at an AI. Claude treats them as text, not as commands.

## Troubleshooting

Ask Claude to "run the LINE doctor". It runs `setup.py --doctor` from the plugin's `skills/line-cli/scripts` folder, which checks Python, the `line` program and its version, login, the keyring (Linux), your config file, whether the archive folder is writable, and whether an update exists, and gives a plain-language fix for each item. Review the output before pasting it into a bug report; it can contain local paths and your account name.

| Symptom | What to do |
| --- | --- |
| "line not found" | Let Claude run the guided install, or check `line_path` in your config |
| "not logged in" | Ask Claude to log you in; remember it signs out the Chrome extension |
| Login window did not open | Run `line login` yourself in a terminal, then ask Claude to check again |
| Linux: keyring missing or locked | Install the packages Claude shows (`libsecret-tools`, `gnome-keyring`) and unlock the keyring; for servers see the headless option in `references/setup.md` |
| "busy" | Another `line` command is running; wait a moment (reads retry automatically) |
| Video has frames but no transcript | No speech-to-text engine is ready: ask Claude to list and set one up |
| "needs ffmpeg" | Install ffmpeg (see above), then run again |
| Transcript failed on a long recording with a cloud service | Audio over 25 MB is not split yet; use a local engine |
| Message might not have been sent | Claude checks the chat first and never resends without your yes |
| Claude warns about a "gap" | More than 100 messages arrived since the last sync, and the missing ones cannot be recovered. Sync more often |
| A message is marked "(possibly unsent)" | It vanished from LINE's latest 100 after it was archived, so it was probably unsent. The saved text is kept |
| A photo or file is marked "expired" or "over 20 MiB" | LINE no longer has it, or it is above this client's limit, so it cannot be downloaded |
| Archive warning about a git repository | Set `archive_root` (or `LINE_SKILL_ARCHIVE`) to a folder outside the repository |
| Searching a chat name fails with "LINE request failed" | Upstream names every chat, including inactive ones, in one go and one failure stops it. Claude falls back to your active chats and says so; inactive chats are not found by name, use the full ID |
| Group messages show truncated IDs instead of names | Non-friend group members cannot be looked up as contacts. Sync reads their names from the chat text instead and caches them; if a name is still missing, the person has not written inside the latest 100 messages |
| Sending to one particular person always fails with "LINE request failed" | Known limit: sending works in general, but that person's Letter Sealing key is an old version, so LINE rejects it (error 10051 / 86, "e2ee or version mismatch") and upstream does not fall back. Nothing was sent; Claude says so and does not retry. Send that message from the LINE app. Reading the chat still works |
| `chats --search` or looking up group member names fails with "LINE request failed" | Upstream bug: one deleted account or non-friend member in the batch makes LINE's contact lookup fail. Already worked around (active chats only, names read from the chat text), see the two rows above |
| Windows install fails with a `tar` error under Git Bash | Git's GNU tar cannot read `C:` paths. The installer now puts Windows' own System32 first on PATH for the install; if you install by hand, run it from PowerShell |

[Back to the README](../README.md)
