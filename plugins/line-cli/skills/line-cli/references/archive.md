# Local archive and sync.py

Matches line-cli v0.4.0 (verified 2026-10-12)

`line` keeps no message history and returns at most the latest 100 messages of a chat. `sync.py`
builds the history on the user's machine: it fetches the latest 100, appends what is new, and
downloads attachments immediately because LINE expires them.

## Contents

- Command line
- Archive layout
- Replies, reactions and thread.py
- LINE emoji cache and emoji.py
- Stability for integrations
- Configuration
- What a sync does
- Summary JSON
- Limits and heuristics
- Privacy

## Command line

```
sync.py CHAT [--name LABEL] [--types image,video,audio,file] [--max-bytes N] [--no-download] [--json]
sync.py --all [same options]
```

- `CHAT` is a chat name (resolved like `lineq.py resolve`) or a full ID (`id:` prefix allowed).
  An ambiguous name exits `3` with the candidates; no match exits `4`.
- `--types` and `--max-bytes` override `download.types` and `download.max_bytes` for this run.
  `--no-download` archives text only; attachments then show as "not downloaded" and are picked up
  by a later sync while they are still inside the latest 100 messages.
- `--name` labels the folder when `CHAT` is an ID. A name found by resolving, or a contact name for
  a direct chat, is used automatically. Without any name the folder is just the chat ID, or keeps
  its existing name. Group names are not in `line chats --json`, so `--all` takes them from the
  text listing of active chats (`chats --limit 0 --show-ids`); a group missing from that listing
  stays unlabeled until it is synced once by name (or with `--name`).
- `--all` reads `chats --json` and syncs each chat one after another. A chat that fails is
  reported under `failed` and the rest continue; the exit code is `1` if any chat failed.
  Output: `{"all": true, "total": N, "chats": [summary, ...], "failed": [{chat_id, error}]}`.
- Output is always one JSON document on stdout. `--json` is accepted and changes nothing.
- Exit codes: `0` ok, `1` line failed or archive not writable (`error.kind`), `2` usage, `3`
  ambiguous chat, `4` no such chat.

## Archive layout

```
<archive_root>/
|- contacts.json                      sender name cache (mid -> display name)
`- <chat name>__<chat_id>/            keyed by ID; a rename only changes the part before "__"
   |- messages.jsonl                  source of truth: one raw `line messages --json` item per line
   |- messages.md                     generated for reading; times are local to the machine
   |- state.json                      last sync time, newest message, gaps, failed downloads, self_mid
   `- files/                          <YYYYMMDD-HHMMSS>_<message_id>.<ext>
```

- Folder names are made safe for Windows: reserved characters and names are replaced, and the chat
  name part is capped at 60 characters (the chat ID is never shortened). When the name is unknown
  the folder is just `<chat_id>`.
- Folders are found by chat ID: the current `<name>__<chat_id>`, a bare `<chat_id>`, and the older
  `<chat_id>__<name>` layout all match, and the next sync renames them to the current layout
  (contents untouched). When no name is given and none is stored, a sync looks the name up
  (direct chats via contacts, groups and rooms via `chats --limit 0 --show-ids`, one call per
  run); if that fails the folder stays id-only, silently.
- If more than one folder matches the same chat ID (for example `<name>__<id>` plus an old
  `<id>__<name>` left by a concurrent run of older code), the next sync merges them into the
  canonical one before doing anything else: `messages.jsonl` is unioned by message ID (a decrypted
  copy beats `decryption_failed`, reactions and `possibly_unsent_at` are unioned, otherwise the
  copy from the newer sync wins), `state.json` is merged (failed downloads unioned, latest sync
  time, newest message recomputed), and `files/` entries are moved (same name and identical
  content keeps one, same name with different content keeps both with a `-1` suffix). The emptied
  folders are removed; a folder that still holds anything unexpected is left alone and reported
  as a warning. Idempotent.
- `messages.jsonl` holds the CLI's items unchanged plus one archive-only field,
  `possibly_unsent_at` (ISO UTC time). Deleting `messages.md` is harmless; the next sync rebuilds it.
- Reactions and decryption results of messages still inside the latest 100 are refreshed. A
  readable copy is never replaced by a failed decryption.
- `messages.md`: one `## YYYY-MM-DD` section per day, one line per message
  (`- 14:03 **Sender**: text`), continuation lines indented, `Me` for the user's own messages.
  Attachments are relative links (`[Image](files/...)`); failures read `[Image] expired ...`,
  `[Video] over 20 MiB, not downloaded`, `[File: x.zip] external link, cannot be downloaded`.
  Other content types read `[System event (type 18)]` or `[Unsupported content (type N)]`
  (stickers, contact cards and rich messages have their own labels); no meaning is guessed.
  Between two messages more than `render.quiet_gap_days` days apart (default 3, `0` disables) a line
  `— no messages for X days (YYYY-MM-DD → YYYY-MM-DD) —` is added; it is only a fact about the
  archive and unrelated to the possible-gap marker below.
  Reactions read `_(reactions: ❤️ love (Alice, 14:05), ...)_`: the built-in six (like, love, laugh,
  surprise, sad, angry) as emoji plus name; a LINE emoji reaction as
  `[LINE emoji <productId>/<emojiId>](https://stickershop.line-scdn.net/sticonshop/v1/sticon/<productId>/android/<emojiId>.png)`
  (link only for a 3-digit `emojiId`; pattern from a community API spec, not official LINE docs);
  anything else as `reaction (type unknown)`. The time is local, from `atMillis`. Cached images and
  labels change the LINE emoji form: see "LINE emoji cache and emoji.py".
  Markers: `(possibly unsent)` after a message, and a `> ⚠ Possible gap ...` quote between messages.
- File extensions: content type 1 `.jpg`, 2 `.mp4`, 3 `.m4a`, 14 the sanitized extension of
  `file_name` (`.bin` if none). The first bytes of the file then correct the extension for jpg,
  png, gif, webp, mp4, m4a and pdf. An existing file is never overwritten (a `-1` suffix is added).

## Replies, reactions and thread.py

- A reply is a message whose `reply_to` field holds the ID of the message it answers
  (`messages.md` shows `in reply to Name: "..."`). Reactions are stored on the message that was
  reacted to, in its `reactions` array (`fromUserMid`, `reactionType`, `atMillis`), and are refreshed
  while that message is inside the latest 100.
- "Who replied to / reacted to message X" therefore means: find X, then collect the messages whose
  `reply_to` equals X's ID and read X's `reactions`. Only messages in the archive can be found.
- `thread.py` does exactly that, offline:

```
thread.py CHAT_DIR_OR_ID --message ID [--depth N]
thread.py CHAT_DIR_OR_ID --text SUBSTRING [--from NAME] [--depth N]
```

  `CHAT_DIR_OR_ID` is an archive folder or a full chat ID (looked up under `archive_root`). `--text`
  is a case-insensitive substring of the message text; `--from` narrows it by sender name. Output:
  `{target: {id, time, sender, first_line}, replies: [{id, time, sender, text_first_line}],
  reactions: [{type_label, sender, time}]}`; with `--depth N` greater than 1 each reply also has a
  `replies` list. Times are local `YYYY-MM-DD HH:MM`; sender names come from `contacts.json`
  (a short ID when unknown). Exit codes: `0` ok, `1` archive unreadable, `2` usage, `3` several
  messages match (`candidates` lists them), `4` no archive or no match.

## LINE emoji cache and emoji.py

Built-in reactions (like, love, laugh, surprise, sad, angry) are six fixed emoji. Other reactions are
LINE emoji (`reactionType.paidReactionType`: `productId` + `emojiId`). Sync caches their images once,
shared by all chats:

```
<archive_root>/_line_emoji/
|- index.json                 per emoji "<productId>/<emojiId>": file, label, last_attempt, error
`- <productId>/<emojiId>.png
```

- The image comes from `https://stickershop.line-scdn.net/sticonshop/v1/sticon/<productId>/android/<emojiId>.png`,
  an **unofficial** pattern from a community API spec. It is fetched only for a safe `productId`
  (letters, digits, `_`, `-`) and a 3-digit `emojiId`, with a 10 s timeout, at most 1 MiB, and the
  PNG signature is checked. Animated sticons are untested.
- An emoji already cached is never downloaded again, so the archive grows only by unique emoji, not
  per use. A failed download is recorded in `index.json` (`last_attempt`, `error`) and not retried for
  7 days. `download.line_emoji = false` (or `--no-download`) switches downloading off.
- `messages.md` writes a LINE emoji reaction as a link to the **local** file
  (`../_line_emoji/<productId>/<emojiId>.png`); with a label it reads `LINE emoji "OK hand"`. When the
  image is not cached it falls back to the remote URL, and an unlabelled cached one reads
  `LINE emoji <productId>/<emojiId>`.
- Labels are short text kept in `index.json`: `emoji.py list [--unlabeled]` shows cached emoji with
  their absolute image path, `emoji.py label PRODUCT_ID/EMOJI_ID "OK hand"` saves one (an empty text
  removes it; at most 40 characters, brackets and quotes are dropped). Exit `4`: unknown emoji. A new
  label shows in `messages.md` after the next sync of a chat that uses it.
- Sticker **messages** (content type 7) are shown as unsupported content: line-cli does not say which
  sticker was sent.

## Stability for integrations

Other tools may build on the archive. These parts are stable:

1. **`sync.py` command line and summary JSON.** The options and the fields `chat_id`, `archive_dir`,
   `messages_md`, `new_messages`, `new_messages_from_line`, `new_files`, `failed_files`,
   `gap_possible` and `warnings` (and the other fields listed under "Summary JSON") are not renamed or
   removed. Changes are additive only; anything else is marked **BREAKING** in `CHANGELOG.md`.
2. **Chat folders** are `<name>__<chat_id>` (or just `<chat_id>` when the name is unknown) and hold
   `messages.jsonl`; attachments are `files/<YYYYMMDD-HHMMSS>_<message_id>.<ext>`.
3. **Other entries in `archive_root` are not chats.** Directories that are not chats always start with
   `_` (for example `_line_emoji/`) and never end with `__<chat_id>`; files live at the root (for
   example `contacts.json`, the name cache: `{"schema": 1, "names": {"<mid>": {"name": "...", "ts": ms}}}`).
   Skip every directory whose name starts with `_`.
4. **Read `messages.jsonl`, not `messages.md`.** `messages.md` is for people and its line format may
   change. Integrations should use `messages.jsonl` (`reply_to`, `reactions`, `created_time`, ...) and
   `contacts.json` for names.

## Configuration

Precedence for the archive location, highest first:

1. Environment variable `LINE_SKILL_ARCHIVE`.
2. `archive_root` in `config.toml` (a relative path is relative to the config file).
3. Default: Windows `%LOCALAPPDATA%\line-skill\archive`, macOS
   `~/Library/Application Support/line-skill/archive`, Linux `$XDG_DATA_HOME/line-skill/archive`
   (`~/.local/share/line-skill/archive`).

`config.toml` lives in `%APPDATA%\line-skill\` on Windows and `$XDG_CONFIG_HOME/line-skill/`
(`~/.config/line-skill/`) elsewhere:

```toml
line_path = "/opt/fork/line"                  # optional: use this `line` (a file, or a folder holding it)
archive_root = "D:/somewhere/line-personal"
[download]
types = ["image", "video", "audio", "file"]   # attachment types downloaded automatically
max_bytes = 20971520                          # larger attachments are only marked in messages.md
line_emoji = true                             # cache LINE emoji reaction images once (see above)
[render]
quiet_gap_days = 3                            # messages.md notes silences longer than this; 0 = off
```

`max_bytes` cannot raise the CLI's own 20 MiB cap; it can only lower it. A file over the limit is
removed again after download and recorded as `too_large`; raising the limit later retries it while
it is still inside the latest 100 messages.

## What a sync does

1. `messages CHAT --json --limit 100`, then `contacts --mid ...` for unknown senders (cached for a
   week) and `whoami` to recognise the user's own messages.
   If `contacts --mid` cannot name a sender (typical for non-friend group members), one extra
   `messages CHAT --limit 100 --show-ids` (text) call maps message IDs to display names; they are
   stored in `contacts.json` like any other name. `--all` also names folders of active chats from
   `chats --limit 0 --show-ids`.
2. Merge by message ID: new IDs are appended, known IDs refreshed. Nothing is ever deleted.
3. Download every attachment of the fetched window that has no file yet and whose type is allowed.
   Permanent failures (`expired`, `external`, `too_large`) are recorded in `state.json` and not
   retried; transient ones (for example `processing`, network) are tried again next sync. A login or
   storage problem stops the downloads for that chat without recording anything against messages.
4. Rewrite `messages.md` and `state.json` (atomically: temp file, then rename).

Video and audio are not transcribed during sync; use `media-digest` on a single file when needed.

## Summary JSON

`chat`, `chat_id`, `archive_dir`, `messages_md`, `new_messages`, `new_messages_from_line` (first
`messages.md` line of the new messages, `null` if none), `total_messages`, `new_files[]`
(`message_id`, `type`, `path`, `file`, `bytes`), `failed_files[]` (`message_id`, `type`, `reason`,
`kind`, `message`; only attempts made in this run), `possibly_unsent[]` (`message_id`,
`created_time`, `from`, `excerpt`; newly detected in this run), `gap_possible`, `warnings[]`.
`archive_dir` is `null` for a chat with no messages (nothing is created for it).

## Limits and heuristics

- A first sync reaches only the latest 100 messages. Older history cannot be fetched.
- Attachments: 20 MiB each; expired ones and ones stored behind external URLs cannot be downloaded.
- **Gap**: when the fetched window is full (100 messages) and its oldest message is newer than the
  newest archived message, messages in between may be lost: `gap_possible: true`, a gap marker in
  `messages.md`, an entry in `state.json`. Sync more often (it is cheap) to avoid this.
- **Possibly unsent** (a guess): an archived message whose time lies inside the fetched window but
  which is missing from it. It may also be a message LINE could not return. The message is kept
  and marked, and the mark disappears if it shows up again. Only messages archived before the
  unsend can be detected, and exact unsend events are not available from the CLI.
- Only chats the personal account has joined are visible; this is not the OA chat list.
- Two syncs of the same chat at the same moment are not supported.

## Privacy

The archive contains private conversations. It lives outside the plugin directory, by default in
the user's profile. Never put it in a git repository or share it; `sync.py` warns (in `warnings`)
when `archive_root` is inside one. Never send archive content to third-party services, and do not
paste it into places the user did not ask for. `contacts.json` also holds the names of contacts.
