"""mdcore.config: path resolution, schema validation, presets, routing, privacy defaults."""

import sys
import textwrap
from pathlib import Path

import pytest
from mdcore import config as cfg

SAMPLE = """
[asr]
order = ["faster_whisper", "whisper_cpp", "groq"]

[asr.routes]
zh = ["my-qwen3", "faster_whisper"]
ja = ["faster_whisper"]

[adapters.groq]
type = "openai_compat"
base_url = "https://api.groq.com/openai/v1"
model = "whisper-large-v3-turbo"
api_key_env = "GROQ_API_KEY"

[adapters.my-qwen3]
type = "command"
cmd = ["uv", "run", "~/asr/qwen3_svc.py", "--audio", "{audio}", "--lang", "{lang}"]
"""


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_config_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_DIGEST_CONFIG", str(tmp_path / "x.toml"))
    assert cfg.config_path() == tmp_path / "x.toml"


def test_config_path_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("MEDIA_DIGEST_CONFIG", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfg.config_path() == tmp_path / "media-digest" / "config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert cfg.config_path() == Path.home() / ".config" / "media-digest" / "config.toml"


def test_config_path_windows_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("MEDIA_DIGEST_CONFIG", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cfg.config_path() == tmp_path / "media-digest" / "config.toml"


def test_missing_file_gives_local_only_defaults(tmp_path):
    c = cfg.load_config(tmp_path / "nope.toml")
    assert c["exists"] is False
    assert c["order"] == ["faster_whisper", "whisper_cpp"]
    assert not any(cfg.is_cloud(s) for s in c["adapters"].values())


def test_parse_plan_sample(tmp_path):
    c = cfg.load_config(write(tmp_path, SAMPLE))
    assert c["exists"] is True
    assert c["order"] == ["faster_whisper", "whisper_cpp", "groq"]
    assert c["routes"] == {"zh": ["my-qwen3", "faster_whisper"], "ja": ["faster_whisper"]}
    assert c["adapters"]["groq"]["type"] == "openai_compat"
    assert c["adapters"]["my-qwen3"]["cmd"][0] == "uv"
    assert cfg.is_cloud(c["adapters"]["groq"])
    assert not cfg.is_cloud(c["adapters"]["my-qwen3"])


def test_preset_fills_defaults_and_user_overrides(tmp_path):
    c = cfg.load_config(
        write(
            tmp_path,
            """
        [asr]
        order = ["fast"]
        [adapters.fast]
        preset = "groq"
        model = "whisper-large-v3"
    """,
        )
    )
    spec = c["adapters"]["fast"]
    assert spec["type"] == "openai_compat"
    assert spec["base_url"] == "https://api.groq.com/openai/v1"
    assert spec["api_key_env"] == "GROQ_API_KEY"
    assert spec["model"] == "whisper-large-v3"


def test_bare_preset_name_in_order_is_explicit_opt_in(tmp_path):
    c = cfg.load_config(write(tmp_path, '[asr]\norder = ["faster_whisper", "groq"]\n'))
    assert c["adapters"]["groq"]["api_key_env"] == "GROQ_API_KEY"
    assert cfg.is_cloud(c["adapters"]["groq"])


@pytest.mark.parametrize("name", ["openai", "groq", "vllm", "speaches"])
def test_presets_are_valid(name, tmp_path):
    c = cfg.load_config(write(tmp_path, f'[asr]\norder = ["{name}"]\n'))
    spec = c["adapters"][name]
    assert spec["base_url"].endswith("/v1") and spec["model"]
    assert cfg.is_cloud(spec) is (name in {"openai", "groq"})


def test_cloud_adapter_defined_but_unlisted_is_never_routed(tmp_path):
    c = cfg.load_config(
        write(
            tmp_path,
            """
        [asr]
        order = ["faster_whisper"]
        [adapters.groq]
        preset = "groq"
    """,
        )
    )
    assert "groq" in c["adapters"]
    for lang in ("auto", "zh", "ja", "xx"):
        assert "groq" not in cfg.route_for(c, lang)


def test_route_for(tmp_path):
    c = cfg.load_config(write(tmp_path, SAMPLE))
    assert cfg.route_for(c, "zh") == ["my-qwen3", "faster_whisper"]
    assert cfg.route_for(c, "zh-TW") == ["my-qwen3", "faster_whisper"]
    assert cfg.route_for(c, "ZH_tw") == ["my-qwen3", "faster_whisper"]
    assert cfg.route_for(c, "ja") == ["faster_whisper"]
    assert cfg.route_for(c, "auto") == c["order"]
    assert cfg.route_for(c, "th") == c["order"]


def test_auto_route(tmp_path):
    c = cfg.load_config(
        write(tmp_path, '[asr]\norder=["whisper_cpp"]\n[asr.routes]\nauto=["faster_whisper"]\n')
    )
    assert cfg.route_for(c, "auto") == ["faster_whisper"]


def test_builtin_adapter_table_can_set_options(tmp_path):
    c = cfg.load_config(
        write(
            tmp_path,
            """
        [adapters.whisper_cpp]
        model_path = "/models/ggml-large-v3-turbo.bin"
        threads = 4
        [adapters.faster_whisper]
        model = "small"
        device = "cpu"
    """,
        )
    )
    assert c["adapters"]["whisper_cpp"]["type"] == "whisper_cpp"
    assert c["adapters"]["whisper_cpp"]["threads"] == 4
    assert c["adapters"]["faster_whisper"]["model"] == "small"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ("[asr\n", "invalid TOML"),
        ('[asr]\norder = ["nope"]\n', "no [adapters.nope]"),
        ('[asr]\norder = "faster_whisper"\n', "list of non-empty strings"),
        ('[adapters.x]\ntype = "weird"\n', "'type' must be one of"),
        ('[adapters.x]\npreset = "nope"\n', "unknown preset"),
        ('[adapters.x]\ntype = "command"\n', "needs 'cmd'"),
        ('[adapters.x]\ntype = "command"\ncmd = "my-asr"\n', "list of non-empty strings"),
        ('[adapters.x]\ntype = "openai_compat"\n', "needs 'base_url'"),
        ('[adapters.x]\ntype = "openai_compat"\nbase_url = "http://h"\nbogus = 1\n', "unknown key"),
        ('[adapters.x]\ntype = "openai_compat"\nbase_url = "http://h"\ntimeout = -1\n', "positive"),
        (
            '[adapters.x]\ntype = "openai_compat"\nbase_url = "http://h"\nlanguage_field = "z"\n',
            "language_field",
        ),
    ],
)
def test_invalid_configs(tmp_path, body, fragment):
    with pytest.raises(cfg.ConfigError, match=fragment.replace("[", r"\[").replace("]", r"\]")):
        cfg.load_config(write(tmp_path, body))


def test_secret_values_are_not_a_config_key(tmp_path):
    # Only the *name* of an env var is accepted; a literal api_key must be rejected.
    with pytest.raises(cfg.ConfigError, match="unknown key"):
        cfg.load_config(
            write(
                tmp_path,
                """
            [adapters.x]
            type = "openai_compat"
            base_url = "https://example.com/v1"
            api_key = "sk-literal"
        """,
            )
        )


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:8000/v1", True),
        ("http://127.0.0.1:1/v1", True),
        ("http://[::1]:8000/v1", True),
        ("https://api.openai.com/v1", False),
        ("http://gpu-box.lan:8000/v1", False),
    ],
)
def test_is_local_url(url, local):
    assert cfg.is_local_url(url) is local
