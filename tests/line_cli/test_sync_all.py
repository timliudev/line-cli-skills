"""--all, archive location warnings, file-name safety, CLI argument handling."""

from __future__ import annotations

import pytest
from linecore import store
from linecore.config import config_path

from .helpers import GID, resp, rule
from .sync_helpers import (
    ALICE,
    CHAT,
    base_rules,
    messages_rule,
    msg,
    run_sync,
    sync,
)

OTHER = "C" + "E" * 43
BROKEN = "C" + "F" * 43


def chats_listing(*ids):
    return rule(
        ["chats", "--json"],
        stdout=[{"id": cid, "type": 0, "unread_count": 0} for cid in ids],
    )


def test_all_continues_after_a_chat_fails(fake_line, archive_root, capsys):
    fake_line.scenario(
        base_rules(
            chats_listing(CHAT, BROKEN, OTHER),
            messages_rule([msg(1, 0, "in the first chat")], chat=CHAT),
            rule(
                ["messages", BROKEN, "--json", "--limit", "100"],
                responses=[resp(1, "", "line: LINE request failed: dial tcp: i/o timeout")],
            ),
            messages_rule([msg(2, 1, "in the third chat")], chat=OTHER),
        )
    )
    code, out = run_sync(capsys, "--all")
    assert code == 1  # at least one chat failed, but everything else was synced
    assert out["all"] is True and out["total"] == 3
    assert [c["chat_id"] for c in out["chats"]] == [CHAT, OTHER]
    assert [f["chat_id"] for f in out["failed"]] == [BROKEN]
    assert out["failed"][0]["error"]["kind"] == "network"
    ids = {p.name.rsplit("__", 1)[-1] for p in archive_root.iterdir() if p.is_dir()}
    assert ids == {CHAT, OTHER}

    # chats were processed one after another, in listing order
    asked = [a[1] for a in fake_line.argv_calls() if a[:1] == ["messages"]]
    assert asked == [CHAT, BROKEN, OTHER]


def test_all_succeeds_with_exit_zero(fake_line, archive_root, capsys):
    fake_line.scenario(base_rules(chats_listing(CHAT), messages_rule([msg(1, 0, "hello", ALICE)])))
    code, out = run_sync(capsys, "--all", "--no-download")
    assert code == 0 and out["failed"] == [] and out["chats"][0]["new_messages"] == 1


def test_all_when_listing_chats_fails(fake_line, archive_root, capsys):
    fake_line.scenario(
        base_rules(
            rule(["chats", "--json"], exit=1, stderr="line: no saved LINE session; run line login")
        )
    )
    code, out = run_sync(capsys, "--all")
    assert code == 1 and out["error"]["kind"] == "not_logged_in" and out["chats"] == []


# ------------------------------------------------------------------ archive location


def test_warns_when_archive_is_inside_a_git_repo(fake_line, isolated_env, monkeypatch, capsys):
    repo = isolated_env / "my-project"
    (repo / ".git").mkdir(parents=True)
    root = repo / "notes" / "line"
    monkeypatch.setenv("LINE_SKILL_ARCHIVE", str(root))
    fake_line.scenario(base_rules(messages_rule([msg(1, 0, "x")])))
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0
    assert any("git repository" in w and "never commit" in w for w in out["warnings"])
    assert (root / f"G__{CHAT}" / "messages.md").is_file()  # still works, just warns


def test_no_git_warning_outside_a_repo(fake_line, archive_root, capsys):
    fake_line.scenario(base_rules(messages_rule([msg(1, 0, "x")])))
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert not any("git" in w for w in out["warnings"])


def test_archive_root_from_config_file(fake_line, isolated_env, capsys):
    cfg = config_path()
    cfg.parent.mkdir(parents=True)
    target = isolated_env / "from-config"
    cfg.write_text(f'archive_root = "{target.as_posix()}"\n', encoding="utf-8")
    fake_line.scenario(base_rules(messages_rule([msg(1, 0, "x")])))
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0 and out["archive_dir"].startswith(str(target))


def test_unwritable_archive_root_is_an_error(fake_line, isolated_env, monkeypatch, capsys):
    blocker = isolated_env / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("LINE_SKILL_ARCHIVE", str(blocker / "archive"))
    fake_line.scenario(base_rules(messages_rule([msg(1, 0, "x")])))
    code, out = run_sync(capsys, CHAT)
    assert code == 1 and out["error"]["kind"] == "archive_unwritable"


# ------------------------------------------------------------------ names and arguments


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Family", "Family"),
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("lpt1", "_lpt1"),
        ("trailing dots...", "trailing dots"),
        ("  spaced   out  ", "spaced out"),
        ("tab\tand\nnewline", "tab and newline"),
        ("", "unnamed"),
        ("???", "unnamed"),
        ("家族 👨‍👩‍👧 グループ", "家族 👨‍👩‍👧 グループ"),
    ],
)
def test_safe_name(raw, expected):
    assert store.safe_name(raw) == expected


def test_safe_name_limits_length_and_keeps_windows_happy():
    long = store.safe_name("x" * 500)
    assert len(long) <= store.NAME_LIMIT
    assert store.safe_name("y" * (store.NAME_LIMIT - 1) + ". ") == "y" * (store.NAME_LIMIT - 1)


def test_folder_name_is_name_first_and_keeps_the_id_intact():
    assert store.folder_name(GID, "Family") == f"Family__{GID}"
    assert store.folder_name(GID, None) == GID
    assert store.folder_name(GID, "???") == GID
    long = store.folder_name(GID, "n" * 200)
    assert long == "n" * store.NAME_LIMIT + f"__{GID}"
    assert store.folder_name(GID, "a:b/c?") == f"a_b_c___{GID}"
    assert store.folder_name(GID, "CON") == f"_CON__{GID}"
    assert store.folder_name(GID, "name. ") == f"name__{GID}"


def test_find_chat_dir_matches_new_old_and_bare_layouts(tmp_path):
    other = "C" + "Z" * 43
    (tmp_path / f"{GID}X__other").mkdir()
    (tmp_path / f"Other__{GID}X").mkdir()
    (tmp_path / "contacts.json").write_text("{}", encoding="utf-8")
    assert store.find_chat_dir(tmp_path, GID) is None
    for name in (f"{GID}__Old Name", f"New Name__{GID}", GID):
        (tmp_path / name).mkdir()
        found = store.find_chat_dir(tmp_path, GID)
        assert found is not None and found.name == name
        (tmp_path / name).rmdir()
    assert store.find_chat_dir(tmp_path, other) is None


def test_folder_label_reads_new_old_and_bare(tmp_path):
    assert store.folder_label(tmp_path / f"New__{GID}", GID) == "New"
    assert store.folder_label(tmp_path / f"{GID}__Old", GID) == "Old"
    assert store.folder_label(tmp_path / GID, GID) is None


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--all", CHAT],
        [CHAT, "--types", "gif"],
        [CHAT, "--max-bytes", "0"],
        ["--all", "--name", "X"],
    ],
)
def test_bad_arguments_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        sync.main(argv)
    assert exc.value.code == 2


def test_atomic_state_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "dir"
    store.write_json(target / "state.json", {"a": 1})
    store.write_json(target / "state.json", {"a": 2})
    assert [p.name for p in target.iterdir()] == ["state.json"]
    assert store.read_json(target / "state.json", None) == {"a": 2}
