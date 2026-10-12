"""openai_compat adapter against a local http.server stub (multipart parsing, languages vs
language, response formats, too_large, exit-code mapping). No network."""

import json
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from adapters import openai_compat
from mdcore import asr


class Stub:
    def __init__(self):
        self.requests = []
        self.responses = []  # queue of (status, body-str)
        self.default = (200, json.dumps({"text": "ok"}))

        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                head = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
                msg = BytesParser(policy=HTTP).parsebytes(head + raw)
                fields = []
                for part in msg.iter_parts():
                    fields.append(
                        (
                            part.get_param("name", header="content-disposition"),
                            part.get_filename(),
                            part.get_payload(decode=True),
                        )
                    )
                stub.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "fields": fields}
                )
                status, body = stub.responses.pop(0) if stub.responses else stub.default
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def form(self, i=-1):
        out = {}
        for name, filename, payload in self.requests[i]["fields"]:
            out.setdefault(name, []).append((filename, payload))
        return out

    def text_fields(self, i=-1):
        return {k: [v[1].decode() for v in vs] for k, vs in self.form(i).items() if k != "file"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


@pytest.fixture
def audio(tmp_path):
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 996)
    return str(p)


def run_adapter(capsys, *argv):
    code = openai_compat.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_language_and_multipart(stub, audio, capsys, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sekret")
    stub.default = (
        200,
        json.dumps(
            {
                "text": " hello world",
                "language": "english",
                "duration": 3.0,
                "segments": [
                    {"start": 0, "end": 1.2, "text": " hello"},
                    {"start": 1.2, "end": 3.0, "text": "world"},
                ],
            }
        ),
    )
    code, out, err = run_adapter(
        capsys, "--audio", audio, "--lang", "zh-TW", "--prompt", "術語",
        "--model", "whisper-large-v3-turbo", "--base-url", stub.url,
        "--api-key-env", "TEST_KEY", "--extra", "temperature=0",
    )  # fmt: skip
    assert code == 0, err
    req = stub.requests[0]
    assert req["path"] == "/v1/audio/transcriptions" and req["auth"] == "Bearer sekret"
    fields = stub.text_fields()
    assert fields["language"] == ["zh"] and "languages[]" not in fields
    assert fields["model"] == ["whisper-large-v3-turbo"]
    assert fields["response_format"] == ["verbose_json"]  # whisper family -> timestamps
    assert fields["prompt"] == ["術語"] and fields["temperature"] == ["0"]
    filename, payload = stub.form()["file"][0]
    assert filename == "clip.wav" and payload.startswith(b"RIFF") and len(payload) == 1000
    result = json.loads(out)
    assert result["language"] == "en" and result["text"] == "hello world"
    assert result["segments"] == [
        {"start": 0.0, "end": 1.2, "text": "hello"},
        {"start": 1.2, "end": 3.0, "text": "world"},
    ]
    assert "sekret" not in out + err


def test_gpt_transcribe_sends_languages_not_language(stub, audio, capsys):
    stub.default = (200, json.dumps({"text": "你好", "languages": [{"code": "zh"}]}))
    code, out, _ = run_adapter(
        capsys,
        "--audio",
        audio,
        "--lang",
        "zh",
        "--model",
        "gpt-transcribe",
        "--base-url",
        stub.url,
    )
    assert code == 0
    fields = stub.text_fields()
    assert fields["languages[]"] == ["zh"] and "language" not in fields
    assert fields["response_format"] == ["json"]
    r = json.loads(out)
    assert r["language"] == "zh" and r["segments"] == [{"start": 0.0, "end": 0.0, "text": "你好"}]


def test_auto_sends_no_language_field(stub, audio, capsys):
    for model in ("gpt-transcribe", "whisper-1"):
        run_adapter(capsys, "--audio", audio, "--lang", "auto", "--model", model,
                    "--base-url", stub.url)  # fmt: skip
    for i in (0, 1):
        assert not ({"language", "languages[]", "languages"} & set(stub.text_fields(i)))


def test_language_field_override_none(stub, audio, capsys):
    run_adapter(capsys, "--audio", audio, "--lang", "ja", "--model", "whisper-1",
                "--language-field", "none", "--base-url", stub.url)  # fmt: skip
    assert "language" not in stub.text_fields()


def test_no_key_header_for_keyless_local_servers(stub, audio, capsys):
    assert run_adapter(capsys, "--audio", audio, "--base-url", stub.url)[0] == 0
    assert stub.requests[0]["auth"] is None


def test_too_large_exits_2(stub, audio, capsys):
    code, out, err = run_adapter(capsys, "--audio", audio, "--max-mb", "0.0005",
                                 "--base-url", stub.url)  # fmt: skip
    assert code == 2 and out == "" and err.strip().startswith("too_large")
    assert stub.requests == []  # nothing uploaded


def test_missing_env_exits_2(stub, audio, capsys, monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    code, _, err = run_adapter(capsys, "--audio", audio, "--base-url", stub.url,
                               "--api-key-env", "NOPE_KEY")  # fmt: skip
    assert code == 2 and err.startswith("missing_env") and "NOPE_KEY" in err
    assert stub.requests == []


@pytest.mark.parametrize(
    "status, expected, prefix",
    [(401, 2, "auth_failed"), (403, 2, "auth_failed"), (413, 2, "too_large"),
     (500, 1, "http_error"), (404, 1, "http_error")],
)  # fmt: skip
def test_http_error_mapping(stub, audio, capsys, status, expected, prefix):
    stub.default = (status, json.dumps({"error": "x"}))
    code, out, err = run_adapter(capsys, "--audio", audio, "--model", "gpt-4o-transcribe",
                                 "--base-url", stub.url)  # fmt: skip
    assert code == expected and out == "" and err.startswith(prefix)


def test_unreachable_exits_2(audio, capsys):
    code, _, err = run_adapter(capsys, "--audio", audio, "--base-url", "http://127.0.0.1:1/v1")
    assert code == 2 and err.startswith("unreachable")


def test_verbose_json_rejected_retries_with_json(stub, audio, capsys):
    stub.responses = [(400, json.dumps({"error": "unsupported response_format"}))]
    stub.default = (200, json.dumps({"text": "fallback ok"}))
    code, out, _ = run_adapter(capsys, "--audio", audio, "--model", "whisper-large-v3",
                               "--base-url", stub.url)  # fmt: skip
    assert code == 0 and json.loads(out)["text"] == "fallback ok"
    assert [stub.text_fields(i)["response_format"] for i in (0, 1)] == [["verbose_json"], ["json"]]


def test_explicit_response_format_is_not_retried(stub, audio, capsys):
    stub.default = (400, "{}")
    code, _, _ = run_adapter(capsys, "--audio", audio, "--response-format", "verbose_json",
                             "--model", "whisper-1", "--base-url", stub.url)  # fmt: skip
    assert code == 1 and len(stub.requests) == 1


def test_text_response_format(stub, audio, capsys):
    stub.default = (200, "plain transcript")
    code, out, _ = run_adapter(capsys, "--audio", audio, "--response-format", "text",
                               "--base-url", stub.url)  # fmt: skip
    assert code == 0 and json.loads(out)["text"] == "plain transcript"


@pytest.mark.parametrize(
    "args, available, fragment",
    [
        ([], False, "no_base_url"),
        (["--base-url", "https://api.groq.com/openai/v1", "--api-key-env", "UNSET_KEY_XYZ"],
         False, "missing_env"),
        (["--base-url", "http://localhost:8000/v1"], True, "local endpoint"),
        (["--base-url", "https://api.openai.com/v1"], True, "audio leaves this machine"),
    ],
)  # fmt: skip
def test_probe(capsys, monkeypatch, args, available, fragment):
    monkeypatch.delenv("UNSET_KEY_XYZ", raising=False)
    code, out, _ = run_adapter(capsys, "--probe", *args)
    info = json.loads(out)
    assert code == 0 and info["available"] is available and fragment in info["reason"]
    assert "install_hint" in info


def test_through_dispatcher_with_fallback(stub, audio, tmp_path, monkeypatch):
    """transcribe(): too_large (exit 2) on the first adapter falls back to the next one."""
    monkeypatch.setenv("TEST_KEY", "k")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
[asr]
order = ["small_limit", "stub_ok"]
[adapters.small_limit]
type = "openai_compat"
base_url = "{stub.url}"
max_mb = 0.0005
[adapters.stub_ok]
type = "openai_compat"
base_url = "{stub.url}"
model = "whisper-1"
api_key_env = "TEST_KEY"
extra = {{ temperature = "0.2" }}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MEDIA_DIGEST_CONFIG", str(cfg))
    stub.default = (200, json.dumps({"text": "via stub", "language": "chinese"}))
    r = asr.transcribe(audio, "zh", prompt="p")
    assert r["ok"] and r["adapter"] == "stub_ok" and r["text"] == "via stub"
    assert r["language"] == "zh"
    assert [(a["adapter"], a["exit_code"]) for a in r["attempts"]] == [
        ("small_limit", 2),
        ("stub_ok", 0),
    ]
    assert r["attempts"][0]["reason"].startswith("too_large")
    f = stub.text_fields()
    assert f["language"] == ["zh"] and f["prompt"] == ["p"] and f["temperature"] == ["0.2"]
    assert stub.requests[0]["auth"] == "Bearer k"
