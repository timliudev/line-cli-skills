"""Built-in local adapters (whisper_cpp, faster_whisper) without real binaries or models, plus
mdcore.asr.list_adapters() probing. No network."""

import json
import struct
import subprocess
import sys
import wave

import pytest
from adapters import faster_whisper, whisper_cpp
from mdcore import asr


def make_wav(path, rate=16000, width=2, seconds=0.1):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return str(path)


# ----------------------------------------------------------------------------- whisper_cpp

WHISPER_JSON = {
    "result": {"language": "zh"},
    "transcription": [
        {"timestamps": {"from": "00:00:00,000", "to": "00:00:03,200"},
         "offsets": {"from": 0, "to": 3200}, "text": " 你好"},
        {"offsets": {"from": 3200, "to": 5000}, "text": " world "},
        {"offsets": {"from": 5000, "to": 6000}, "text": "  "},
    ],
}  # fmt: skip


def test_parse_whisper_json():
    r = whisper_cpp.parse_whisper_json(WHISPER_JSON)
    assert r["language"] == "zh" and r["text"] == "你好 world"
    assert r["segments"] == [
        {"start": 0.0, "end": 3.2, "text": "你好"},
        {"start": 3.2, "end": 5.0, "text": "world"},
    ]


@pytest.fixture
def cpp_env(tmp_path, monkeypatch):
    model = tmp_path / "ggml-test.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(whisper_cpp, "find_binary", lambda name: f"/fake/{name}")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = cmd[cmd.index("-of") + 1] + ".json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(WHISPER_JSON, fh)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(whisper_cpp.subprocess, "run", fake_run)
    return model, calls


def test_whisper_cpp_success_builds_command(cpp_env, tmp_path, capsys):
    model, calls = cpp_env
    wav = make_wav(tmp_path / "a.wav")
    code = whisper_cpp.main(["--audio", wav, "--lang", "zh-TW", "--prompt", "詞", "--model-path",
                             str(model), "--threads", "3"])  # fmt: skip
    out = capsys.readouterr()
    assert code == 0, out.err
    cmd = calls[0]
    assert cmd[0] == "/fake/whisper-cli"
    assert cmd[cmd.index("-m") + 1] == str(model) and cmd[cmd.index("-f") + 1] == wav
    assert cmd[cmd.index("-l") + 1] == "zh" and "-oj" in cmd
    assert cmd[cmd.index("--prompt") + 1] == "詞" and cmd[cmd.index("-t") + 1] == "3"
    assert json.loads(out.out)["segments"][0]["end"] == 3.2


def test_whisper_cpp_auto_language(cpp_env, tmp_path, capsys):
    model, calls = cpp_env
    whisper_cpp.main(["--audio", make_wav(tmp_path / "a.wav"), "--model-path", str(model)])
    assert calls[0][calls[0].index("-l") + 1] == "auto"


@pytest.mark.parametrize("kwargs, prefix", [
    ({"rate": 44100}, "needs_16khz_wav"),
    ({"width": 1}, "needs_16khz_wav"),
])  # fmt: skip
def test_whisper_cpp_rejects_non_16k_wav_with_exit_2(cpp_env, tmp_path, capsys, kwargs, prefix):
    model, calls = cpp_env
    wav = make_wav(tmp_path / "bad.wav", **kwargs)
    assert whisper_cpp.main(["--audio", wav, "--model-path", str(model)]) == 2
    assert capsys.readouterr().err.startswith(prefix) and calls == []


def test_whisper_cpp_non_wav_exit_2(cpp_env, tmp_path, capsys):
    model, _ = cpp_env
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"ID3")
    assert whisper_cpp.main(["--audio", str(mp3), "--model-path", str(model)]) == 2


def test_whisper_cpp_environment_problems_exit_2(tmp_path, capsys, monkeypatch):
    wav = make_wav(tmp_path / "a.wav")
    monkeypatch.setattr(whisper_cpp, "find_binary", lambda name: None)
    assert whisper_cpp.main(["--audio", wav, "--model-path", "m.bin"]) == 2
    assert capsys.readouterr().err.startswith("not_found")
    monkeypatch.setattr(whisper_cpp, "find_binary", lambda name: "/fake/x")
    assert whisper_cpp.main(["--audio", wav]) == 2
    assert capsys.readouterr().err.startswith("no_model_path")
    assert whisper_cpp.main(["--audio", wav, "--model-path", str(tmp_path / "missing.bin")]) == 2
    assert capsys.readouterr().err.startswith("model_missing")


def test_whisper_cpp_process_failure_exit_1(cpp_env, tmp_path, capsys, monkeypatch):
    model, _ = cpp_env
    monkeypatch.setattr(
        whisper_cpp.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", "error: bad model\n"),
    )  # fmt: skip
    assert (
        whisper_cpp.main(["--audio", make_wav(tmp_path / "a.wav"), "--model-path", str(model)]) == 1
    )
    assert capsys.readouterr().err.startswith("whisper_cli_failed")


def test_whisper_cpp_probe(tmp_path, capsys, monkeypatch):
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(whisper_cpp, "find_binary", lambda name: None)
    whisper_cpp.main(["--probe"])
    assert json.loads(capsys.readouterr().out)["available"] is False
    monkeypatch.setattr(whisper_cpp, "find_binary", lambda name: "/fake/whisper-cli")
    whisper_cpp.main(["--probe"])
    assert "no_model_path" in json.loads(capsys.readouterr().out)["reason"]
    whisper_cpp.main(["--probe", "--model-path", str(model)])
    assert json.loads(capsys.readouterr().out)["available"] is True


# --------------------------------------------------------------------------- faster_whisper


def test_faster_whisper_has_pep723_block():
    src = faster_whisper.Path(faster_whisper.__file__).read_text(encoding="utf-8")
    assert "# /// script" in src and 'dependencies = ["faster-whisper>=1.2.1"]' in src
    assert 'requires-python = ">=3.11"' in src


def test_faster_whisper_probe_reports_cache_and_download_size(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setattr(faster_whisper.shutil, "which", lambda name: "/fake/uv")
    faster_whisper.main(["--probe"])
    info = json.loads(capsys.readouterr().out)
    assert info["available"] is True and info["model_cached"] is False
    assert info["download_size"] == "~1.6 GB" and "large-v3-turbo" in info["reason"]

    snap = tmp_path / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots"
    snap.mkdir(parents=True)
    faster_whisper.main(["--probe"])
    info = json.loads(capsys.readouterr().out)
    assert info["model_cached"] is True and info["download_size"] is None


def test_faster_whisper_probe_does_not_import_heavy_deps(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # any import would raise
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    monkeypatch.setattr(faster_whisper.shutil, "which", lambda name: "/fake/uv")
    assert faster_whisper.main(["--probe"]) == 0
    assert json.loads(capsys.readouterr().out)["available"] is True


def test_faster_whisper_probe_unavailable_without_uv_or_package(capsys, monkeypatch):
    monkeypatch.setattr(faster_whisper.shutil, "which", lambda name: None)
    monkeypatch.setattr(faster_whisper.importlib.util, "find_spec", lambda name: None)
    faster_whisper.main(["--probe"])
    info = json.loads(capsys.readouterr().out)
    assert info["available"] is False and info["reason"].startswith("uv_not_found")
    assert "uv" in info["install_hint"]


def test_faster_whisper_run_without_package_exits_2(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    wav = make_wav(tmp_path / "a.wav")
    assert faster_whisper.main(["--audio", wav]) == 2
    assert capsys.readouterr().err.startswith("missing_dependency")


def test_faster_whisper_run_with_fake_module(tmp_path, capsys, monkeypatch):
    class Seg:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    class Info:
        language = "ja"

    seen = {}

    class FakeModel:
        def __init__(self, name, device, compute_type):
            seen["init"] = (name, device, compute_type)

        def transcribe(self, audio, **kw):
            seen["kw"] = kw
            return iter([Seg(0, 1.234567, " こんにちは "), Seg(1.3, 2, "x")]), Info()

    fake = type(sys)("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # forces cpu
    wav = make_wav(tmp_path / "a.wav")
    assert faster_whisper.main(["--audio", wav, "--lang", "ja_JP", "--prompt", "p"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert seen["init"] == ("large-v3-turbo", "cpu", "int8")
    assert seen["kw"]["language"] == "ja" and seen["kw"]["initial_prompt"] == "p"
    assert r["language"] == "ja" and r["text"] == "こんにちは x"
    assert r["segments"][0] == {"start": 0.0, "end": 1.235, "text": "こんにちは"}


# ----------------------------------------------------------------------------- list_adapters


def test_list_adapters(tmp_path, monkeypatch):
    probe_script = tmp_path / "probe.py"
    probe_script.write_text(
        'import json; print(json.dumps({"available": True, "reason": "fine"}))',
        encoding="utf-8",
    )
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
[asr]
order = ["whisper_cpp", "with_probe", "no_probe_found", "no_probe_missing", "cloudy"]
[adapters.with_probe]
type = "command"
cmd = ["whatever"]
probe_cmd = [{json.dumps(sys.executable)}, {json.dumps(str(probe_script))}]
[adapters.no_probe_found]
type = "command"
cmd = [{json.dumps(sys.executable)}, "x.py"]
[adapters.no_probe_missing]
type = "command"
cmd = ["no-such-binary-xyz"]
install_hint = "pip install thing"
[adapters.cloudy]
type = "openai_compat"
base_url = "https://api.example.com/v1"
api_key_env = "CLOUDY_KEY_UNSET_XYZ"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIA_DIGEST_CONFIG", str(cfg))
    monkeypatch.delenv("CLOUDY_KEY_UNSET_XYZ", raising=False)
    rows = {r["name"]: r for r in asr.list_adapters()}
    assert set(rows) == {"faster_whisper", "whisper_cpp", "with_probe", "no_probe_found",
                         "no_probe_missing", "cloudy"}  # fmt: skip
    for row in rows.values():
        assert {"name", "type", "available", "reason", "install_hint"} <= set(row)
    assert rows["faster_whisper"]["type"] == "faster_whisper"
    assert isinstance(rows["faster_whisper"]["available"], bool)  # probe ran, no heavy import
    assert rows["whisper_cpp"]["type"] == "whisper_cpp"
    assert rows["with_probe"]["available"] is True and rows["with_probe"]["reason"] == "fine"
    assert rows["no_probe_found"]["available"] is True
    assert rows["no_probe_missing"]["available"] is False
    assert rows["no_probe_missing"]["install_hint"] == "pip install thing"
    assert rows["cloudy"]["available"] is False and "missing_env" in rows["cloudy"]["reason"]
    assert rows["cloudy"]["cloud"] is True and rows["whisper_cpp"]["cloud"] is False


def test_list_adapters_reports_config_errors(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[asr\n", encoding="utf-8")
    monkeypatch.setenv("MEDIA_DIGEST_CONFIG", str(cfg))
    rows = asr.list_adapters()
    assert len(rows) == 1 and rows[0]["available"] is False and "invalid TOML" in rows[0]["reason"]
