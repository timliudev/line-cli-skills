# line-cli JSON output schema

Matches line-cli v0.4.0 (verified 2026-10-12)

All `--json` output goes to stdout as one JSON document (pretty-printed, 2-space indent),
except `watch`, which writes NDJSON (one object per line). Diagnostics go to stderr.
Field names are exact. Upstream sources: `internal/cli/*.go`, `internal/messaging/*.go`,
`internal/events/watch.go`, `pkg/line/structs.go`, `pkg/line/reaction.go`,
`internal/update/update.go`, `internal/session/storage_control.go`.

## Contents

- Conventions
- whoami
- contacts (list)
- contacts --mid
- chats
- messages (item)
- send
- react and unsend
- download
- watch events
- auth status
- update --check (and update --json)
- content_type map
- Error output with JSON

## Conventions

- Empty results are `[]`, never `null` (for example `messages` with no messages).
- `created_time`, `unread_count`, and `atMillis` are bare JSON numbers, not strings.
- `revision` in watch events is a string (decimal digits).
- Optional fields marked "omitempty" are absent when empty or zero. Do not assume they exist.
- Parse by field name. Do not depend on field order.

## whoami

`line whoami --json` writes the full LINE profile object (`pkg/line/structs.go:48-62`):

| Field | Type | Notes |
| --- | --- | --- |
| `mid` | string | Your user MID (the ID used as `from` on your messages). |
| `userid` | string | LINE user ID (not your MID). |
| `regionCode` | string | |
| `displayName` | string | |
| `statusMessage` | string | |
| `allowSearchByUserid` | bool | |
| `allowSearchByEmail` | bool | |
| `picturePath` | string | |
| `statusMessageContentMetadata` | object (string to string) | May be null. |
| `nftProfile` | bool | |
| `profileId` | string | |
| `profileType` | int | |
| `createdTimeMillis` | string | Decimal milliseconds. |

## contacts (list)

`line contacts --json` (no `--mid`) writes an array of `line.Contact` objects, sorted by
effective display name then MID (`internal/cli/contacts.go:132-138`). Keys:

| Field | Type | Notes |
| --- | --- | --- |
| `mid` | string | User MID. |
| `displayName` | string | Profile name. |
| `displayNameOverridden` | string | Your custom name. Empty if none. |
| `statusMessage` | string | |
| `picturePath` | string | |

The list output has NO `effectiveDisplayName` and NO `status`. To get the effective name,
use `displayNameOverridden` if non-empty, else `displayName` (`structs.go:211-217`).

## contacts --mid

`line contacts --mid MID [--mid MID ...] --json` writes an array (even for one ID). Each
item has all five fields above, plus:

| Field | Type | Notes |
| --- | --- | --- |
| `effectiveDisplayName` | string | Custom name, else profile name. Empty when no name. |
| `status` | string | `resolved` if LINE returned a profile; `unavailable` if LINE omitted that MID. |

- Output order = input order, with exact duplicates removed (only the first occurrence appears).
  Match rows by `mid`.
- An `unavailable` row has `mid` set and empty name/profile fields.
- A `resolved` row can still have an empty name. Fall back to the MID.
- If any row is `unavailable`, the full array is still written, then the command exits 1
  (`internal/cli/contacts.go:223-225`). Treat the JSON as a partial result.
- A network, auth, or malformed-response error produces no array.

## chats

`line chats --json` writes an array of chat objects.

| Field | Type | Present when |
| --- | --- | --- |
| `id` | string | Always. Chat/room/group/user ID. |
| `type` | int | Always. LINE `midType` from the message box, not mapped by the CLI. |
| `unread_count` | number | Always. Defaults to `0` if LINE omits it. |
| `name` | string | Only with a non-empty `--search` and a non-empty name (omitempty). |
| `updated_at` | int (ms since epoch) | Only with a non-empty `--search` and non-zero activity time (omitempty). |

**Important:** `line chats --json` WITHOUT a non-empty `--search` is the legacy shape. It
returns only `id`, `type`, and `unread_count` (`internal/cli/chats.go:54-65`). There is no
`name` and no `updated_at`. To get names and activity time, add `--search TEXT`.

Prefix-based kind (human output, and useful when reading `id`): `u` = Direct, `c` = Group,
`r` = Room (`chats.go:125-139`).

## messages (item)

`line messages CHAT --json` writes an array of message items (`internal/messaging/client.go:110-123`).

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Numeric message ID. Use it with `react`, `unsend`, `download`, `send --reply-to`. |
| `from` | string | Sender MID. Your own MID for messages you sent. |
| `to` | string | Recipient or chat ID as LINE reports it. |
| `created_time` | number | Milliseconds since epoch. `0` when LINE omits it. |
| `content_type` | int | See the content_type map below. |
| `text` | string | Decoded text for `plaintext` and `decrypted`. Empty otherwise. |
| `encrypted` | bool | True when the message had encrypted chunks or an `e2eeVersion` marker. |
| `status` | string | One of: `plaintext`, `decrypted`, `decryption_failed`, `attachment`, `unsupported`. |
| `file_name` | string | Omitempty. Set only for `content_type` 14 (file). Filename as LINE reports it. |
| `error` | string | Omitempty. Set only for `status` `decryption_failed`. Free text; do not match exactly. |
| `reply_to` | string | Omitempty. ID of the message this one replies to. |
| `reactions` | array | Omitempty. See the reaction shape below. |

### status values

| Status | Meaning | Downloadable? |
| --- | --- | --- |
| `plaintext` | Unencrypted text (`content_type` 0). | No |
| `decrypted` | Encrypted text, decrypted successfully. | No |
| `decryption_failed` | Encrypted, could not decrypt. `error` explains why. The command then exits 1 (`messages.go:216-218`). | No |
| `attachment` | `content_type` 1, 2, 3, or 14. Use `line download`. | Yes (if under 20 MiB and not an external URL) |
| `unsupported` | Any other `content_type` (for example 7, 13, 22). `text` is empty. | No |

Note: `attachment` and `unsupported` messages have an empty `text`. Show the `content_type`
meaning instead.

Error text examples (free text, may change): `letter sealing keys unavailable; run line login`,
`group key unavailable for this historical message`, `invalid encrypted message chunks`,
`letter sealing decryption failed; the required device keys may be unavailable`.

### reaction shape (inside `reactions`)

```
{
  "fromUserMid": "<mid>",
  "atMillis": 1760000000000,
  "reactionType": { "predefinedReactionType": 3 }
}
```

- `reactionType.predefinedReactionType`: 2 like, 3 love, 4 laugh, 5 surprise, 6 sad, 7 angry
  (`pkg/line/reaction.go:106-123`).
- `reactionType.paidReactionType` (omitempty): object with `productId`, `emojiId`,
  `resourceType` (omitempty), `version` (omitempty).

## send

`line send CHAT ... --json` writes one object (`internal/messaging/client.go:230-236`):

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Message ID assigned by LINE. |
| `chat_id` | string | Resolved chat ID. |
| `encrypted` | bool | False only when Letter Sealing was unavailable for the chat. |
| `group_key_registered` | bool | True only if this send registered a new group key. |
| `request_sequence` | int | Persisted request sequence for this send. |

## react and unsend

Both write one object (`internal/messaging/actions.go:33-38`):

| Field | Type | Notes |
| --- | --- | --- |
| `action` | string | `react` (add or change reaction), `remove_reaction`, or `unsend`. |
| `chat_id` | string | Resolved chat ID. |
| `message_id` | string | Target message ID. |
| `request_sequence` | int | |

## download

`line download CHAT --message ID --output PATH --json` writes (`internal/cli/download.go:151-157`):

| Field | Type | Notes |
| --- | --- | --- |
| `path` | string | The `--output` value as given. |
| `bytes` | int | Number of bytes written. |
| `message_id` | string | |

`--json` cannot be combined with `--output -` (binary stdout).

## watch events

`line watch` writes one event per line (NDJSON). Source: `internal/events/watch.go:19-25`.
Three shapes exist. Consumers should switch on `event`.

### `message`

```
{"event":"message","revision":"123456","type":26,"chat_id":"u...","message":{ ...message item... }}
```

| Field | Type | Notes |
| --- | --- | --- |
| `event` | string | `"message"`. |
| `revision` | string | Decimal digits. Use it to deduplicate. |
| `type` | int | LINE operation type. Always present for `message` events, 25 (sent) or 26 (received). |
| `chat_id` | string | For type 25: the message's `to`. For type 26 with `toType` 0 (direct): the sender's `from`. Otherwise `to`. |
| `message` | object | Same shape as a `messages` item (see above). |

### `operation`

```
{"event":"operation","revision":"123457","type":5}
```

| Field | Type | Notes |
| --- | --- | --- |
| `event` | string | `"operation"`. |
| `revision` | string | |
| `type` | int | LINE operation type. Omitted (omitempty) when 0. |

There are no `chat_id` or `message` fields on `operation` events. Any operation that is not a
message (or a message op with no message body) appears in this form. Look up the meaning of
`type` yourself; the CLI does not translate it.

### `resync_required`

```
{"event":"resync_required","revision":"123460"}
```

LINE reported a gap. Refresh chats and messages (`line chats --json`, `line messages ... --json`)
before trusting later events. Only `event` and `revision` are present.

## auth status

`line auth status --json` (and `--check --json`) writes the StorageStatus object
(`internal/session/storage_control.go:48-63`):

| Field | Type | Notes |
| --- | --- | --- |
| `schema` | int | Currently `1`. |
| `configured` | string | Session state. Observed values: `present`, `none`, `unknown`, `cleanup_pending`. |
| `backend` | string | Observed: `native`, `headless`, `unknown`. |
| `email` | string | Omitempty. Account email, if one was saved. |
| `protection` | string | Observed: `unverified` (not yet verified), `host-user` (Host key; no TPM), `host-tpm2-user` (Host key + TPM binding). The human output relabels these (`internal/cli/auth.go:60-69`). |
| `protection_verified` | bool | True only after successful decryption. |
| `fixed_pcr_mask` | int | Omitempty. TPM-related (unverified: which hosts populate it). |
| `pcr_bank` | int | Omitempty. |
| `signed_pcr_mask` | int | Omitempty. |
| `read_access` | string | Observed: `available`, `unavailable`, `not_applicable`, `unknown`. |
| `write_access` | string | Observed: `available`, `unavailable`, `unknown`, `not_checked`. |
| `after_reboot` | string | `not_verified` or `expected_not_verified`. Never a promise. |
| `line_validity` | string | Always `not_checked`. Local status never proves the LINE session works. |
| `reason` | string | Machine code. See the list below. |

`reason` values (from `StorageReason`, `storage_control.go:76-131`): `ok`, `durability_uncertain`,
`busy`, `no_session`, `cleanup_pending`, `migration_pending`, `native_key_shared`, `storage_changed`,
`host_consent_required`, `migration_required`, `unsupported_platform`, `interactive_check_required`,
`unsafe_storage`, `repair_required`, `probe_cleanup_failed`, `cancelled`, `helper_timeout`,
`helper_output_limit`, `protection_mismatch`, `authentication_failed`, `invalid_format`, `missing_key`,
`headless_helper_untrusted`, `headless_unavailable`, `storage_unavailable`, `storage_error`.

`auth status --json` still writes this object when storage is unavailable, then exits with the
matching storage exit code (see errors.md).

## update --check (and update --json)

`line update --check --json` (and `line update --json` after installation) writes the Plan object
(`internal/update/update.go:19-30`):

| Field | Type | Notes |
| --- | --- | --- |
| `current_version` | string | The version that started this command. |
| `latest_version` | string | Latest stable GitHub release tag. After `updated`, the installed version. |
| `status` | string | Check: `update_available`, `up_to_date`, `ahead`, `unknown_version`. Install: `updated`, `updated_unconfirmed`, `failed`. |
| `installation` | string | `standalone`, `homebrew`, `source`, or `unknown`. |
| `executable` | string | Path of the executable the CLI found. |
| `can_self_update` | bool | Describes support, not whether a newer release exists. Only standalone macOS/Linux builds are true. Windows is always false. |
| `release_url` | string | |
| `upgrade_command` | string | Omitempty. E.g. `brew upgrade line-cli`. |
| `instructions` | string | Omitempty. Human-readable next step. |

- `updated_unconfirmed`: replacement happened but the directory could not be synced. Run
  `line version` before doing anything else.
- `failed`: installation was attempted and failed. The JSON is written before the nonzero exit.
- A failed initial check writes no JSON at all (only stderr and a nonzero exit).

## content_type map

From `internal/cli/display.go:18-27` (labels) and `internal/messaging/files.go:28-43` (downloadable).

| content_type | Meaning | Status it gets | Downloadable? |
| --- | --- | --- | --- |
| 0 | text | `plaintext` / `decrypted` / `decryption_failed` | No |
| 1 | image | `attachment` | Yes |
| 2 | video | `attachment` | Yes |
| 3 | audio | `attachment` | Yes |
| 7 | sticker | `unsupported` | No |
| 13 | contact | `unsupported` | No |
| 14 | file | `attachment` (`file_name` set) | Yes |
| 22 | rich message | `unsupported` | No |
| other | unknown | `unsupported` | No |

Downloadable = `status` `attachment`. Images, video, audio, and files are downloadable up to
20 MiB, and only if the message has no external `DOWNLOAD_URL`.

## Error output with JSON

Errors are never written as JSON. Stdout stays empty (or contains only the partial result described
above, for `messages`, `contacts --mid`, `update`, and `auth status`). The error line goes to stderr
as `line: <message>`. Check the exit code (see errors.md).
