# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.2.0] - 2026-10-15

### Added
- Works in any Agent Skills-compatible agent (Codex, Gemini CLI, Cursor, Copilot, OpenCode, ...),
  installed with `npx skills add timliudev/line-cli-skills`. README and GUIDE (4 languages) get
  "Option C: other agents" (global vs project scope, `npx skills update`, never install the same
  skill both ways on one machine) and two troubleshooting rows (duplicate skills, "scripts not
  found" outside Claude Code). Claude Code plugin install and user-skill install are unchanged.
- `tests/test_skill_portability.py`: `${CLAUDE_SKILL_DIR}` may appear only on the single
  `SKILL_DIR:` line of each SKILL.md; every command uses `<SKILL_DIR>/scripts/...`, each referenced
  script exists, descriptions stay agent-neutral and within 1024 characters.
- `scripts/broadcast.py`: send one message (text via `--text`/`--stdin`, optionally a `--file`) to a
  list of chats (one name or full chat ID per line, `#` comments and blank lines ignored). Dry run by
  default: every entry is resolved (only an exact name from a complete lookup or a full ID counts; partial, ambiguous,
  `incomplete` (a search failed, so a second chat of that name may be hiding: use the full chat ID,
  which always works) and unmatched entries are listed under `unresolved` and the exit code is not 0, nothing is sent) and
  the JSON shows the resolved recipients, the exact text and a `plan` hash (recipient IDs, text and
  the SHA-256 of an attachment's bytes). `--yes` requires `--plan HASH` (exit 2 without it) and is
  refused if the list, text or file changed; it sends sequentially with `--delay` seconds
  between recipients (default 3, at most `--max-recipients`, default 50), stops at the first
  failure, never retries, and reports every recipient as `sent`, `failed` or `not_attempted`
  (a failed one says whether it may still have been delivered). SKILL.md requires a dry run, showing
  the user the full list and text, an explicit yes, and never re-running a failed broadcast.
- `scripts/watch.py`: long-running live archiver. Runs `line watch --json`, appends each `message`
  event of an archived chat to the same archive as `sync.py` (de-duplicated by message ID), then
  syncs that chat (attachments, `messages.md`). Syncs the archived chats at start and on
  `resync_required` (catch-up). **Privacy default:** only chats that already have an archive folder,
  or are listed under `[watch] archive_chats` / passed with `--archive NAME_OR_ID`, are archived;
  events of every other chat are ignored (nothing written, nothing logged, counted as
  `ignored_messages`). `--archive-all` is an explicit opt-in that archives every chat. `--max-restarts` defaults to 10
  consecutive fast failures (a run that lasted a minute resets the count), so a crash loop ends
  while a healthy watcher runs forever.
  **No `line` call for plain text:** a new message is appended from the event and the chat's
  `messages.md` is re-rendered from the local files (names cache, downloaded files, emoji cache), so
  LINE's session lock stays free for `send`. The full sync runs only for an attachment to download,
  an unknown sender or chat name (once each), at start and on `resync_required`, with a 2 s pause
  between chats. Default `--settle` 15 s, at most 2 min while a chat stays busy. The set of archive
  folders is re-read at most every 5 s (a folder deleted while running is not recreated unless the
  chat is on the allowlist), and sender names are remembered in memory. Restarts `line watch` with exponential backoff (2 s up to 2 min) and logs to stderr or
  `--log FILE`; exits with a clear error (exit 3) when another `line watch` already runs, and
  stops on a lost login or missing `line`. Ctrl-C / SIGTERM / `--duration` stop it cleanly with a JSON
  summary. New `linecore.runner.start_line` helper.
- VIP notifications: new config table `[watch]` with `vip_chats` and `archive_chats` (chat names or
  full IDs, no secrets) and `--vip` / `--archive` for `watch.py`. VIP chats notify even when not
  archived, and nothing is written to disk for them then. A fresh incoming message (not sent by you, not older than
  15 minutes) in a VIP chat shows a Windows toast (PowerShell, stdlib only) with chat, sender and a
  short preview; other systems, or a failed toast, get one stderr line with the chat and sender but
  never the message text. The toast PowerShell runs without an execution-policy bypass and with a
  minimal environment (no API keys or tokens from the parent). `--no-notify` disables it. VIP and archive entries must be exact names
  or full IDs (a partial-name match is refused with a warning).
- `references/watch.md`: broadcast and watch in detail, running `watch.py` on Windows with `pythonw`
  or Task Scheduler at logon, a scheduled `sync.py --all` fallback with `schtasks` (commands
  are documented only; no task is created), and the note that a `send` can be refused with `busy`
  while `watch.py` syncs: that proves nothing was sent; tell the user, wait, and send again only
  after they confirm (also in SKILL.md and `references/safety.md`).

### Changed
- Portable script paths: SKILL.md and `references/` no longer use `${CLAUDE_SKILL_DIR}` in commands
  (only Claude Code substitutes it, and only inside SKILL.md). Each SKILL.md has a "Skill folder"
  note with a `SKILL_DIR: ${CLAUDE_SKILL_DIR}` line (Claude Code fills it in; other agents use the
  folder the skill was loaded from) and every command is written `python "<SKILL_DIR>/scripts/x.py"`.
- Agent-neutral wording: "the Read tool" became "your file-viewing tool (Claude Code: Read)",
  the media-digest hand-off names the skill as `media-digest` (Claude Code plugin name
  `media-digest:media-digest`), and the frontmatter descriptions no longer say "Claude".
  The confirmation rules for write actions are unchanged. Python: `python`, else `python3` / `py -3`.
- Plugin versions bumped to 0.2.0.
- Docs: clarify that `/plugin` commands run inside Claude Code; add terminal alternative (`claude plugin ...`).
- Archive folders are matched to a chat by the whole chat-ID segment (`<name>__<id>`, bare `<id>`,
  old `<id>__<name>`), never by prefix or suffix: a chat whose name looks like another chat's ID can
  no longer be mistaken for that chat or merged into its archive.
- `sync.py` summary: new `gap` field (`null`, or `after_id`, `before_id`, `from`, `to` as UTC times)
  next to `gap_possible`. The warning now names the unrecoverable time range and the fix (sync more
  often or run `watch.py`). Additive; `state.json` is unchanged.
- All scripts tolerate missing stdout/stderr (`pythonw`, Task Scheduler): `runner.configure_stdio`
  replaces them with a null device.
- Docs: SKILL.md, `references/archive.md`, `references/safety.md`, README and GUIDE (4 languages;
  new GUIDE section "Broadcast, live archive and notifications").

## [0.1.2] - 2026-10-14

### Added
- `scripts/thread.py`: offline "who replied to / reacted to message X". Finds the target by message ID
  or by text (and sender), and returns its replies (messages whose `reply_to` points at it, optionally
  nested with `--depth`) and reactions, with sender names and reaction labels. Several matches exit 3
  with candidates, no match exits 4. SKILL.md, `references/archive.md`, the guide (4 languages) and
  the README feature list describe it. Only messages that are in the archive can be found.
  Your own messages show as `Me`: sync now records your MID as `self_mid` in `state.json`.
- Shared LINE emoji cache: sync saves each distinct LINE emoji reaction image once under
  `<archive_root>/_line_emoji/<productId>/<emojiId>.png` (stdlib download, safe ids only, 10 s timeout,
  1 MiB cap, PNG check), reused by every chat. Failures are recorded in `_line_emoji/index.json` and
  retried at most every 7 days. New setting `download.line_emoji` (default `true`). The image URL is an
  unofficial pattern; animated sticons are untested.
- `scripts/emoji.py`: `list [--unlabeled]` and `label PRODUCT_ID/EMOJI_ID "text"` keep short labels in
  `index.json`, so Claude looks at an image once. `messages.md` shows a labelled LINE emoji reaction as
  `LINE emoji "<label>"`, linking the local cached file and falling back to the remote URL.

### Changed
- `references/archive.md`: new "Stability for integrations" section. The `sync.py` options and
  summary JSON fields, the chat folder layout, the rule that non-chat directories in `archive_root`
  start with `_`, and reading `messages.jsonl`/`contacts.json` instead of `messages.md` are stable;
  changes are additive unless marked BREAKING here.
- Docs: sticker messages (content type 7) are only shown as unsupported content because line-cli does
  not expose which sticker was sent; built-in reactions map to six emoji.
- Plugin versions bumped to 0.1.2.

## [0.1.1] - 2026-10-13

### Changed
- Docs: sending verified working (a text message to a direct chat was delivered end-to-end encrypted,
  live on Windows with line-cli v0.4.0). The upstream TalkException 86 failure only affects some peers
  with old Letter Sealing keys. Replying (`--reply-to`) shares the send path but was not verified
  separately; reacting and unsending are still untried live.
- SKILL.md: new "Replying to a specific message" flow (sync, find the target in the local archive,
  confirmation shows the quoted original); example prompts added to the guide in all four languages.
  Any message type can be the target (text, photo, video, voice, file, sticker) and a reply can itself
  be a file (`send --file PATH --reply-to ID`). `--reply-to` does not check the latest-100 window (that
  limit applies to react/unsend/download); whether LINE renders a quote for very old targets is not verified.
- Known limit documented: @mentions are not supported (upstream has no MENTION metadata); "@name" is
  sent as plain text, not highlighted, no notification.
- Plugin versions bumped to 0.1.1.

## [0.1.0] - 2026-10-13

First release. This is early software: the list below says exactly what has been tested and how.

### Status
- **Tested live** (a real LINE account, Windows only): guided install, login, chat-name resolution
  (`lineq.py resolve`), sync and archive (`sync.py`), attachment downloads, group names.
- **macOS and Linux:** covered by unit tests only; not tried on a real machine.
- **`send` is blocked by an upstream bug** (line-cli v0.4.0, LINE error 10051 / TalkException 86,
  see Known issues). Sending, reacting and unsending were not verified live; they are covered by
  unit tests only.
- **media-digest:** keyframe extraction was tested on synthetic media generated with ffmpeg. The
  speech-to-text adapters (`faster_whisper`, `whisper_cpp`, `openai_compat`, `command`) have **not**
  been run against a real engine or service yet; their unit tests use fakes.
- **Evals:** the 7 plugin evals were run once each (3 runs per case, 2026-10-12) and all passed.

### Added
- Marketplace manifest, `line-cli` and `media-digest` plugin manifests, manifest tests and CI.
- Plugin evals (M4) in the official layout `plugins/<plugin>/evals/`: skill triggering, no trigger
  for LINE OA questions, confirm-before-send/unsend, no retry after a timeout, login warning.
  How to run and cost guidance: `docs/EVALS.md`.
- `line-cli` plugin:
  - `setup.py` (`--check`, `--install`, `--doctor`): detects `line`, plans and runs a pinned,
    checksum-verified install per OS after user consent, never runs `sudo`, diagnoses problems in
    plain language.
  - `login.py`: opens a terminal window for `line login` (QR or email, password never seen by
    Claude), polls `whoami`, falls back to a manual command when no window is available.
  - `lineq.py`: `resolve NAME` (chat/contact name to full ID, ambiguity returned to the user) and
    `run` (unified JSON result with classified errors; busy retries for reads only; state-changing
    commands need `--yes`; writes are attempted once).
  - `linecore` (config, `line` lookup, error classification), `config.toml` with `line_path` and
    reserved archive keys.
  - `SKILL.md` workflow (first run, reading, attachments, confirmed writes) and `references/`
    (commands, JSON schema, errors, safety, setup) matching line-cli v0.4.0.
  - Sync and local archive (M3): `sync.py CHAT|--all` fetches the latest 100 messages, de-duplicates
    by message ID into `messages.jsonl`, regenerates a readable `messages.md` (sender names via
    cached `contacts --mid` lookups, relative attachment links) and keeps `state.json`. New
    attachments are downloaded right away (`download.types`, `download.max_bytes`, `--types`,
    `--max-bytes`, `--no-download`) with extensions corrected from magic bytes; expired, external
    and too-large ones are marked, not retried forever. Detects possible gaps and possibly unsent
    messages (kept, marked), follows chat renames, warns when the archive is inside a git
    repository. Archive location: `LINE_SKILL_ARCHIVE`, `archive_root` or a per-OS default.
    `SKILL.md` now syncs first and reads `messages.md`; new `references/archive.md`.
- `media-digest` plugin:
  - Core: `digest.py` turns video into scene-based keyframes with timestamps and audio into a
    transcript, merged into `digest.md` and `digest.json`; images and PDFs pass through; results
    are cached; missing ffmpeg or ASR degrades gracefully with install hints.
  - ASR adapters and routing: `faster_whisper`, `whisper_cpp`, `openai_compat` (OpenAI, Groq, vLLM,
    Speaches presets) and `command` adapters; fallback order and per-language routes in
    `config.toml`; API keys by environment variable name only; `python -m mdcore.asr list` probes
    backends; local-first, cloud only when configured.
  - `SKILL.md` and `references/` (`config.md`, `adapters.md` with a task-queue example,
    `install.md`).
- Multilingual documentation (English, zh-TW, ja, th): README and user guide, including the
  disclaimer, with tests for existence, language switchers and equal section structure.
- CI `docs-sync` check: pull requests that change `plugins/**` or `.claude-plugin/**` must also
  change documentation (label `no-docs` to bypass).
- Release notes: `docs/RELEASE_NOTES_v0.1.0.md`.

### Changed
- Archive folders are now named `<chat name>__<chat_id>` (name first, full ID kept intact, name
  part truncated first). Existing `<chat_id>` and `<chat_id>__<name>` folders are found by ID and
  renamed automatically on the next sync, contents untouched.
- `messages.md`: content type 18 reads `[System event (type 18)]`, other unsupported types read
  `[Unsupported content (type N)]`, and a "no messages for X days (from -> to)" line marks silences
  longer than the new `render.quiet_gap_days` setting (default 3, `0` disables).
- `lineq.py resolve`: a single partial-name match (no exact match) now resolves directly
  (`status: resolved`, `matched_by: "substring"`) instead of asking the user to pick from one
  option. Two or more matches still return `candidates`.

### Fixed
- Duplicate archive folders for one chat (`<name>__<id>` plus an old `<id>__<name>` or bare `<id>`,
  e.g. from a concurrent run of older code) no longer make sync keep writing to the old one and warn
  "could not rename ... target exists": sync now merges them losslessly into the canonical folder
  (messages unioned by ID with the more complete copy winning, `state.json` merged, `files/` moved
  with identical files deduplicated and conflicting names kept with a suffix) and removes only
  folders that end up empty; anything unexpected is left in place with a warning.
- Reactions in `messages.md` no longer all read `reaction (Me)`: built-in reactions render as emoji
  plus name with who and when (`❤️ love (Alice, 14:05)`), LINE emoji reactions as
  `LINE emoji <productId>/<emojiId>` with a link to the public sticon image, unknown shapes as
  `reaction (type unknown)`.
- Tests: Windows installer PATH helper uses Windows path rules on every OS, and `setup --check` tests no longer depend on the CI host's keyring (Linux/macOS CI).
- `sync.py <CHAT_ID>` without `--name` no longer leaves the archive folder and the `messages.md`
  title as the bare ID: the name is looked up (contacts for direct chats, one cached
  `chats --limit 0 --show-ids` call for groups and rooms) and an id-only folder is renamed. If the
  lookup fails the folder stays id-only, silently.
- `lineq.py resolve` no longer dies when upstream `chats --search` fails (it names all chats,
  including inactive ones, and one failing batch fails the call): it falls back to the parsed text
  listing `chats --limit 0 --show-ids` (active chats only, new `linecore/chatlist.py`) and warns
  "searched active chats only".
- `sync.py --all` names archive folders of active chats from the same listing.
- Group sender names: when `contacts --mid` cannot resolve a non-friend member, sync reads names
  from `messages CHAT --show-ids` text (new `linecore/msgtext.py`) and caches them in `contacts.json`.
- `lineq.py run` forwards stdin automatically when the `line` arguments contain `--stdin`, instead
  of silently sending empty input ("message must not be empty").
- Windows install under Git Bash: `_windows_installer_env` puts System32 first on PATH so the
  installer's `tar.exe` is Windows' bsdtar, not Git's GNU tar; covered by unit tests.
- Documentation audit against the code: `references/archive.md` no longer says groups synced through
  `--all` stay unlabeled (they are named from the active-chat listing); `SKILL.md` shows the correct
  `lineq.py run -- watch --timeout 30s` form (lineq's own `--timeout` takes plain seconds) and says
  exit code `4` is shared by `resolve` and `sync.py`; `references/install.md` no longer claims the ASR
  engines were tested with a real model; the maintainer's local checkout path was removed from
  `references/commands.md`; `docs/EVALS.md` counts 4 `llm` graders; GUIDE: attachments are read from
  the archive (not a temporary folder), Homebrew installs cannot be pinned, and `--doctor` also
  checks that the archive folder is writable (all four languages).
- CI: `astral-sh/setup-uv@v10` does not exist (no floating major tag), which would have failed every
  test job; tools are now installed with pip. Added a non-blocking job that runs the media tests with
  a real ffmpeg on Linux.

### Known issues
- Upstream line-cli v0.4.0: `send` to some direct chats fails with LINE error 10051 / TalkException
  86 (`sender or receiver not supported e2ee or version mismatch`). The peer key has e2eeVersion 0
  while the CLI always encrypts V2, and the real cause is hidden behind a generic "request failed".
  Nothing is delivered; the skill says so, does not retry and suggests sending from the LINE app.
  Parked; no upstream issue or PR yet.
- Upstream `getContactsV2` rejects the whole batch when LINE returns userStatus-only stubs (deleted
  accounts, non-friend group members), which breaks `chats --search` and `contacts --mid`. Worked
  around in the skill (see Fixed). Details: `references/errors.md`, "Known upstream bugs".
- media-digest: audio above a cloud adapter's upload limit (25 MB) is not split or compressed yet, so
  that adapter fails with `too_large` and the next one in the chain takes over. With `--lang auto`
  the per-language routes in `config.toml` are not applied; pass `--lang`.
