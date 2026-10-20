# line-cli command reference

Matches line-cli v0.5.0 (verified 2026-10-20 against `line COMMAND --help` and docs/CLI.md at the
v0.5.0 tag)

Source paths are relative to the upstream repo [kongesque/line-cli](https://github.com/kongesque/line-cli).
Source line numbers were taken from a v0.4.0 checkout (`d3e79ff`); v0.5.0 moved code around, so
treat them as pointers, not exact lines. Options added in v0.5.0 cite docs/CLI.md sections instead.

## Contents

- Global behavior
- Command classification (read-only, remote mutation, local destructive)
- help / version
- whoami
- logout
- contacts
- chats
- members
- messages
- send
- react
- unsend
- download
- watch
- login
- auth status
- auth migrate
- update
- Non-interactive usage rules

## Global behavior

- `line` with no arguments, `line help`, `line --help`, `line -h` print help and exit 0.
  `line --version` is the same as `line version` (internal/cli/cli.go:84-89).
- Unknown command: `unknown command; run line help` (cli.go:121), exit 1.
- Invalid flags: `invalid options; run line <command> --help`, exit 1.
- Extra positional arguments are rejected (`unexpected arguments ...`), exit 1.
- Stdout carries results (human output or `--json`). Stderr carries prompts,
  progress, warnings, and errors. Errors print as `line: <message>` (cmd/line/signals.go:58-62).
- Interactive mode requires stdin to be a terminal and, unless the command is
  `login`, stdout to be a terminal too (cmd/line/main.go:36). Redirected stdout
  therefore disables prompts for every command except `login`.
- Prompts are never shown with `--json` or `--stdin` (messages.go:54, actions.go:42,
  download.go:47). Scripts must pass every required argument.
- Ctrl-C prints `Cancelled.`/`Stopped after receiving a signal.` and exits 130;
  SIGTERM exits 143 (cmd/line/signals.go:17-77).
- `--timeout DURATION` (e.g. `30s`) is accepted by `whoami`, `contacts`, `chats`, `members`,
  `messages`, `send`, `download`, `react`, `unsend`. Default `0` (no deadline), except `download`
  (`2m`). The deadline covers name and message lookup, authentication recovery, key preparation
  and transfers; a deadline failure exits 1 (docs/CLI.md "Saved sessions and cancellation").
- `--error-format=json` (global, **before** the command): one structured error object on stderr
  for the core commands and `auth status`; stdout keeps its normal success/partial format and it
  disables prompts. `login`, `logout`, `auth migrate`, `watch` and `update` reject it
  (docs/JSON.md "Errors"). The skill's scripts still parse the text errors.
- Every command that touches the session takes a session lock. If another process
  holds it, the command fails fast with exit 75 (see errors.md). Watch waits instead
  of failing (internal/events/watch.go:39-57).

## Command classification

| Class | Commands | Notes |
| --- | --- | --- |
| Local only, no LINE contact | `help`, `version`, `auth status`, `auth migrate`, `logout` | `auth status --check` writes a local probe to test storage. |
| Network, no LINE login | `update --check`, `update` | Contacts the GitHub release API. `update` (without `--check`) replaces the executable when it can self-update. |
| Read-only remote | `whoami`, `contacts`, `chats`, `members`, `messages`, `download` | `download` also writes a new local file. Reading does not mark messages as read (docs/CLI.md:234). |
| Read-only remote, long-running | `watch` | Writes a local resume checkpoint in session storage (internal/events/watch.go:124-131, 200-203). No LINE mutation. |
| Remote mutation | `send`, `react`, `unsend`, `login` | Each is attempted once. Never retried automatically (TOKEN_SESSION.md; docs/CLI.md:260-262). |
| Local destructive | `logout`, `auth migrate --storage=headless`, `update` (installs) | `logout` removes the local session only; it does not revoke it on LINE (docs/CLI.md:489). |

## help / version

- `line help` (no other args). `line version` prints `line <version>`
  (cli.go:109-116). Both take no flags.

## whoami

```
line whoami [--json] [--show-ids]
```

- `--json` (bool, default false): writes the full LINE profile object (cli.go:164-165).
  See json-schema.md.
- `--show-ids` (bool, default false): human output only; adds `ID: <mid>` (cli.go:168-170).
- Human output: `Signed in as <name>` then `Session saved securely on this device.`
- Remote read. Requires a saved session; otherwise `no saved LINE session; run line login` (exit 1, see errors.md).

## logout

```
line logout
```

- No flags. Deletes the local session (cli.go:146-151). Output:
  `Signed out on this device. Your LINE account remains active on your phone.`
- Local destructive. Does not revoke the session on LINE (docs/CLI.md:489).
- No `--json`.

## contacts

```
line contacts [--search TEXT] [--limit N] [--show-ids] [--json]
line contacts --mid USER_MID [--mid USER_MID ...] [--json]
```

- `--search` (string, default ""): case-insensitive substring match on the effective
  display name (contacts.go:26, 86). Cannot be combined with `--mid` (contacts.go:53).
- `--limit` (int, default 20): maximum rows; `0` means all. Negative values are rejected
  (`limit must be 0 or greater`, contacts.go:69-71). With `--json` and no explicit
  `--limit`, all matches are returned (contacts.go:90-92). Cannot be combined with `--mid`.
- `--mid` (string, repeatable): direct lookup of user MIDs (contacts.go:28-31).
  - Must match `^(?:[uU][A-Za-z0-9_-]{43}|u[0-9a-f]{32})$` (contacts.go:175). Group/room IDs
    and display names are rejected: `invalid user MID at --mid #N ...`.
  - Duplicates are removed; input order is kept (contacts.go:56-67).
  - Does not add the person as a friend.
  - Exit 1 if any requested MID is unavailable, after printing or emitting all rows
    (contacts.go:223-225). See json-schema.md for `status`.
- `--show-ids` (bool, default false): human output only; prints the MID under a name
  when the name differs from the MID (contacts.go:111-113).
- `--json` (bool): list output is the `line.Contact` array; `--mid` output is the lookup array.
- Remote read. Friend list is fetched in batches of 100 (contacts.go:146).

## chats

```
line chats [--search TEXT] [--all] [--limit N] [--show-ids] [--json]
```

- `--all` (bool, default false): include inactive conversations. Human output only; ignored
  when `--search` is set or `--json` is used (chats.go:56).
- `--search` (string): case-insensitive substring match on the chat name. Searching includes
  inactive chats (chats.go:56, 77-88).
- `--limit` (int, default 20): `0` means all. Negative is rejected (chats.go:40-42).
  With `--json` and no explicit `--limit`, all rows are returned (chats.go:67-69).
- `--show-ids` (bool): human output; shows the ID under each row, and always for rows without
  a name (chats.go:115-119).
- `--json` (bool): see the legacy-JSON note below and json-schema.md.
- Human columns: `UPDATED`, `UNREAD`, `TYPE` (Direct/Group/Room/Unknown), `CHAT`.
  TYPE is derived from the ID prefix (chats.go:125-139).
- Legacy JSON: `--json` with no `--search` returns only `id`, `type`, and `unread_count`
  (chats.go:54-65). `name` and `updated_at` are absent. Add `--search` to get names
  and activity time.
- `--details` (bool, requires `--json`, v0.5.0): `{"schema_version":1,"chats":[...]}` with `id`,
  `type`, `name` (or `null`), `name_status`, `updated_at`, `unread_count` for every chat, newest
  activity first (docs/JSON.md "Chat envelope").

## members

```
line members CHAT [--json] [--timeout DURATION]
```

- v0.5.0. Groups only: direct chats and legacy rooms are unsupported. Read-only; never registers
  keys or sends read receipts (docs/CLI.md "List group members and mention someone").
- `--json`: `schema_version: 1` envelope with `chat`, `members[]`, `invitees[]` (each `mid`, `name`
  or `null`, `name_status` `resolved`/`unavailable`, `self`), sorted by MID, plus
  `members_complete` / `invitees_complete`. A present empty list is complete; a missing or `null`
  list is incomplete.
- Use it to get the user MID for `send --mentions`.

## messages

```
line messages [CHAT] [--limit N] [--show-ids] [--json] [--timeout DURATION]
line messages CHAT --page --json [--limit N] [--cursor CURSOR] [--details [--resolve-names]]
line messages CHAT --details --json [--resolve-names]
line messages CHAT --diagnostics --json [--limit N]
```

- `CHAT` (positional, optional): full chat ID or unique exact name, case-insensitive. Quote names
  with spaces. Required non-interactively (messages.go:55-59). A flag may come before the chat.
- `--limit` (int, default 20): fetch 1-100 messages per call (messages.go:36, 61-63).
  Human output is oldest first; plain `--json` keeps server order (newest first in practice).
- `--show-ids` (bool): human output; shows each message ID and reply targets (display.go:92-105).
- `--json` (bool): array of message objects (json-schema.md).
- `--page` (bool, requires `--json`, v0.5.0): one history page, an object with
  `schema_version: 1`, `chat_id`, `order: "newest_first"`, `messages` (same message objects as
  plain `--json`), `exhausted`, `stop_reason` (`count_limit`, `page_limit`,
  `end_of_available_history`) and `next_cursor` (absent when exhausted). Pass the cursor
  unchanged with `--cursor` to the same chat for the next older page. A short page is not the end
  (the anchor takes a slot, a page can even be empty); only `exhausted: true` is. Only history
  still available to this client can be read; neither full history nor a fixed retention is
  guaranteed (docs/CLI.md "History pagination JSON"). `sync.py` uses this.
- `--details` (bool, requires `--json`, v0.5.0): versioned envelope with decoded `content`
  (`kind` text/location/image/video/audio/file/contact/sticker/flex/rich/unsupported, incoming
  `mentions`, sticker IDs, attachment name/size), `sender` and `chat`. `--resolve-names` adds
  sender names (batched). Plain `--json` keeps the legacy shape (docs/JSON.md "Detailed content").
- `--diagnostics` (bool, requires `--json`): message IDs and counts before decryption, no
  content. Cannot combine with `--details`, `--resolve-names`, `--page`, `--cursor`.
- Read-only. Reading never marks messages as read.
- If any message has status `decryption_failed`, the full JSON or text output (or the page
  object, with its cursor) is still written, then the command exits 1 with
  `N messages could not be decrypted; see each message's status/error` (messages.go:202-218).

## send

```
line send [CHAT] --text TEXT [--reply-to MESSAGE_ID] [--mentions JSON] [--require-e2ee] [--json]
line send [CHAT] --stdin [--reply-to MESSAGE_ID] [--mentions JSON] [--require-e2ee] [--json]
line send [CHAT] --file PATH [--reply-to MESSAGE_ID] [--require-e2ee] [--json]
line send CHAT --image PATH [--require-e2ee] [--json]
```

All forms also take `--show-ids` and `--timeout DURATION`.

- Exactly one of `--text`, `--stdin`, `--file`, `--image` is required non-interactively
  (messages.go:70-87). Remote mutation.
- `--text` (string): non-empty, valid UTF-8, at most 10,000 UTF-16 units (client.go:18, 29-40).
- `--stdin` (bool): reads UTF-8 from stdin. Read limit is 40,000 bytes
  (`MaxTextUnits*4`, messages.go:118-124). Input is NOT trimmed: a trailing newline from
  `< file` stays in the message. Strip it yourself if you do not want it.
- `--file` (path): generic file, regular files only, at most 20 MiB (messages.go:89-112;
  files.go:25). The sent name is `filepath.Base(PATH)`; the name must not contain `/` or `\`
  or control characters (files.go:50-57). Images, video, and audio are sent as ordinary files
  (docs/CLI.md:249-250). Stickers and specialized media cannot be sent.
- `--image` (path, v0.5.0): native still JPEG/PNG (converted to JPEG, metadata removed, at most
  1280x1280). **Encrypted native images are disabled** (`encrypted_image_unavailable`), so this only
  works where LINE explicitly allows non-E2EE images, and live delivery is unverified upstream.
  No replies, mentions or text with it. Prefer `--file` for images.
- `--reply-to` (string): numeric message ID. v0.5.0 verifies that the target is in this chat
  before any upload or send, looking back at most 10 pages / 1000 messages / 30 s
  (`message_not_found`, `lookup_incomplete`, `deadline_exceeded`; docs/CLI.md "Download or change
  a message").
- `--mentions` (JSON string, v0.5.0): explicit array, at most 100 entries, each
  `{"start":S,"end":E,"mid":"USER_MID"}` or `{"start":S,"end":E,"all":true}`. `start` inclusive,
  `end` exclusive, in UTF-16 code units of the exact text (😀 = 2 units). Spans cannot overlap or
  split a surrogate pair; `all` needs a group or room. Text and stdin only (with or without
  `--reply-to`); not with `--file`, `--image` or a prompted message. Literal `@name` text creates
  no mention. Invalid input is rejected before the session is used. Mention metadata is outside the
  encrypted text, as in LINE for Chrome (docs/CLI.md "List group members and mention someone").
- `--require-e2ee` (bool, v0.5.0): send only with Letter Sealing; otherwise fail with
  `encryption_required` before any remote action. **The skill passes it by default** (`lineq.py run
  -- send`, `broadcast.py`); `--allow-non-e2ee` on those scripts drops it.
- `--show-ids` (bool): human output adds `ID: <id>` and a group-key note
  (messages.go:183-195).
- `--json` (bool): see json-schema.md (`send`).
- Human receipt says `end-to-end encrypted` or `transport encrypted (no E2EE)`.
- The CLI makes one send attempt. Result ambiguity messages are in errors.md.
- Blocked contacts are refused before send (client.go:268-282).
- Encryption fallback (v0.5.0): sends prefer Letter Sealing. When LINE explicitly reports no
  E2EE support for the chat (checked separately for text and for files), the CLI sends over
  HTTPS/TLS **without asking**, also in `--json`/`--stdin` mode; LINE can read that content.
  Missing or invalid capabilities, missing keys, incomplete membership and transport errors never
  allow a downgrade. `--require-e2ee` blocks the fallback (docs/CLI.md "Send messages and files").
- A non-E2EE file message is created before its bytes are uploaded; if the upload then fails,
  the message exists (structured errors keep its ID). Check LINE before doing anything else.

## react

```
line react [CHAT] --message MESSAGE_ID --reaction REACTION [--json]
line react [CHAT] --message MESSAGE_ID --remove [--json]
```

- `--message` (string, required non-interactively): numeric message ID in this chat, found
  within 10 pages / 1000 messages / 30 s of history (v0.5.0; `message_not_found`,
  `lookup_incomplete` otherwise). Interactive choosers only list the latest 100.
- `--reaction` (string): `like`, `love`, `laugh`, `surprise`, `sad`, `angry`, matched
  case-insensitively, or the matching emoji (actions.go:28; messaging/actions.go:24-31;
  pkg/line/reaction.go:106-123). Emoji IDs: like=2, love=3, laugh=4, surprise=5, sad=6, angry=7.
- `--remove` (bool): removes your own reaction. Cannot be combined with `--reaction` (actions.go:54-56).
- Without `--reaction` or `--remove`, non-interactive use fails (actions.go:57-59).
- JSON action values: `react` or `remove_reaction`.
- Remote mutation, attempted once. Human success: `Reaction added.` / `Reaction removed.`

## unsend

```
line unsend [CHAT] --message MESSAGE_ID [--json]
```

- `--message` (string, required non-interactively): must be your own message, found within 10
  pages / 1000 messages / 30 s of history (v0.5.0).
- Remote mutation. Irreversible for everyone. The CLI prompts `Unsend this message for everyone? [y/N]`
  only in interactive mode (actions.go:74-82). A non-interactive call has no confirmation step, so
  the calling agent must confirm first (see safety.md).
- JSON action value: `unsend`. Human output: `Message unsent.`

## download

```
line download [CHAT] --message MESSAGE_ID --output PATH [--json]
line download CHAT --message MESSAGE_ID --output - | consumer
```

- `--message` (string, required non-interactively): attachment message, found within 10 pages /
  1000 messages / 30 s of history (v0.5.0).
- `--output` (string, required non-interactively):
  - A new path. Existing paths are never overwritten (download.go:91-93). The CLI writes a
    temp file in the same directory and hard-links it into place (download.go:97-148), so a
    failed run leaves no partial destination.
  - `-` writes the raw bytes to stdout. Refused when stdout is a terminal
    (download.go:169-171). Cannot be combined with `--json` (download.go:166-168).
- `--json` (bool): JSON summary `{path, bytes, message_id}` (download.go:151-157).
- Supported types: image (1), video (2), audio (3), file (14), each at most 20 MiB
  (files.go:28-43, 160-162). Default command deadline 2 minutes (`--timeout` overrides it).
- Not supported: messages with an external `DOWNLOAD_URL` (files.go:157-158). Other content
  types have status `unsupported`.
- Encrypted media is authenticated before any byte is written (files.go:233-238).
- Reading or downloading never registers a group key (docs/CLI.md:287).
- Local write: creates one new file at PATH. The parent directory must already exist.

## watch

```
line watch [--from-now] [--limit N] [--timeout DURATION]
```

- `--json` (bool, default true): output is always NDJSON. `--json=false` is rejected
  (watch.go:20, 30).
- `--from-now` (bool, default false): discards the saved checkpoint and starts at the current
  revision (watch.go:21, 124-131).
- `--limit` (int, default 0): stop after N emitted events. `0` = continuous (watch.go:22).
- `--timeout` (duration, default 0): e.g. `30s`. A timeout exits 0 (watch.go:37-41, 54-56).
  Negative values are rejected.
- Only one watcher may run at a time. A second one fails with `another line watch is already running`
  (internal/session/lock_unix.go:22-28). This is exit 1, not 75.
- First run starts at LINE's current revision. Later runs resume from the checkpoint.
- Stdout: one JSON event per line. Stderr: `Watching LINE events. Press Ctrl-C to stop.` and
  reconnect notices. Event shapes are in json-schema.md.
- Watch can be restarted after errors; `resync_required` means refresh chats and messages.

## login

```
line login [--qr | --email ADDRESS] [--qr-url] [--force] [--headless]
```

- Remote: contacts LINE and replaces the local session. LINE allows one Chrome-style session
  at a time, so this may sign out LINE for Chrome or another Chrome-style client
  (internal/cli/login.go:88-90; docs/CLI.md:10-12).
- `--qr` (bool): default method. Shows a terminal QR code. Requires an interactive terminal
  (login.go:57-59).
- `--email` (string): password login. Requires a non-empty address and cannot be combined with
  `--qr` or `--qr-url` (login.go:48-53). The password is read from the terminal with echo off
  (cmd/line/password.go:36-38). There is no password flag or environment variable.
- `--qr-url` (bool): prints the one-time QR URL instead of drawing a code (login.go:29).
  The URL is a sensitive secret. See safety.md.
- `--force` (bool): skips only the "replace the saved session?" question (login.go:101-139).
  It does not skip storage checks or the headless consent step (docs/CLI.md:129-132).
  Without `--force`, a saved session plus no terminal fails
  (`a LINE session is already saved; ...`, login.go:124-126).
- `--headless` (bool, Linux only): enroll host-key storage. Needs an interactive terminal and the
  explicit `yes` consent (auth.go:127-163). Not an unattended mode.
- Login success prints `Signed in as <name>. Session saved securely.` then `Next: line chats`.
- If the user keeps the existing session, login prints `Kept the existing session.` and exits 0.

## auth status

```
line auth status [--check] [--json]
```

- Local only. Does not contact LINE, does not refresh tokens, and does not prove the session
  still works (docs/CLI.md:475-477).
- `--check` (bool): tests write readiness where possible, without replacing the active session
  (auth.go:26). It can report `interactive_check_required` on macOS Keychain and Linux Secret
  Service (docs/CLI.md:478-480). On Linux it may write a temporary probe file next to the session
  (storage_prepare_linux.go:53), not a session change.
- `--json` (bool): StorageStatus object (json-schema.md). Also written on storage errors, followed
  by the matching nonzero exit code (auth.go:45-49, 54-58).

## auth migrate

```
line auth migrate --storage=headless
```

- Only valid value: `headless`. Linux only (auth.go:98-103).
- Requires an interactive terminal and a typed `yes` to accept host-only protection (auth.go:104-113).
- Local destructive: moves a native session to headless storage. Stop watchers and older
  CLI versions first (auth.go:107; docs/CLI.md:557-560).
- Does not contact LINE and does not ask for a password. Output:
  `Storage: Headless. Migration and native-key cleanup complete. After reboot: Expected; not verified.`
- Partial failure: rerun the same command. Do not delete `migration.pending` or replace `session.enc`
  (docs/CLI.md:562-566).

## update

```
line update [--check] [--json]
```

- Works without a LINE login. Contacts GitHub releases only.
- `--check` (bool): check only; never installs or takes session locks (docs/CLI.md:343-344).
- `--json` (bool): one JSON result on stdout. Use `--check --json` for a read-only check.
  Fields: json-schema.md. Human progress goes to stderr when not using `--json`.
- Without `--check`, when the status is `update_available` and the install can self-update,
  it installs. Installation requires the watcher lock and the session lock, and fails with
  guidance if either is busy (internal/cli/update.go:97-120).
- Self-update works only for official standalone installs with the `.line-cli-install` receipt
  (docs/CLI.md:354-358). Homebrew prints `brew upgrade line-cli`. Windows prints a PowerShell
  installer command to run after the command exits (docs/CLI.md:346-352).
- Exit codes: successful check exits 0 even when an update is available. Check failures and
  installation failures exit nonzero (docs/CLI.md:388-391).

## Non-interactive usage rules

1. Always use `--json` when parsing output. Parse stdout only; read stderr for diagnostics.
2. Pass the chat as a full ID (from `line chats --json` or `--show-ids`) rather than a name.
   Names must be unique and exact (chats.go:194-244). Duplicate names exit 1 with a message that
   asks you to choose a full ID.
3. `send`: use `--stdin` for multi-line text, or `--text` for short messages. Remember that
   `--stdin` keeps the trailing newline.
4. `download`: always pass `--output PATH` (new path) or `--output -` piped to a consumer with
   `set -o pipefail`. Do not use shell `>` redirection to a file that may already exist.
5. `watch`: consumers must deduplicate by `revision` (docs/CLI.md:329-330).
6. `login` cannot run without a real TTY. An agent cannot complete it. Ask the user to run it.
7. Remote mutations are never retried by the CLI. Retrying is your decision; check LINE first
   (errors.md).
