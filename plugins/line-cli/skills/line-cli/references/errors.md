# line-cli exit codes and error messages

Matches line-cli v0.5.0 (verified 2026-10-20)

Upstream sources: `cmd/line/signals.go:51-77` (exit mapping), `internal/session/storage_control.go:76-152`
(`StorageReason` and `StorageExitCode`), `internal/messaging/*.go`, `internal/cli/*.go`,
`internal/events/watch.go`. Quoted strings are copied from source. They can change between releases,
so match on the exit code first and treat text as a hint.

## Contents

- Exit codes
- How to decide what to do after a failure
- Lost response on a remote change (send, react, unsend)
- Errors by category (selection, input, attachments, login and session, storage, watch, update)
- Known upstream bugs (send e2ee version mismatch for some peers with old keys, `getContactsV2` batch failure)

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Success, help, a `--timeout` stop, or a user cancellation (`Cancelled.`). Also `Kept the existing session.` from login. | Cancellation (`ErrCancelled`, `cmd/line/signals.go:70`) means no action was taken. Check stdout for what happened. |
| 1 | Generic CLI, validation, network, or LINE error. Also a classified storage code that is not listed below (for example, no saved session). | Read the stderr message. Fix input or ask the user. See categories below. |
| 65 | Storage: invalid format, missing key, failed authentication, or protection mismatch. | Local data is damaged or the key is gone. Do not restore an old copy. Ask the user to run `line login`. Storage state is not safe to retry blindly. |
| 69 | Storage or helper unavailable or timed out (locked keyring, headless helper missing or untrusted). | Restore the environment (unlock keyring in the same D-Bus session, fix systemd sandbox). Then retry the same read or status command. |
| 74 | Uncertain durability (the file may already have changed) or failed probe cleanup. | Repeat the same local operation. Do not restore an older session copy. |
| 75 | Busy: another `line` process holds the session lock, or storage changed during login, or a helper was cancelled. | For reads, retry after a short wait. Never retry writes automatically (see below). |
| 78 | Configuration or consent required (migration needed, host consent, unsupported platform, cleanup or repair needed, interactive check required). | Needs a human. Describe the required step. Do not work around it. |
| 130 | SIGINT (Ctrl-C), or context cancelled without a signal. | Treat as interrupted. Check for partial effects before rerunning a write. |
| 143 | SIGTERM. | Same as 130. |

Signals take precedence: when a signal arrived, the exit code is 130 or 143 even if the command
also returned an error (`cmd/line/signals.go:67-69`).

Mapping source: `StorageExitCode` (`storage_control.go:135-152`). Busy (`ErrBusy`) and
`storage_changed` map to 75. `durability_uncertain` maps to 74. `authentication_failed`,
`invalid_format`, `missing_key`, `protection_mismatch` map to 65. Unavailable/timeout/headless-helper
reasons map to 69. Consent, migration, repair, and `interactive_check_required` map to 78.

Important: `line watch` on a second instance exits 1 (`another line watch is already running`), not 75.

## How to decide what to do after a failure

1. Exit 0 with `Cancelled.` on stderr: the user declined or cancelled. Nothing changed.
2. Exit 1 on a read (`whoami`, `chats`, `contacts`, `messages`, `download`, `auth status`): safe to
   show the error. Retrying a read is fine, but do not loop. Two tries is a reasonable limit.
3. Exit 75 on a read: busy. Wait a short time and retry once or twice.
4. Exit 1 or any nonzero code on a write (`send`, `react`, `unsend`, `login`): treat the outcome as
   UNKNOWN until checked. Do not retry automatically. Check first (see the procedure below).
5. Exit 65, 69, 74, 78: stop and report. These need the user (keyring, consent, reinstall login).
6. Any ambiguous name or chat choice: ask the user which one to use. Never pick one yourself.

## Lost response on a remote change

The CLI attempts each send, reaction, and unsend once. The source says so directly:
`delivery may have occurred; inspect history before retrying` (`internal/messaging/client.go:370`,
`actions.go:105`). `docs/CLI.md:260-262` repeats the instruction.

Procedure:

1. Do not rerun the write right away.
2. Read recent history: `line messages CHAT --limit 20 --show-ids --json`.
3. Look for the message you intended:
   - For `send`: your MID as `from`, the same text (or same file name), and a `created_time` close to
     when you sent it. A `send --json` result that failed has no ID to match.
   - For `react`: the target message's `reactions` contains a `fromUserMid` equal to your MID.
   - For `unsend`: the target message no longer appears in the history.
4. If it is there (or the effect is already visible), report success. Do not resend.
5. If it is absent, ask the user before any retry. Only retry on explicit confirmation.

After a `file upload did not return success` error, the message may exist without its file. Check the
message list and inform the user. Do not resend the file as a new message until the user confirms.

## Errors by category

### Chat and message selection

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `no chat or contact has that exact name; use line chats --search NAME or line contacts --search NAME --show-ids` | 1 | Search with `line chats --search NAME --show-ids`, then pass the full ID. |
| `N chats or contacts have that name; use --show-ids with line chats or line contacts and choose a full ID` | 1 | Ambiguous name. Ask the user to choose from the listed IDs. Do not guess. |
| `provide a chat ID or exact name; run line chats to find one` | 1 | Empty or control-character selector. Supply a valid one. |
| `use a LINE chat ID from line chats or line contacts` | 1 | The `id:` form or the ID does not match the expected pattern. Use the exact ID from `line chats --json`. |
| `use a numeric message ID from line messages` | 1 | Message IDs are digits only. Copy from `--show-ids` or `--json`. |
| `Message was not found in available history of the selected chat (N messages checked); older history may be unavailable.` (`message_not_found`) | 1 | react, unsend, download, `send --reply-to`: the target is not in the history LINE still serves for this chat. Check the chat and the ID; nothing was changed. |
| `Message was not found within the lookup budget (10 pages, N messages checked; ...)` / `History lookup deadline expired after ...` (`lookup_incomplete`) | 1 | The target is older than the CLI's lookup budget (10 pages / 1000 messages / 30 s). Nothing was changed. Tell the user; the action cannot reach it. |
| `only your own messages can be unsent` | 1 | Do not try again. Only your own MID can be unsent. |
| `your account changed while choosing; run the command again` | 1 | The session changed during an interactive prompt. Rerun the command. |

### Input and options

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `invalid options; run line <command> --help` | 1 | Check flag names and values against commands.md. |
| `unknown command; run line help` | 1 | Typo in the command. |
| `unexpected arguments; ...` / `unexpected command arguments` | 1 | Extra positional argument. Put options after the chat. |
| `interactive input unavailable; provide command options` | 1 | A prompt was needed but there is no terminal. Supply the missing flag. |
| `provide exactly one of --text TEXT, --stdin, or --file PATH` | 1 | Use exactly one content source. |
| `message must not be empty` | 1 | Empty or whitespace-only text. Do not send. |
| `message exceeds the CLI limit of 10000 UTF-16 units` | 1 | Shorten the text. |
| `limit must be between 1 and 100` | 1 | `messages --limit` range is 1-100. |
| `limit must be 0 or greater` | 1 | Negative `--limit`. Use 0 for all. |
| `stdin message exceeds the CLI size limit` | 1 | Input over 40,000 bytes. Split it. |
| `could not read message from stdin` / `stdin is unavailable` | 1 | Check the redirect or pipe. |
| `reaction must be like, love, laugh, surprise, sad, or angry (or the matching emoji)` | 1 | Use one of the six names. |
| `use --reaction or --remove` / `choose a reaction or --remove, not both` | 1 | Pick one. |
| `invalid user MID at --mid #N; provide a full user ID (u/U), not a group/room ID or display name` | 1 | Use a user MID (`u`/`U` prefix), not a chat ID or name. |
| `--mid cannot be combined with --search or --limit; ...` | 1 | Remove the conflicting flag. |
| `download requires --output PATH` | 1 | Supply `--output` (a new path). |
| `output already exists; choose a new path` | 1 | Never overwrite. Ask the user for a new path. |
| `--output - cannot be combined with --json; ...` | 1 | Choose binary stdout or JSON, not both. |
| `refusing to write binary data to a terminal; use --output PATH or pipe stdout to another command` | 1 | Use `--output PATH` or pipe to a consumer. |
| `attachment must be a regular file no larger than 20 MiB` | 1 | Wrong file type or too large. Not sent. |
| `attachment needs a valid file name without path separators or control characters` | 1 | Rename the file. |
| `open attachment: ...` | 1 | Path does not exist or cannot be read. |

### Sending and reacting

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `This chat doesn't support end-to-end encryption for this message/file. Nothing was sent. This send requires E2EE. ...` (`encryption_required`) | 1 | Nothing was sent: the skill passed `--require-e2ee` and LINE reports no E2EE for this chat (text and files are checked separately). Tell the user that LINE could read the message if it goes out without E2EE; only after an explicit yes run it once with `lineq.py run --yes --allow-non-e2ee -- send ...` (`broadcast.py --allow-non-e2ee` for a broadcast, which needs a new dry run). |
| `encrypted_image_unavailable` | 1 | `send --image` with encryption is disabled upstream. Nothing was sent. Use `--file` instead. |
| `contact is blocked on LINE; unblock them on your phone before sending` | 1 | Nothing was sent. The user must unblock on the phone. |
| `could not verify blocked contacts; message was not sent` | 1 | Nothing was sent. Treat as a read failure; ask before retrying the send. |
| `could not prepare encrypted message; nothing was sent; check chat membership and Letter Sealing keys` | 1 | Nothing was sent. Tell the user about the key or membership problem. |
| `could not prepare encrypted attachment; no message was sent, but an uploaded file may remain; ...` | 1 | Probably no message. An upload may remain on LINE's storage. Report it. |
| `encryption returned invalid chunks; nothing was sent` | 1 | Nothing was sent. Report. |
| `send did not return success; delivery may have occurred; inspect history before retrying: ...` | 1 | Ambiguous. Follow the lost-response procedure. |
| `send returned no message ID; delivery may have occurred; inspect history before retrying` | 1 | Ambiguous. Follow the lost-response procedure. |
| `message <id> was created but file upload did not return success; inspect LINE before retrying: ...` | 1 | The message exists but its file may be missing. Do not resend without confirmation. |
| `react did not return success; check LINE before retrying: ...` (action name varies) | 1 | Ambiguous. Check reactions on the message first. |
| `unsend did not return success; check LINE before retrying: ...` | 1 | Ambiguous. Check whether the message still exists. |
| `saved Letter Sealing keys are missing; run line login` / `could not restore Letter Sealing keys; run line login` / `own Letter Sealing key is unavailable; run line login` | 1 | Ask the user to run `line login`. Nothing was sent. |

### Reading and history

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `N messages could not be decrypted; see each message's status/error` | 1 | The JSON (or text) is still complete. Use the decrypted messages; flag `decryption_failed` ones to the user. |
| `N of M requested contacts unavailable; LINE did not return their profiles` | 1 | The JSON is partial. Use the rows with `status` `resolved`. |
| `Some sender names are unavailable; showing IDs instead.` | 0 | Warning on stderr. Output still valid. |
| `LINE chat pagination did not advance; retry the command` | 1 | Read error. Retry once. |
| `LINE chat pagination returned an empty page before completion` | 1 | Read error. Retry once. |
| `LINE returned an empty chat response` / `LINE returned no contact names` / `LINE returned an invalid contacts response` / `LINE returned inconsistent contact IDs` | 1 | Unexpected LINE response. Retry a read once, then report. |
| `LINE returned no profile` | 1 | Same as above. |
| `LINE request failed; check your connection and LINE account settings` from `chats --search` on an account with many chats | 1 | Upstream names ALL chats (inactive ones too) in batches and one failing batch fails the search, even when the network is fine. `lineq.py resolve` and `sync.py` fall back to `chats --limit 0 --show-ids` (active chats only; parsed by `linecore/chatlist.py`) and add the warning `searched active chats only (line chats --search failed upstream)`. Inactive chats are then only reachable by full ID. Do not retry the search. |
| `contacts --mid` fails for group members (`LINE request failed`), `messages.md` shows truncated `U...` IDs | 1 | Non-friend group members cannot be looked up as contacts. `sync.py` then reads names from the text output of `messages CHAT --limit 100 --show-ids` (`linecore/msgtext.py`, once per sync) and caches them in `contacts.json`. Members who did not write in the latest 100 messages stay unnamed. |

### Attachments (download)

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `download supports image, video, audio, and file messages only` | 1 | Not an attachment. Choose a message with status `attachment`. |
| `downloads from external media URLs are not supported` | 1 | Cannot download this message. Tell the user. |
| `attachment exceeds the CLI limit of 20 MiB` | 1 | Too large for the CLI. |
| `attachment is no longer available from LINE` | 1 | Object expired or removed. Tell the user. |
| `LINE is still processing this attachment; try again later` | 1 | Retry later. Downloads are reads, so a retry is allowed. |
| `attachment authentication failed; no data was output` | 1 | Integrity check failed. Do not use the data. Report. |
| `encrypted attachment has no object ID` / `invalid attachment key material` / `attachment decryption key is unavailable` / `attachment storage metadata does not match its content type` | 1 | Malformed message. Report; do not retry. |
| `download` timeout (2 minutes), `context deadline exceeded` | 1 | Retry once for a read. |

### Login and session

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `no saved LINE session; run line login` | 1 | Ask the user to run `line login` in a terminal. |
| `LINE session was logged out; run line login` | 1 | The session was invalidated by LINE. The user must log in again. |
| `LINE session was invalidated or replaced by another client; run line login` | 1 | Kind `session_replaced`. LINE allows one Chrome-style session per account: another computer running line-cli, or LINE in Chrome, took over. Ask the user before logging in again, because that signs the other device out. Not retryable. |
| `a LINE session is already saved; run login in a terminal to confirm replacement, or use --force` | 1 | Non-interactive login refuses to replace a session. Ask the user. Use `--force` only with explicit approval. |
| `QR login and headless enrollment require an interactive terminal. LINE was not contacted` | 1 | Login cannot run from an agent. Ask the user to run it. |
| `login requires an interactive terminal for password input` | 1 | Email login also needs a terminal. |
| `--email cannot be combined with --qr or --qr-url. LINE was not contacted` | 1 | Pick one login method. |
| `unset QRCODE_DEBUG before QR login, or use line login --qr-url` | 1 | Unset the variable. Do not paste a QR URL into chat. |
| `This terminal is too narrow to show a scannable QR code. Widen it ... or run: line login --qr-url.` | 1 | User action: widen the terminal, or use `--qr-url` in a trusted tool only. |
| `Login did not complete; your saved session was not changed` | 1 | Safe: old session intact. Ask the user to retry. |
| `Kept the existing session.` | 0 | The user chose not to replace the saved session. |
| `saved storage changed during login; run login again` | 75 | Rerun login in a terminal. |
| `session storage changed but its durability could not be confirmed` | 74 | Repeat the same local operation. Do not restore an older copy. Check `line auth status --check`. |
| `native storage is unavailable or locked` | 69 | Unlock the Secret Service keyring in the same D-Bus session, then retry. |
| `session encryption key is missing; restore keyring access or run line logout before signing in again` | 65 | Ask the user; logout only with explicit approval. |
| `a native session exists; run line auth migrate --storage=headless` | 78 | Needs the user's decision on migration (it asks for the typed `yes`). |
| `headless storage is available only on Linux` | 78 | Not supported on this OS. |
| `storage metadata needs repair; existing data was preserved` | 78 | Ask the user. Do not delete files. |
| `local logout cleanup is incomplete; run line logout again` | 78 | Repeat `line logout` (safe). |
| `storage migration needs completion; run line auth migrate --storage=headless again` | 78 | Rerun the same migrate command. Do not delete `migration.pending`. |
| `this native backend cannot prove write readiness without a possible unlock prompt; use interactive line login` | 78 | Ask the user to run `line login` interactively. |
| `user-scoped systemd credential storage is unavailable` | 69 | Environment problem. Report. |
| `systemd credential operation failed; check credential broker access, service sandboxing, ...` | 69 | Report. Check sandbox settings. |
| `systemd credential protection does not match the required policy` | 65 | Report. Ask the user. |

`auth status --json` still prints its JSON status object when storage is unavailable, then exits with
the matching code. Read `reason` in that object (see json-schema.md).

### Watch

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `Watching LINE events. Press Ctrl-C to stop.` | 0 | Informational. |
| `LINE stream disconnected; reconnecting in <delay>…` | 0 | Informational. The watcher is retrying. |
| `another line watch is already running` | 1 | Only one watcher is allowed. Stop the other one or use it. |
| `LINE login changed; restart line watch` | 1 | Restart the watcher. |
| `LINE reported a stream error; restart line watch` | 1 | Restart the watcher. |
| `LINE stream authentication failed after token refresh; saved credentials were retained; retry line watch` | 1 | Retry the watcher once. If it fails again, ask the user to log in. |
| `could not write watch output; the event will replay on restart` | 1 | Consumer pipe closed. Restart; duplicates are possible. |
| `invalid LINE operation event; resume position was not advanced` / `unsupported LINE stream event; ...` | 1 | Unexpected protocol event. Report. Do not skip it. |

### Update

| Stderr message (abridged) | Exit | What to do |
| --- | --- | --- |
| `Checking for updates...` | 0 | Informational. |
| `cannot acquire the watcher lock: ...; stop line watch and its service before updating, then try again` | 1 | Stop the watcher first. |
| `cannot acquire the session lock: ...; stop other LINE CLI commands before updating, then try again` | 1 | Wait for other commands to finish. |
| `The update was installed, but its durability could not be confirmed; run line version to check the installed version` | 1 (inferred, unverified) | Run `line version` before doing anything else. This error is not a `session` storage error, so `StorageReason` should classify it as `storage_error` (exit 1). |
| `updated_unconfirmed` status in `--json` | 1 (inferred, unverified) | Same as above. |
| Windows: `status` `update_available`, `can_self_update` false | 0 | The CLI does not replace itself on Windows. Give the user the printed PowerShell command to run after this exits. |

## Known upstream bugs

Confirmed live on line-cli v0.4.0 with a debug build; not re-tested on v0.5.0. Decision: parked, no
upstream issue or PR for now. Both hide their cause behind the generic `LINE request failed; check your connection and LINE
account settings`, so a "connection" message here is usually not a connection problem.

### `send` to some peers with old Letter Sealing keys fails (e2ee version mismatch)

Scope: `send` works in general (verified live 2026-10-13 on Windows, line-cli v0.4.0: a text message to
a direct chat was delivered end-to-end encrypted, via `lineq.py run --yes -- send <id> --stdin --json`).
Re-verified live 2026-10-20 on line-cli v0.5.0 in a group: plain text, `--mentions` (a user span after
an emoji, and `all:true`), and `--reply-to` an older message, all end-to-end encrypted and read back
with `messages --details --json`. Only the case below fails.

- **Symptom:** `line send` to certain direct (1:1) chats (peers with an old key version) fails with exit 1. LINE answers HTTP 400,
  envelope code 10051, TalkException code 86 `sender or receiver not supported e2ee or version
  mismatch`. The CLI shows only the generic message, prefixed `send did not return success; delivery
  may have occurred; inspect history before retrying`. Nothing is delivered. The official LINE apps
  can message the same person.
- **Cause:** `negotiateE2EEPublicKey` returns a peer key with `e2eeVersion` 0 (the peer's Letter Sealing
  key is the old version, not V2), while the CLI always encrypts as V2. Upstream does not fall back to another
  version and does not surface the real error.
- **What the skill does:** tell the user plainly that the message was NOT sent. Do not retry and do
  not try other chats or formats. Suggest sending it from the LINE app. If unsure, read recent
  messages (`messages CHAT --limit 20 --show-ids --json`, see "Lost response") to confirm nothing
  went out. Reads from that chat are unaffected.

### `getContactsV2` rejects the whole batch (`chats --search`, `contacts --mid`)

- **Symptom:** `chats --search` or `contacts --mid` fails with the generic `LINE request failed`
  although the network and login are fine.
- **Cause:** for deleted accounts and non-friend group members LINE returns userStatus-only stubs
  (no contact object). Upstream rejects the entire batch when one stub is present.
- **What the skill does (already worked around):** `lineq.py resolve` and `sync.py` fall back to the
  active-chat listing (`linecore/chatlist.py`, warning `searched active chats only`) and read group
  sender names from the `messages` text output (`linecore/msgtext.py`). See "Reading and history".

## Quick reference: what not to retry

- Writes: `send`, `react`, `unsend`, file upload, `login`, `logout`, `auth migrate`, `update`
  install. Never loop on these. Check state first, then ask the user.
- Reads: `whoami`, `contacts`, `chats`, `members`, `messages`, `download`, `watch` (reconnects itself), `auth status`,
  `update --check`. A single bounded retry is fine. Exit 75 on a read is the only case that calls for waiting.
