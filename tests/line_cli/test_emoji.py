"""LINE emoji (sticon) cache: download once, back off on failure, labels, rendering, config."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from linecore import config, emoji, render

from .helpers import load_script
from .sync_helpers import ALICE, CHAT, ME, at, base_rules, messages_rule, msg, run_sync

emoji_cli = load_script("emoji")
PNG = emoji.PNG_MAGIC + b"\x00" * 16
KEY = "5ac1de17040ab4a8bd8bc9b4/002"
NOW = datetime(2026, 10, 14, tzinfo=UTC)


def paid(product="5ac1de17040ab4a8bd8bc9b4", emoji_id="002", mid=ALICE):
    return {
        "fromUserMid": mid,
        "atMillis": at(2),
        "reactionType": {"paidReactionType": {"productId": product, "emojiId": emoji_id}},
    }


class Fetcher:
    def __init__(self, data=PNG, fail=False):
        self.data, self.fail, self.urls = data, fail, []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("boom")
        return self.data


def test_download_once_then_cache_hit(archive_root, monkeypatch):
    fetch = Fetcher()
    monkeypatch.setattr(emoji, "fetch_bytes", fetch)
    index, warnings = emoji.ensure(archive_root, [KEY], now=NOW)
    assert warnings == [] and index[KEY]["file"] == "5ac1de17040ab4a8bd8bc9b4/002.png"
    assert fetch.urls == [emoji.URL.format(product="5ac1de17040ab4a8bd8bc9b4", emoji="002")]
    emoji.ensure(archive_root, [KEY, KEY], now=NOW + timedelta(days=30))
    assert len(fetch.urls) == 1  # shared: never downloaded again
    assert (archive_root / "_line_emoji" / index[KEY]["file"]).read_bytes() == PNG


def test_failure_backs_off_for_seven_days(archive_root, monkeypatch):
    fetch = Fetcher(fail=True)
    monkeypatch.setattr(emoji, "fetch_bytes", fetch)
    index, warnings = emoji.ensure(archive_root, [KEY], now=NOW)
    assert warnings and "file" not in index[KEY] and index[KEY]["last_attempt"]
    emoji.ensure(archive_root, [KEY], now=NOW + timedelta(days=6))
    assert len(fetch.urls) == 1
    emoji.ensure(archive_root, [KEY], now=NOW + timedelta(days=8))
    assert len(fetch.urls) == 2


@pytest.mark.parametrize(
    "data", [b"<html>nope</html>", PNG + b"x" * emoji.MAX_BYTES], ids=["html", "too_big"]
)
def test_rejects_non_png(archive_root, monkeypatch, data):
    def fetch(url):
        if len(data) > emoji.MAX_BYTES:
            raise ValueError("image larger than 1 MiB")
        return data

    monkeypatch.setattr(emoji, "fetch_bytes", fetch)
    index, _ = emoji.ensure(archive_root, [KEY], now=NOW)
    assert "file" not in index[KEY] and index[KEY]["error"]


def test_unsafe_ids_are_never_fetched(archive_root, monkeypatch):
    fetch = Fetcher()
    monkeypatch.setattr(emoji, "fetch_bytes", fetch)
    emoji.ensure(archive_root, ["../x/002", "abc/12", "abc/0021"], now=NOW)
    assert fetch.urls == []
    assert emoji.keys_in([{"reactions": [paid(product="a/b"), paid(emoji_id="2")]}]) == []


def test_render_local_remote_and_label():
    rtype = paid()["reactionType"]
    remote = render.reaction_label(rtype)
    assert remote.startswith("[LINE emoji 5ac1de17040ab4a8bd8bc9b4/002](https://stickershop")
    info = {KEY: {"label": None, "file": "5ac1de17040ab4a8bd8bc9b4/002.png"}}
    local = render.reaction_label(rtype, info, "../_line_emoji/")
    assert (
        local
        == "[LINE emoji 5ac1de17040ab4a8bd8bc9b4/002]"
        "(../_line_emoji/5ac1de17040ab4a8bd8bc9b4/002.png)"
    )
    info[KEY]["label"] = "OK hand"
    labeled = render.reaction_label(rtype, info, "../_line_emoji/")
    assert labeled == '[LINE emoji "OK hand"](../_line_emoji/5ac1de17040ab4a8bd8bc9b4/002.png)'
    info[KEY]["file"] = None
    assert render.reaction_label(rtype, info) == 'LINE emoji "OK hand"'


def test_label_cli(archive_root, monkeypatch, capsys):
    monkeypatch.setattr(emoji, "fetch_bytes", Fetcher())
    emoji.ensure(archive_root, [KEY], now=NOW)
    assert emoji_cli.main(["list", "--unlabeled"]) == 0
    rows = json.loads(capsys.readouterr().out)["emoji"]
    assert rows[0]["key"] == KEY and rows[0]["label"] is None
    assert emoji_cli.main(["label", KEY, 'OK "hand"']) == 0
    assert json.loads(capsys.readouterr().out)["label"] == "OK hand"
    assert emoji_cli.main(["list", "--unlabeled"]) == 0
    assert json.loads(capsys.readouterr().out)["emoji"] == []
    assert emoji_cli.main(["label", "1234/001", "x"]) == 4
    capsys.readouterr()
    assert emoji_cli.main(["label", "bad", "x"]) == 2


def sync_with_reaction(capsys, fake_line):
    window = [msg(1, 0, "hi", ME, reactions=[paid()])]
    fake_line.scenario(base_rules(messages_rule(window)))
    return run_sync(capsys, CHAT, "--name", "Family")


def messages_md(archive_root):
    return next(archive_root.glob("*/messages.md")).read_text(encoding="utf-8")


def test_sync_downloads_labels_and_renders_local_link(fake_line, archive_root, capsys, monkeypatch):
    fetch = Fetcher()
    monkeypatch.setattr(emoji, "fetch_bytes", fetch)
    code, _ = sync_with_reaction(capsys, fake_line)
    assert code == 0 and len(fetch.urls) == 1
    assert "](../_line_emoji/5ac1de17040ab4a8bd8bc9b4/002.png)" in messages_md(archive_root)
    emoji.set_label(archive_root, KEY, "OK hand")
    sync_with_reaction(capsys, fake_line)
    assert len(fetch.urls) == 1  # reused, not downloaded again
    assert 'LINE emoji "OK hand"' in messages_md(archive_root)


def test_sync_falls_back_to_remote_url_when_download_fails(
    fake_line, archive_root, capsys, monkeypatch
):
    monkeypatch.setattr(emoji, "fetch_bytes", Fetcher(fail=True))
    code, out = sync_with_reaction(capsys, fake_line)
    assert code == 0 and any("LINE emoji" in w for w in out["warnings"])
    assert "stickershop.line-scdn.net" in messages_md(archive_root)


def test_disabled_by_config_and_no_download(fake_line, archive_root, capsys, monkeypatch):
    fetch = Fetcher()
    monkeypatch.setattr(emoji, "fetch_bytes", fetch)
    cfg_file = config.config_path()
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text("[download]\nline_emoji = false\n", encoding="utf-8")
    code, _ = sync_with_reaction(capsys, fake_line)
    assert code == 0 and fetch.urls == []
    assert not (archive_root / "_line_emoji").exists()
    assert "stickershop.line-scdn.net" in messages_md(archive_root)
