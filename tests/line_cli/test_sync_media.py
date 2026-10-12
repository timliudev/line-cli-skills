"""Attachment downloads: names, magic bytes, failures, filters."""

from __future__ import annotations

import json

import pytest
from linecore import media, store
from linecore.config import config_path

from .helpers import rule
from .sync_helpers import (
    CHAT,
    JPEG,
    M4A,
    MP4,
    PDF,
    PNG,
    Seq,
    archive_dir,
    at,
    att,
    base_rules,
    count_calls,
    download_error,
    download_rule,
    messages_rule,
    msg,
    run_sync,
)


def files_of(folder):
    directory = folder / "files"
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def test_download_names_files_by_time_and_id(fake_line, archive_root, capsys):
    window = [
        msg(1, 0, "see attached"),
        att(11, 1, 1),
        att(12, 2, 2),
        att(13, 3, 3),
        att(14, 4, 14, file_name="Quarterly Report.pdf"),
    ]
    fake_line.scenario(
        base_rules(
            download_rule(11, JPEG),
            download_rule(12, MP4),
            download_rule(13, M4A),
            download_rule(14, PDF),
            messages_rule(window),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0 and out["failed_files"] == []

    folder = archive_dir(archive_root)
    expected = {
        f"{media.stamp(at(1))}_11.jpg",
        f"{media.stamp(at(2))}_12.mp4",
        f"{media.stamp(at(3))}_13.m4a",
        f"{media.stamp(at(4))}_14.pdf",
    }
    assert set(files_of(folder)) == expected
    assert {f["message_id"]: f["type"] for f in out["new_files"]} == {
        "11": "image",
        "12": "video",
        "13": "audio",
        "14": "file",
    }
    first = out["new_files"][0]
    assert first["file"] == f"files/{media.stamp(at(1))}_11.jpg" and first["bytes"] == len(JPEG)
    assert (folder / first["file"]).is_file() and first["path"] == str(folder / first["file"])

    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert f"[Image](files/{media.stamp(at(1))}_11.jpg)" in md
    assert f"[File: Quarterly Report.pdf](files/{media.stamp(at(4))}_14.pdf)" in md

    # no temp files are left behind; the CLI is asked to write a fresh, non-existing path
    assert not [n for n in files_of(folder) if n.endswith(".part")]

    # a second sync downloads nothing again
    before = count_calls(fake_line, "download")
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["new_files"] == [] and count_calls(fake_line, "download") == before


@pytest.mark.parametrize(
    ("content_type", "file_name", "payload", "expected"),
    [
        (1, None, JPEG, ".jpg"),
        (1, None, PNG, ".png"),  # declared jpg, really a png
        (1, None, b"GIF89a" + b"\0" * 20, ".gif"),
        (1, None, b"RIFF\x10\0\0\0WEBPVP8 " + b"\0" * 8, ".webp"),
        (2, None, MP4, ".mp4"),
        (2, None, M4A, ".m4a"),  # an audio container sent as a video
        (3, None, M4A, ".m4a"),
        (3, None, MP4, ".m4a"),  # voice messages in an mp4 container stay .m4a
        (14, "report", PDF, ".pdf"),  # no extension, but the bytes say pdf
        (14, "Report.PDF", PDF, ".pdf"),
        (14, "photo.jpeg", JPEG, ".jpeg"),  # equivalent spelling is kept
        (14, "data.xlsx", b"PK\x03\x04" + b"\0" * 20, ".xlsx"),  # unknown magic: trust the name
        (14, "weird.ex/e?", b"abc", ".bin"),
        (14, None, b"abc", ".bin"),
    ],
)
def test_extension_follows_magic_bytes(content_type, file_name, payload, expected):
    record = att(5, 0, content_type, file_name=file_name)
    assert media.final_ext(record, payload[:32]) == expected


def test_sniff_covers_documented_formats():
    assert media.sniff_ext(JPEG) == ".jpg"
    assert media.sniff_ext(PNG) == ".png"
    assert media.sniff_ext(PDF) == ".pdf"
    assert media.sniff_ext(M4A) == ".m4a"
    assert media.sniff_ext(MP4) == ".mp4"
    assert media.sniff_ext(b"") is None and media.sniff_ext(b"hello world!") is None


def test_corrected_extension_in_a_real_sync(fake_line, archive_root, capsys):
    fake_line.scenario(base_rules(download_rule(11, PNG), messages_rule([att(11, 1, 1)])))
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["new_files"][0]["file"].endswith("_11.png")


def test_expired_attachment_is_marked_and_not_retried(fake_line, archive_root, capsys):
    window = [att(11, 1, 1), att(12, 2, 2)]
    fake_line.scenario(
        base_rules(
            download_error(11, "line: attachment is no longer available from LINE"),
            download_rule(12, MP4),
            messages_rule(Seq(window, window)),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0
    assert [f["message_id"] for f in out["new_files"]] == [
        "12"
    ]  # one failure does not stop the rest
    assert out["failed_files"][0]["message_id"] == "11"
    assert out["failed_files"][0]["reason"] == "expired"

    folder = archive_dir(archive_root)
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert "[Image] expired" in md
    state = store.read_state(folder / "state.json", CHAT)
    assert state["failed_files"]["11"]["reason"] == "expired"

    run_sync(capsys, CHAT, "--name", "G")
    assert count_calls(fake_line, "download") == 2  # 11 and 12 once each, nothing retried


def test_attachment_over_the_cli_limit(fake_line, archive_root, capsys):
    fake_line.scenario(
        base_rules(
            download_error(11, "line: attachment exceeds the CLI limit of 20 MiB"),
            messages_rule([att(11, 1, 2)]),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["failed_files"][0]["reason"] == "too_large"
    md = (archive_dir(archive_root) / "messages.md").read_text(encoding="utf-8")
    assert "[Video] over 20 MiB, not downloaded" in md


def test_configured_max_bytes_discards_the_download(fake_line, archive_root, capsys):
    window = [att(11, 1, 14, file_name="big.pdf")]
    fake_line.scenario(
        base_rules(download_rule(11, PDF, pad=500), messages_rule(Seq(window, window, window)))
    )
    code, out = run_sync(capsys, CHAT, "--name", "G", "--max-bytes", "100")
    assert out["new_files"] == [] and out["failed_files"][0]["reason"] == "too_large"
    folder = archive_dir(archive_root)
    assert files_of(folder) == []  # the oversized file and its temp file are gone
    assert "[File: big.pdf] over 100 B, not downloaded" in (folder / "messages.md").read_text(
        "utf-8"
    )

    # same limit: not retried
    run_sync(capsys, CHAT, "--name", "G", "--max-bytes", "100")
    assert count_calls(fake_line, "download") == 1

    # a higher limit makes it worth another try
    code, out = run_sync(capsys, CHAT, "--name", "G", "--max-bytes", "10000")
    assert [f["message_id"] for f in out["new_files"]] == ["11"]
    assert count_calls(fake_line, "download") == 2


def test_transient_failures_are_retried_next_sync(fake_line, archive_root, capsys):
    window = [att(11, 1, 1)]
    processing = {
        "exit": 1,
        "stdout": "",
        "stderr": "line: LINE is still processing this attachment; try again later",
    }
    ok = {
        "exit": 0,
        "stdout": {"path": "x", "bytes": 1, "message_id": "11"},
        "write": {"b64": "/9j/4A==", "pad": 10},
    }
    fake_line.scenario(
        base_rules(
            rule(prefix=["download", CHAT, "--message", "11"], responses=[processing, ok]),
            messages_rule(Seq(window, window)),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["failed_files"][0]["reason"] == "processing"
    folder = archive_dir(archive_root)
    assert "still processing" in (folder / "messages.md").read_text(encoding="utf-8")

    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert [f["message_id"] for f in out["new_files"]] == ["11"] and out["failed_files"] == []
    assert store.read_state(folder / "state.json", CHAT)["failed_files"] == {}
    assert "still processing" not in (folder / "messages.md").read_text(encoding="utf-8")


def test_types_filter_and_no_download(fake_line, archive_root, capsys):
    window = [att(11, 1, 1), att(12, 2, 2)]
    fake_line.scenario(
        base_rules(
            download_rule(11, JPEG),
            download_rule(12, MP4),
            messages_rule(Seq(window, window, window)),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G", "--no-download")
    assert out["new_files"] == [] and count_calls(fake_line, "download") == 0
    folder = archive_dir(archive_root)
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert "[Image] not downloaded" in md and "[Video] not downloaded" in md

    code, out = run_sync(capsys, CHAT, "--name", "G", "--types", "image")
    assert [f["message_id"] for f in out["new_files"]] == ["11"]
    assert count_calls(fake_line, "download") == 1

    # widening the filter later picks up what was skipped (it is still in the window)
    code, out = run_sync(capsys, CHAT, "--name", "G", "--types", "image,video")
    assert [f["message_id"] for f in out["new_files"]] == ["12"]


def test_config_download_settings_apply(fake_line, archive_root, capsys):
    cfg = config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[download]\ntypes = ["video"]\nmax_bytes = 1000\n', encoding="utf-8")
    window = [att(11, 1, 1), att(12, 2, 2)]
    fake_line.scenario(
        base_rules(download_rule(11, JPEG), download_rule(12, MP4), messages_rule(window))
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert [f["message_id"] for f in out["new_files"]] == ["12"]


def test_login_problem_stops_downloading_without_marking_attachments(
    fake_line, archive_root, capsys
):
    window = [att(11, 1, 1), att(12, 2, 1)]
    fake_line.scenario(
        base_rules(
            rule(
                prefix=["download", CHAT],
                exit=1,
                stderr="line: no saved LINE session; run line login",
            ),
            messages_rule(window),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0 and count_calls(fake_line, "download") == 1  # gave up after the first
    assert out["failed_files"][0]["kind"] == "not_logged_in"
    assert any("stopped downloading" in w for w in out["warnings"])
    state = store.read_state(archive_dir(archive_root) / "state.json", CHAT)
    assert state["failed_files"] == {}  # the attachments themselves are fine; retry next time


def test_download_never_overwrites(tmp_path, fake_line, monkeypatch):
    record = att(11, 1, 1)
    fake_line.scenario([download_rule(11, JPEG)])
    files = tmp_path / "files"
    files.mkdir()
    taken = files / f"{media.stamp(at(1))}_11.jpg"
    taken.write_bytes(b"precious")
    result = media.download_attachment(CHAT, record, files, max_bytes=10_000)
    assert result.ok and result.path != taken
    assert result.path.name == f"{media.stamp(at(1))}_11-1.jpg"
    assert taken.read_bytes() == b"precious"


def test_index_files_ignores_partial_downloads(tmp_path):
    (tmp_path / "20251009-000100_11.jpg").write_bytes(b"x")
    (tmp_path / "20251009-000200_12.part").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    assert media.index_files(tmp_path) == {"11": "20251009-000100_11.jpg"}


def test_download_json_shape_is_documented_not_trusted(fake_line, archive_root, capsys):
    """The real file size wins over whatever `bytes` the CLI printed."""
    response = {
        "exit": 0,
        "stdout": json.dumps({"path": "x", "bytes": 999999, "message_id": "11"}),
        "write": {"b64": "/9j/4A==", "pad": 0},
    }
    fake_line.scenario(
        base_rules(
            rule(prefix=["download", CHAT, "--message", "11"], responses=[response]),
            messages_rule([att(11, 1, 1)]),
        )
    )
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["new_files"][0]["bytes"] == 4
