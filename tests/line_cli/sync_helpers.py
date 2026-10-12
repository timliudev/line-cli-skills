"""Helpers for the sync/archive tests: message builders and a runner for sync.py."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .helpers import GID, WHOAMI_OK, load_script, resp, rule

sync = load_script("sync")

CHAT = GID
ME = WHOAMI_OK["mid"]
ALICE = "u" + "1" * 32
BOB = "u" + "2" * 32
T0 = 1_760_000_000_000  # 2025-10-09, ms since epoch

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
PDF = b"%PDF-1.7\n" + b"x" * 20
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42"


def at(minutes: int) -> int:
    return T0 + minutes * 60_000


def msg(mid, minutes, text="hello", sender=ALICE, **extra):
    item = {
        "id": str(mid),
        "from": sender,
        "to": CHAT,
        "created_time": at(minutes),
        "content_type": 0,
        "text": text,
        "encrypted": True,
        "status": "decrypted",
    }
    item.update(extra)
    return item


def att(mid, minutes, content_type=1, sender=ALICE, file_name=None):
    item = {
        "id": str(mid),
        "from": sender,
        "to": CHAT,
        "created_time": at(minutes),
        "content_type": content_type,
        "text": "",
        "encrypted": True,
        "status": "attachment",
    }
    if file_name is not None:
        item["file_name"] = file_name
    return item


def messages_rule(window, chat=CHAT):
    """`window` is one list (every call) or a list of lists wrapped in Seq (one per call)."""
    args = ["messages", chat, "--json", "--limit", "100"]
    if isinstance(window, Seq):
        return rule(args, responses=[resp(0, w) for w in window.items])
    return rule(args, stdout=window)


class Seq:
    def __init__(self, *items):
        self.items = list(items)


def contacts_rule(**names):
    rows = [
        {"mid": mid, "effectiveDisplayName": name, "displayName": name, "status": "resolved"}
        for mid, name in names.items()
    ]
    return rule(prefix=["contacts", "--mid"], stdout=rows)


def download_rule(mid, payload=JPEG, pad=0, chat=CHAT, **kw):
    """A `download` that writes `payload` to --output (exclusively, like the real CLI)."""
    write = {"b64": base64.b64encode(payload).decode(), "pad": pad}
    response = {"exit": 0, "stdout": {"path": "x", "bytes": len(payload) + pad, "message_id": mid}}
    response["write"] = write
    response.update(kw)
    return rule(prefix=["download", chat, "--message", str(mid)], responses=[response])


def download_error(mid, stderr, chat=CHAT):
    return rule(prefix=["download", chat, "--message", str(mid)], exit=1, stderr=stderr)


def base_rules(*extra, names=None):
    """whoami + sender names; `extra` rules win because they are listed first."""
    return [
        *extra,
        rule(["whoami", "--json"], stdout=WHOAMI_OK),
        contacts_rule(**(names or {ALICE: "Alice", BOB: "Bob"})),
    ]


def run_sync(capsys, *argv):
    """Run sync.main and return (exit_code, parsed_stdout_json)."""
    code = sync.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


def archive_dir(root: Path, chat=CHAT) -> Path:
    found = [p for p in root.iterdir() if p.is_dir() and chat in p.name]
    assert len(found) == 1, f"expected one archive folder for {chat}, got {found}"
    return found[0]


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def count_calls(fake_line, command, *, chat=None):
    return sum(
        1
        for argv in fake_line.argv_calls()
        if argv[:1] == [command] and (chat is None or argv[1:2] == [chat])
    )
