# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
- `sync.py <CHAT_ID>` without `--name` no longer leaves the archive folder and the `messages.md`
  title as the bare ID: the name is looked up (contacts for direct chats, one cached
  `chats --limit 0 --show-ids` call for groups and rooms) and an id-only folder is renamed. If the
  lookup fails the folder stays id-only, silently.

### Changed
- Archive folders are now named `<chat name>__<chat_id>` (name first, full ID kept intact, name
  part truncated first). Existing `<chat_id>` and `<chat_id>__<name>` folders are found by ID and
  renamed automatically on the next sync, contents untouched.
- `messages.md`: content type 18 reads `[System event (type 18)]`, other unsupported types read
  `[Unsupported content (type N)]`, and a "no messages for X days (from -> to)" line marks silences
  longer than the new `render.quiet_gap_days` setting (default 3, `0` disables).

### Added
- M1 skeleton: marketplace manifest, `line-cli` and `media-digest` plugin manifests,
  placeholder skills, package stubs, manifest tests and CI.
- Multilingual documentation (English, zh-TW, ja, th): README and user guide stubs.
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

### Fixed
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

### Changed
- `lineq.py resolve`: a single partial-name match (no exact match) now resolves directly
  (`status: resolved`, `matched_by: "substring"`) instead of asking the user to pick from one
  option. Two or more matches still return `candidates`.

### Known issues
- Upstream line-cli v0.4.0: `send` to some direct chats fails with LINE error 10051 / TalkException
  86 (`sender or receiver not supported e2ee or version mismatch`). The peer key has e2eeVersion 0
  while the CLI always encrypts V2, and the real cause is hidden behind a generic "request failed".
  Nothing is delivered; the skill says so, does not retry and suggests sending from the LINE app.
  Parked; no upstream issue or PR yet.
- Upstream `getContactsV2` rejects the whole batch when LINE returns userStatus-only stubs (deleted
  accounts, non-friend group members), which breaks `chats --search` and `contacts --mid`. Worked
  around in the skill (see Fixed). Details: `references/errors.md`, "Known upstream bugs".
