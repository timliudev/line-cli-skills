# line-cli-skills v0.1.2

Replies, reactions and LINE emoji.

## Added

- `thread.py`: "who replied to / reacted to my message X?" answered offline from the local archive.
  Target by message ID or text (+ sender); replies come from `reply_to`, reactions from `reactions`;
  `--depth N` follows replies to replies. Only messages that are in the archive can be found, so sync
  first.
- LINE emoji reactions: sync caches each distinct emoji image once in `<archive_root>/_line_emoji/`
  (shared by all chats, so the archive grows only by unique emoji). Switch off with
  `download.line_emoji = false`. The image URL is an unofficial pattern; failures back off for 7 days.
- `emoji.py list|label`: short labels (for example "OK hand") so an image is looked at only once;
  `messages.md` shows `LINE emoji "<label>"` with a link to the local file.

## Notes

- Sticker messages still show as unsupported content (line-cli does not say which sticker).
- Still untested live: reacting, unsending, `--reply-to` on its own, macOS and Linux, animated sticons.
