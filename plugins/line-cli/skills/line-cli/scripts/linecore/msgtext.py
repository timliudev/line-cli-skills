"""Sender names from the TEXT form of `line messages CHAT --show-ids`.

`contacts --mid` fails for group members who are not friends, but the text output of `messages`
shows their display names (upstream resolves them through the group member list)::

    Sat, 10 Oct 2026
    15:34  Some Name
           ID: 635498072167940322
           [Image]

Own messages are shown as ``You`` and are never used as a name.
"""

from __future__ import annotations

import re

OWN_LABELS = {"you"}
_HEADER = re.compile(r"^\d{1,2}:\d{2}[ \t]{2,}(\S.*?)\s*$")  # column 0: content lines are indented
_ID = re.compile(r"^[ \t]+ID:[ \t]*(\S+)\s*$")


def parse_sender_names(text: str) -> dict[str, str]:
    """``{message_id: sender display name}``; own messages and unparseable lines are skipped."""
    out: dict[str, str] = {}
    current: str | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        header = _HEADER.match(line)
        if header:
            current = header.group(1)
            continue
        found = _ID.match(line)
        if found and current is not None:
            if current.casefold() not in OWN_LABELS:
                out.setdefault(found.group(1), current)
            current = None
    return out
