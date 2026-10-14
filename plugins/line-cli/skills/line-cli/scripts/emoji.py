#!/usr/bin/env python3
"""emoji - label the shared LINE emoji (sticon) images that sync.py caches. JSON on stdout.

  emoji.py list [--unlabeled]                 Cached emoji: key, absolute image path, label.
  emoji.py label PRODUCT_ID/EMOJI_ID "text"   Save a short label (say "" to remove it).

The cache lives in <archive_root>/_line_emoji/ and is shared by every chat. A label shows up in
messages.md at the next sync (or any later render) as `LINE emoji "<label>"`.

Exit codes
  0  success
  2  usage error
  4  the emoji is not in the cache index (sync the chat that uses it first)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import emoji, runner  # noqa: E402
from linecore.config import load_config_safe  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DECIDE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emoji.py",
        description="List and label the cached LINE emoji images (shared across chats).",
        epilog="Exit codes: 0 ok, 2 usage, 4 emoji not in the cache index.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="show cached emoji")
    listing.add_argument("--unlabeled", action="store_true", help="only emoji without a label")
    label = sub.add_parser("label", help="save a short label for one emoji")
    label.add_argument("key", metavar="PRODUCT_ID/EMOJI_ID")
    label.add_argument("text", help='short description, e.g. "OK hand"')
    return parser


def main(argv: list[str] | None = None) -> int:
    runner.configure_stdio()
    args = build_parser().parse_args(argv)
    config, error = load_config_safe()
    if error:
        runner.log(f"warning: {error}")
    root = config.archive_root

    if args.command == "list":
        rows = []
        for key, entry in sorted(emoji.load_index(root).items()):
            path = emoji.cached_path(root, entry)
            label = entry.get("label") or None
            if path is None or (args.unlabeled and label):
                continue
            rows.append({"key": key, "path": str(path), "label": label})
        runner.emit({"emoji": rows})
        return EXIT_OK

    if emoji.split_key(args.key) is None:
        runner.emit({"error": {"kind": "usage", "message": "key must look like 1234567/001"}})
        return EXIT_USAGE
    if not emoji.set_label(root, args.key, args.text):
        runner.emit({"status": "not_found", "message": f"{args.key} is not in the emoji cache"})
        return EXIT_NOT_FOUND
    runner.emit({"status": "labeled", "key": args.key, "label": emoji.clean_label(args.text)})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
