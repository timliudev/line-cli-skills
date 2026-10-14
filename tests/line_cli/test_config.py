from pathlib import Path

import pytest
from linecore import config as cfg


def write_config(base: Path, text: str) -> Path:
    path = base / "line-skill" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_config_dir_per_os(tmp_path):
    xdg = tmp_path / "xdg"
    env = {
        "XDG_CONFIG_HOME": str(xdg),
        "HOME": str(tmp_path / "h"),
        "APPDATA": str(tmp_path / "ap"),
    }
    assert cfg.config_dir(env, "linux") == xdg / "line-skill"
    assert cfg.config_dir(env, "macos") == xdg / "line-skill"
    assert cfg.config_dir(env, "windows") == tmp_path / "ap" / "line-skill"
    # relative/empty XDG values are ignored per the XDG spec -> ~/.config
    for bad in ("", "relative/dir"):
        env2 = {"XDG_CONFIG_HOME": bad, "HOME": str(tmp_path / "h")}
        assert cfg.config_dir(env2, "linux") == tmp_path / "h" / ".config" / "line-skill"


def test_default_archive_dir_per_os(tmp_path):
    env = {
        "HOME": str(tmp_path / "h"),
        "USERPROFILE": str(tmp_path / "h"),
        "LOCALAPPDATA": str(tmp_path / "local"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    assert cfg.default_archive_dir(env, "windows") == tmp_path / "local" / "line-skill" / "archive"
    assert cfg.default_archive_dir(env, "macos") == (
        tmp_path / "h" / "Library" / "Application Support" / "line-skill" / "archive"
    )
    assert cfg.default_archive_dir(env, "linux") == tmp_path / "data" / "line-skill" / "archive"
    env.pop("XDG_DATA_HOME")
    assert cfg.default_archive_dir(env, "linux") == (
        tmp_path / "h" / ".local" / "share" / "line-skill" / "archive"
    )


def test_defaults_without_file(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "x"), "HOME": str(tmp_path)}
    conf = cfg.load_config(env, "linux")
    assert conf.config_exists is False
    assert conf.line_path is None
    assert conf.archive_source == "default"
    assert conf.download_types == ("image", "video", "audio", "file")
    assert conf.download_max_bytes == 20971520


def test_precedence_env_over_config_over_default(tmp_path):
    base = tmp_path / "x"
    write_config(base, 'archive_root = "' + (tmp_path / "from-config").as_posix() + '"\n')
    env = {"XDG_CONFIG_HOME": str(base), "HOME": str(tmp_path)}

    conf = cfg.load_config(env, "linux")
    assert (conf.archive_source, conf.archive_root) == ("config", tmp_path / "from-config")

    env["LINE_SKILL_ARCHIVE"] = str(tmp_path / "from-env")
    conf = cfg.load_config(env, "linux")
    assert (conf.archive_source, conf.archive_root) == ("env", tmp_path / "from-env")

    env["LINE_SKILL_ARCHIVE"] = "   "  # blank env var does not override
    assert cfg.load_config(env, "linux").archive_source == "config"


def test_full_config_file(tmp_path):
    base = tmp_path / "x"
    write_config(
        base,
        'line_path = "/opt/fork/line"\narchive_root = "arch"\n'
        '[download]\ntypes = ["image", "file", "image"]\nmax_bytes = 1000\n',
    )
    conf = cfg.load_config({"XDG_CONFIG_HOME": str(base), "HOME": str(tmp_path)}, "linux")
    assert conf.line_path == "/opt/fork/line"
    assert conf.archive_root == base / "line-skill" / "arch"  # relative -> config dir
    assert conf.download_types == ("image", "file")  # de-duplicated, order kept
    assert conf.download_max_bytes == 1000
    assert conf.to_dict()["download"] == {
        "types": ["image", "file"],
        "max_bytes": 1000,
        "line_emoji": True,
    }


def test_tilde_is_expanded(tmp_path):
    base = tmp_path / "x"
    write_config(base, 'archive_root = "~/chat-archive"\n')
    env = {
        "XDG_CONFIG_HOME": str(base),
        "HOME": str(tmp_path / "h"),
        "USERPROFILE": str(tmp_path / "h"),
    }
    conf = cfg.load_config(env, "linux")
    assert "~" not in str(conf.archive_root)
    assert conf.archive_root.name == "chat-archive"


@pytest.mark.parametrize(
    "body",
    [
        "this is = not toml [",
        '[download]\ntypes = ["gif"]\n',
        '[download]\ntypes = "image"\n',
        "[download]\nmax_bytes = 0\n",
        "[download]\nmax_bytes = true\n",
        "line_path = 5\n",
        "download = 3\n",
    ],
)
def test_invalid_config_raises_and_safe_loader_falls_back(tmp_path, body):
    base = tmp_path / "x"
    write_config(base, body)
    env = {"XDG_CONFIG_HOME": str(base), "HOME": str(tmp_path)}
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(env, "linux")
    conf, error = cfg.load_config_safe(env, "linux")
    assert error and conf.archive_source == "default"
    assert conf.download_max_bytes == cfg.DEFAULT_MAX_BYTES


def test_safe_loader_keeps_env_archive_override(tmp_path):
    base = tmp_path / "x"
    write_config(base, "broken [")
    env = {
        "XDG_CONFIG_HOME": str(base),
        "HOME": str(tmp_path),
        "LINE_SKILL_ARCHIVE": str(tmp_path / "e"),
    }
    conf, error = cfg.load_config_safe(env, "linux")
    assert error and conf.archive_root == tmp_path / "e"


def test_unknown_keys_are_warnings(tmp_path):
    base = tmp_path / "x"
    write_config(base, 'colour = "red"\n[download]\nspeed = 1\n')
    conf = cfg.load_config({"XDG_CONFIG_HOME": str(base), "HOME": str(tmp_path)}, "linux")
    assert len(conf.warnings) == 2


def test_render_quiet_gap_days_default_custom_and_invalid(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "x"), "HOME": str(tmp_path)}
    assert cfg.load_config(env, "linux").render_quiet_gap_days == 3
    write_config(tmp_path / "x", "[render]\nquiet_gap_days = 0\n")
    conf = cfg.load_config(env, "linux")
    assert conf.render_quiet_gap_days == 0
    assert conf.to_dict()["render"] == {"quiet_gap_days": 0}
    write_config(tmp_path / "x", "[render]\nquiet_gap_days = -1\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(env, "linux")


def test_watch_vip_chats_default_valid_and_invalid(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "x"), "HOME": str(tmp_path)}
    assert cfg.load_config(env, "linux").watch_vip_chats == ()
    write_config(tmp_path / "x", '[watch]\nvip_chats = [" Alice ", "Project Team", "Alice"]\n')
    conf = cfg.load_config(env, "linux")
    assert conf.watch_vip_chats == ("Alice", "Project Team")  # trimmed, de-duplicated
    assert conf.to_dict()["watch"] == {"vip_chats": ["Alice", "Project Team"], "archive_chats": []}
    for bad in ('vip_chats = "Alice"', "vip_chats = [1]", 'vip_chats = [""]'):
        write_config(tmp_path / "x", f"[watch]\n{bad}\n")
        with pytest.raises(cfg.ConfigError, match="vip_chats"):
            cfg.load_config(env, "linux")
    write_config(tmp_path / "x", "watch = 1\n")
    with pytest.raises(cfg.ConfigError, match="watch"):
        cfg.load_config(env, "linux")
    write_config(tmp_path / "x", "[watch]\nsound = true\n")
    assert any("watch.sound" in w for w in cfg.load_config(env, "linux").warnings)


def test_watch_archive_chats_is_an_allowlist_of_names_or_ids(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "x"), "HOME": str(tmp_path)}
    assert cfg.load_config(env, "linux").watch_archive_chats == ()  # nothing allowed by default
    write_config(tmp_path / "x", '[watch]\narchive_chats = ["Family", "C" , "Family"]\n')
    conf = cfg.load_config(env, "linux")
    assert conf.watch_archive_chats == ("Family", "C")
    assert conf.to_dict()["watch"]["archive_chats"] == ["Family", "C"]
    write_config(tmp_path / "x", "[watch]\narchive_chats = [3]\n")
    with pytest.raises(cfg.ConfigError, match="archive_chats"):
        cfg.load_config(env, "linux")
