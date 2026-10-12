"""mdcore.asr.transcribe: routing, fallback chain, exit-code semantics, command templates.

Uses tiny fake adapters (python scripts written to tmp) wired in as `command` adapters.
No network, no real models.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
from mdcore import asr

FAKES = {
    "ok": """
        import json, sys
        print(json.dumps({"text": "hello", "language": "en",
                          "segments": [{"start": 0, "end": 1.5, "text": "hello"}]}))
    """,
    "env2": """
        import sys
        print("some_dependency_missing: install it", file=sys.stderr)
        sys.exit(2)
    """,
    "fail1": """
        import sys
        print("boom: model crashed", file=sys.stderr)
        sys.exit(1)
    """,
    "plain": 'print("just some words")',
    "badjson": "print('{\"unrelated\": 1}')",
    "empty": "pass",
    "sleepy": "import time; time.sleep(30)",
    "echo": """
        import json, sys
        print(json.dumps({"text": "|".join(sys.argv[1:]), "language": None, "segments": []}))
    """,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Returns helper to write config + fake adapters; points MEDIA_DIGEST_CONFIG at tmp."""
    scripts = tmp_path / "fakes"
    scripts.mkdir()
    for name, body in FAKES.items():
        (scripts / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000")
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_DIGEST_CONFIG", str(cfg_path))

    class Env:
        pass

    e = Env()
    e.audio = str(audio)
    e.scripts = scripts

    def write(order, routes=None, extra_adapters=""):
        lines = [f"[asr]\norder = {json.dumps(order)}\n"]
        if routes:
            lines.append("[asr.routes]")
            lines += [f"{k} = {json.dumps(v)}" for k, v in routes.items()]
        for name in FAKES:
            cmd = [
                sys.executable,
                str(scripts / f"{name}.py"),
                "{audio}",
                "{lang}",
                "{prompt}",
                "{model}",
            ]
            lines.append(f'\n[adapters.fake_{name}]\ntype = "command"\ncmd = {json.dumps(cmd)}')
            if name == "echo":
                lines.append('model = "m1"')
            if name == "sleepy":
                lines.append("timeout = 0.5")
        lines.append(extra_adapters)
        cfg_path.write_text("\n".join(lines), encoding="utf-8")

    e.write = write
    return e


def codes(result):
    return [(a["adapter"], a["exit_code"]) for a in result["attempts"]]


def test_first_success_stops_chain(env):
    env.write(["fake_ok", "fake_fail1"])
    r = asr.transcribe(env.audio)
    assert r["ok"] and r["adapter"] == "fake_ok" and r["error"] is None
    assert r["text"] == "hello" and r["language"] == "en"
    assert r["segments"] == [{"start": 0.0, "end": 1.5, "text": "hello"}]
    assert codes(r) == [("fake_ok", 0)]  # fail1 never ran


def test_exit2_skips_and_exit1_falls_through(env):
    env.write(["fake_env2", "fake_fail1", "fake_ok"])
    r = asr.transcribe(env.audio)
    assert r["ok"] and r["adapter"] == "fake_ok"
    assert codes(r) == [("fake_env2", 2), ("fake_fail1", 1), ("fake_ok", 0)]
    assert r["attempts"][0]["reason"].startswith("some_dependency_missing")
    assert r["attempts"][1]["reason"].startswith("boom")
    assert r["attempts"][2]["reason"] is None


def test_all_fail(env):
    env.write(["fake_env2", "fake_fail1"])
    r = asr.transcribe(env.audio)
    assert r["ok"] is False and r["adapter"] is None and r["text"] == "" and r["segments"] == []
    assert "all adapters failed" in r["error"] and "boom" in r["error"]
    assert len(r["attempts"]) == 2


def test_duplicate_names_run_once(env):
    env.write(["fake_fail1", "fake_fail1", "fake_ok"])
    assert [a["adapter"] for a in asr.transcribe(env.audio)["attempts"]] == [
        "fake_fail1",
        "fake_ok",
    ]


def test_language_routes(env):
    env.write(["fake_ok"], routes={"zh": ["fake_plain"], "auto": ["fake_echo"]})
    assert asr.transcribe(env.audio, "zh")["adapter"] == "fake_plain"
    assert asr.transcribe(env.audio, "zh-TW")["adapter"] == "fake_plain"
    assert asr.transcribe(env.audio, "auto")["adapter"] == "fake_echo"
    assert asr.transcribe(env.audio, "ja")["adapter"] == "fake_ok"  # falls to order


def test_route_is_used_instead_of_order(env):
    env.write(["fake_ok"], routes={"zh": ["fake_env2"]})
    r = asr.transcribe(env.audio, "zh")
    assert r["ok"] is False and codes(r) == [("fake_env2", 2)]


def test_command_placeholders(env):
    env.write(["fake_echo"])
    r = asr.transcribe(env.audio, "zh", prompt="術語 A")
    assert r["ok"]
    assert r["text"] == "|".join([env.audio, "zh", "術語 A", "m1"])
    r2 = asr.transcribe(env.audio, "auto")  # prompt None -> empty string
    assert r2["text"].split("|")[1:] == ["auto", "", "m1"]


def test_plain_text_becomes_single_segment(env):
    env.write(["fake_plain"])
    r = asr.transcribe(env.audio)
    assert r["ok"] and r["text"] == "just some words" and r["language"] is None
    assert r["segments"] == [{"start": 0.0, "end": 0.0, "text": "just some words"}]


@pytest.mark.parametrize("name", ["fake_badjson", "fake_empty"])
def test_unusable_output_falls_through(env, name):
    env.write([name, "fake_ok"])
    r = asr.transcribe(env.audio)
    assert r["ok"] and r["adapter"] == "fake_ok"
    assert r["attempts"][0]["exit_code"] == 0
    assert r["attempts"][0]["reason"].startswith("bad_output")


def test_timeout_is_recorded_and_chain_continues(env):
    env.write(["fake_sleepy", "fake_ok"])
    r = asr.transcribe(env.audio)
    assert r["ok"] and r["attempts"][0]["exit_code"] is None
    assert r["attempts"][0]["reason"].startswith("timeout")


def test_missing_executable_counts_as_environment_unavailable(env):
    env.write(
        ["ghost", "fake_ok"],
        extra_adapters='[adapters.ghost]\ntype="command"\ncmd=["no-such-binary-xyz","{audio}"]\n',
    )
    r = asr.transcribe(env.audio)
    assert r["ok"] and codes(r)[0] == ("ghost", 2)
    assert r["attempts"][0]["reason"].startswith("not_found")


def test_missing_audio_file(env):
    env.write(["fake_ok"])
    r = asr.transcribe(str(Path(env.audio).with_name("nope.wav")))
    assert r["ok"] is False and "not found" in r["error"] and r["attempts"] == []


def test_config_error_is_reported_not_raised(env, tmp_path):
    (tmp_path / "config.toml").write_text(
        '[asr]\norder = ["undefined_adapter"]\n', encoding="utf-8"
    )
    r = asr.transcribe(env.audio)
    assert r["ok"] is False and r["error"].startswith("config:")
    assert set(r) >= {"ok", "adapter", "text", "language", "segments", "attempts", "error"}


def test_parse_output_normalises():
    p = asr.parse_output(json.dumps({"segments": [{"start": "1", "end": 2, "text": " hi "}]}))
    assert p == {
        "text": "hi",
        "language": None,
        "segments": [{"start": 1.0, "end": 2.0, "text": "hi"}],
    }
    assert asr.parse_output("noise line\n" + json.dumps({"text": "x"}))["text"] == "x"
    assert asr.parse_output("") is None
    assert asr.parse_output("[1,2]") is None
