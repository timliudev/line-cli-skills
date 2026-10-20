# line-cli-skills v0.3.0

Supports line-cli v0.5.0.

Verified live on line-cli v0.5.0 (Windows, a test group): `sync.py` paging, an end-to-end encrypted
send through `lineq.py` with the default `--require-e2ee`, a `--mentions` user span after an emoji,
`@All`, and `--reply-to` an older message. Not verified live: the `encryption_required` path (no
chat without E2EE available) and the old Letter Sealing key failure.

### Changed
- Targets upstream line-cli v0.5.0 (was v0.4.0). `setup.py` pins the new release and its pinned
  installer script hashes (the install scripts are byte-identical to v0.4.0's). The "sending works"
  live check was done on v0.4.0 and is documented as such; the old Letter Sealing key failure
  (error 10051 / 86) has not been re-tested on v0.5.0.
- `sync.py` now reads history with `messages --page --json` in pages of 100 and pages back until
  it reaches the newest archived message, so messages missed between syncs are filled in
  automatically (default at most 10 pages = 1000 messages per sync). A first sync still reads one
  page unless `--backfill` is given. A `gap` is reported only when the sync stopped before reaching
  the archive (`--max-pages` reached, or an older page failed); the fix is to sync again, with a
  higher `--max-pages` if needed. The summary JSON has a new `pages` field.
  `store.merge_window(history_exhausted=...)` replaces `window_limit`.
- Sends require end-to-end encryption by default: `lineq.py run -- send` and `broadcast.py` add
  `--require-e2ee`, so a chat without E2EE fails with the new error kind `encryption_required` and
  nothing is sent (v0.5.0 would otherwise fall back to HTTPS/TLS, where LINE can read the content).
  Claude sends without E2EE only after your explicit yes (`lineq.py run --allow-non-e2ee`,
  `broadcast.py --allow-non-e2ee`; the choice is part of the broadcast plan hash).
- `members` is treated as a read command. Partial results are now also recognised for
  `messages --page` and `--details` JSON objects.
- Docs (README and GUIDE, four languages): @mentions are supported (Claude looks the member up with
  `line members`; plain "@name" text alone still notifies nobody), the "latest 100 only" and
  "cannot page back" claims are removed, reply/react/unsend/download targets are described as
  looked up by line v0.5.0 (up to 10 pages / 1000 messages and 30 seconds), and the troubleshooting
  tables gain an `encryption_required` row and a rewritten "gap" row.

### Added
- `sync.py --max-pages N` (pages read per chat per sync, default 10) and `--backfill` (first sync
  keeps paging back until LINE has no older history, bounded by `--max-pages`).
- `lineq.py run --allow-non-e2ee` and `broadcast.py --allow-non-e2ee` (explicit opt-out of the
  default `--require-e2ee`).
- Error kinds `encryption_required`, `message_not_found` and `lookup_incomplete`.
