# broadcast.py and watch.py

Matches line-cli v0.5.0 (verified 2026-10-20)

Two scripts built on the same primitives as `sync.py`: `broadcast.py` sends one message to many
chats (dry run first), `watch.py` keeps the archive live from `line watch` and notifies for VIP
chats. Both only use what the CLI offers: `send`, `messages` (at most 100, no paging), `watch`
(NDJSON; only one watcher at a time), `chats`, `contacts`.

## Contents

- broadcast.py
- watch.py (which chats are archived; sends and the session lock)
- VIP notifications
- Running watch.py in the background on Windows
- Fallback: scheduled sync.py --all
- Gaps and how to avoid them

## broadcast.py

```
broadcast.py --recipients FILE (--text TEXT | --stdin) [--file PATH]                  # dry run
broadcast.py --recipients FILE (--text TEXT | --stdin) [--file PATH] --yes --plan HASH
             [--delay SECONDS] [--max-recipients N]
```

- `FILE` is UTF-8, one chat name or full chat ID per line. Blank lines and lines starting with `#`
  are ignored. Duplicates (two entries that resolve to the same chat) are sent once and listed under
  `duplicates`.
- The text comes from `--text` or stdin; trailing newlines (the one a heredoc adds) are removed, and
  the `text` in the output is exactly what will be sent. At most 10,000 UTF-16 units. `--file` adds
  an attachment (up to 20 MiB). `line send` takes either text or a file, so with both the text is
  sent first and the file second (`steps_per_recipient`).
- **Dry run (default)**: resolves every entry with the same logic as `lineq.py resolve`, with one
  difference: a lone partial-name match is not accepted (`inexact`), because a bulk send should not
  rest on a guess. A name whose lookup was **incomplete** (one of the two searches failed, which
  currently happens often because `line chats --search` fails on some accounts) is not accepted
  either: a second chat with the same name could be hiding. Use the full chat ID, which always
  resolves without a lookup. Output: `recipients[]` (`query`, `id`, `name`, `type`),
  `unresolved[]` (`query`, `status` = `ambiguous` | `inexact` | `incomplete` | `not_found` |
  `error`, `candidates[]` with IDs, and a `message` for `incomplete`), `duplicates[]`, the
  `text`, the `file`, `delay_seconds` and `plan`. Nothing is sent, and no `send` is ever run.
- Exit codes: `0` all resolved (dry run) or everything sent, `1` a send failed or `line` itself
  failed during lookup, `2` usage (bad list, empty or too long text, missing file, more than
  `--max-recipients`, default 50), `3` something to decide (ambiguous / inexact entries, or the plan
  changed), `4` every unresolved entry matched nothing.
- **`--yes` sends.** Without `--yes` it never sends. It resolves again, refuses (exit `3`, nothing
  sent) if anything is unresolved, and also if the list or text changed since the dry run.
  `--plan HASH` is **required** with `--yes` (exit `2` without it): the `plan` of the approved
  dry run, a hash of the resolved chat IDs, the text and, for `--file`, the path, size and
  SHA-256 of the file's bytes. Then it
  sends one recipient after another with `--delay` seconds between recipients (default 3), to stay
  clear of rate limits.
- **First failure stops the run. Nothing is retried** (a send gets exactly one attempt). The report:
  `results[]` with `status` `sent` | `failed` | `not_attempted` per recipient; for a failed one
  `error` (`kind`, `message`), `failed_step` (`text` or `file`), `delivered` (steps that did go out
  before it) and `may_have_been_delivered` (`false` only for errors that prove nothing was sent,
  such as `not_logged_in` or `busy`; for a timeout, network error or unknown error the answer may
  have been lost after delivery: look at that chat's latest messages, errors.md "Lost response").
  `summary` counts them. `sent: true` only when everyone got it.
- **Never run a failed broadcast again**: those who already received it would get it twice. To
  continue, make a new list from the `not_attempted` entries (plus the failed one only after
  checking it), dry-run it and get a new explicit yes.

## watch.py

```
watch.py [--archive NAME_OR_ID ...] [--archive-all] [--vip NAME_OR_ID ...] [--no-notify]
         [--no-initial-sync] [--no-download] [--duration SECONDS] [--settle SECONDS]
         [--max-restarts N] [--log FILE]
```

How it works:

1. It resolves the VIP and archive lists (below), then starts `line watch --json` and reads its events:
   - `message` of an **archived chat** (see "Which chats are archived"): the message is appended to
     `<archive>/<chat folder>/messages.jsonl` at once, unless its ID is already there (dedupe by
     message ID; replayed events are harmless). An allowlisted chat without a folder gets a bare-ID
     folder, which the next sync names like any other. A `message` of any other chat is ignored:
     nothing is written, nothing is logged (a VIP chat still notifies).
   - `resync_required`: LINE reported a gap, and the event names no chat, so the archived chats
     (folders plus allowlist) are synced.
   - `operation` and anything else: ignored.
2. A chat with new messages is updated once it has been quiet for `--settle` seconds (default
   15), or after 2 minutes at most while it stays busy. Normally this is a **local re-render**:
   `messages.md` and the newest-message pointers in `state.json` are rewritten from
   `messages.jsonl`, the name cache and the downloaded files, with **no `line` call**. The full
   `sync.py` logic (names, attachment downloads) runs instead only when a new message has an
   attachment of a type that would be downloaded (not with `--no-download`), when its sender
   has no cached name (asked once per sender and run), or when the chat has no name yet (once);
   and for every archived chat at start and on `resync_required`, with a 2 s pause between
   chats. So in steady state plain text costs the session lock nothing (see "Sends and the
   session lock"). A failed sync is tried again after 30 s, five times at most;
   the messages themselves are already safe in `messages.jsonl`.
3. At start it syncs the archived chats too (`--no-initial-sync` skips that), which catches up
   whatever arrived while nothing was watching. `line watch` itself resumes from its saved
   checkpoint, so events that happened while `watch.py` was off are normally replayed as well; the
   sync is the safety net: it pages back to the archive and reports a `gap` only when that took
   more than `sync.py`'s page limit (10 pages, 1000 messages) or an older page failed.
4. If `line watch` exits, it is started again after 2 s, then 4, 8 ... up to 120 s; a run that
   lasted a minute starts the backoff over. It does **not** restart for: another `line watch`
   already running (exit `3`, "only one watcher at a time"), a lost login (`not_logged_in`),
   missing `line`, or a storage problem (exit `1`). `--max-restarts N` (default 10) gives up after N CONSECUTIVE fast failures, about 10 minutes
   of crash loop; a run that lasted a minute resets the count, so a healthy watcher runs forever
   (`0` = never restart). The summary then says `gave_up`.
5. Ctrl-C (or SIGTERM, or `--duration`) stops `line watch`, saves the chats still waiting for a
   sync and prints one JSON summary: `stopped` (`interrupted`, `duration`, `fatal`, `gave_up`),
   `error`, and counters (`events`, `messages_archived`, `duplicate_messages`, `resync_events`,
   `chats_synced`, `ignored_messages`, `sync_failures`, `notifications`, `restarts`, `gaps`).

### Which chats are archived

Private conversations are only stored when the user chose that. `watch.py` archives a chat only if
at least one of these holds:

- it already has an archive folder (the user synced it before);
- it is listed under `[watch] archive_chats` in `config.toml` (names or full IDs);
- it was passed with `--archive NAME_OR_ID` (repeatable; a name must match exactly one chat
  by its exact name, a partial match is refused,
  otherwise it is skipped with a warning).

Events from all other chats are dropped before anything is written, and no text is logged. The
stdout summary counts them as `ignored_messages`. `--archive-all` switches this protection off and
stores every chat that receives a message, **including private 1:1 chats you never synced**, with
their attachments; use it only on purpose. VIP notifications do not need the chat to be archived,
and for an unarchived VIP chat nothing (not even a sender name) is written to disk.

The folder set is re-read at most every 5 seconds, so a folder the user deletes is noticed
within that time and is not recreated by the next message (an allowlisted chat is). Log lines go
to stderr (or `--log FILE`) and never contain message text. Do not run `sync.py` for the same chat at the same moment as `watch.py`; while
it runs the archive is current anyway.

### Sends and the session lock

While `watch.py` runs, a `line send` (or `broadcast.py`) can fail with `busy` ("another line command
is using the session; retry when it finishes") during one of its full syncs, which hold LINE's
session lock for moments, and a write never waits for it. Plain text messages are archived and
rendered without any `line` call, so this only happens at start, after `resync_required`, for
attachment downloads and for unknown names. Raw `line watch` alone does not block sends. `busy` on a
write means the lock was not acquired before anything reached LINE, so **the message was not sent**.
Tell the user, let the sync finish (a few seconds), and send again only after they confirm that
attempt once more; never retry silently. In a broadcast the run stops at that recipient like at
any failure (`may_have_been_delivered: false`), and the rest is `not_attempted`.

Only one `line watch` can run at a time (a second one fails with "another line watch is already
running"), so `watch.py` cannot coexist with a service or another terminal running `line watch`, and
`lineq.py run -- watch ...` fails while it runs.

## VIP notifications

Configuration (`config.toml`, see archive.md): names or full IDs only, no secrets.

```toml
[watch]
vip_chats = ["Project Team", "Alice", "C0123456789abcdef0123456789abcdef"]
```

`--vip NAME_OR_ID` adds more for one run; `--no-notify` switches notifications off. At start each
name is resolved like `lineq.py resolve` but must be an exact name matching exactly one chat;
a partial match, several matches or none skip the entry with a warning on stderr (never
guessed). A full ID is used as is.

A notification is shown for a `message` event that is an incoming message of a VIP chat: not sent
by the user (operation type 25, or `from` equal to the user's own MID), not older than 15 minutes
(so a backlog replayed after a pause does not flood the screen), and not seen before (replays are
de-duplicated). Title: the chat name. Body: `Sender: first 80 characters`, or `[Image]`, `[Video]`,
`[Audio]`, `[File]` for attachments.

- **Windows**: a toast through Windows PowerShell 5.1 and the WinRT `ToastNotificationManager`
  (stdlib only; the toast is attributed to PowerShell). The script runs as an encoded command, so
  no execution-policy bypass is used, and PowerShell gets only a minimal environment (system
  paths, user profile folders and the two text variables), never API keys or tokens from the
  parent. The text travels in those variables and is XML-escaped inside the script, so message
  content never becomes code.
- **Elsewhere, or if the toast fails** (no `powershell`, notifications blocked, Focus Assist does
  not fail the call but may hide the toast): one line `[VIP] new message in <chat> from <sender>` on stderr (or the log file). The log never
  contains the preview or any message text; only the toast shows it.
- The preview shows chat text on screen, which others may see.

## Running watch.py in the background on Windows

The user's choice; do not create tasks or start background processes without their yes. Find the
paths first: Python (`where pythonw`) and the plugin's `scripts` folder. `pythonw` has no console
window, so give it `--log`:

```
pythonw "C:\path\to\scripts\watch.py" --log "%LOCALAPPDATA%\line-skill\watch.log"
```

To start it at every logon with Task Scheduler (show the command to the user, who runs it):

```
schtasks /Create /TN "LINE watch" /SC ONLOGON /RL LIMITED /TR "\"C:\path\to\pythonw.exe\" \"C:\path\to\scripts\watch.py\" --log \"C:\Users\NAME\AppData\Local\line-skill\watch.log\""
```

`/RL LIMITED` keeps normal rights, no administrator needed. Start it now with
`schtasks /Run /TN "LINE watch"`; stop it with `schtasks /End /TN "LINE watch"` (this kills it,
which is safe: `line watch` writes its checkpoint only after an event was delivered, so at worst the
last event is replayed and deduplicated); remove it with `schtasks /Delete /TN "LINE watch" /F`.
Check that it runs with `schtasks /Query /TN "LINE watch"` and by looking at the log file.

The log is not private message text but does contain chat IDs and chat names in warnings.
If the toast does not appear, check Windows notification settings for PowerShell and Focus Assist.

## Fallback: scheduled sync.py --all

Without a permanent process, syncing on a schedule works as long as fewer than about 1000
messages (sync's default `--max-pages 10`) arrive per chat between runs (every 15 minutes in the
example). Run `sync.py --all` through
Task Scheduler (`pythonw` is fine: the scripts tolerate having no console):

```
schtasks /Create /TN "LINE sync" /SC MINUTE /MO 15 /RL LIMITED /TR "\"C:\path\to\pythonw.exe\" \"C:\path\to\scripts\sync.py\" --all"
```

Remove it with `schtasks /Delete /TN "LINE sync" /F`. This does not stop a `line watch` and does
not conflict with it (`sync` only reads). It produces no output anywhere: add `--no-download` only
if attachments are not wanted. A permanent `watch.py` is better for busy chats and for VIP
notifications; do not run both on the same chat at the same moment.

## Gaps and how to avoid them

Since line v0.5.0 `sync.py` reads history in pages and keeps paging back until it reaches the
newest archived message, so missed messages are normally filled in. A gap remains only when paging
stopped first: after `--max-pages` pages (default 10, 1000 messages) or because an older page
failed. `sync.py` then reports `gap` (`from`, `to`, in UTC), writes a marker into `messages.md` and
records it in `state.json`. Run `sync.py CHAT --max-pages N` with a larger N to fetch the rest
(the marker stays). Messages older than the history LINE still serves cannot be fetched at all.
