# Installing the pieces

Versions checked on 2026-10-12. Newer versions normally work; re-check if something breaks.
Always tell the user what will be downloaded and roughly how big it is, and get a yes before
running an install. Sizes below are approximate.

## Contents

- What is needed for what
- ffmpeg and ffprobe
- uv
- faster-whisper (local, easiest)
- whisper.cpp (local)
- Cloud and self-hosted services
- Check the result

## What is needed for what

| You want | You need |
| --- | --- |
| Images and PDFs | nothing (the agent reads them directly) |
| Video keyframes | ffmpeg + ffprobe |
| Transcripts of video or audio | ffmpeg + ffprobe, plus one ASR backend |
| Easiest local ASR | uv (then `faster_whisper` installs itself on first use) |
| ASR without Python packages | whisper.cpp |
| ASR with no local model | an `openai_compat` cloud service (audio leaves the machine) |

Python 3.11 or newer runs the scripts themselves.

## ffmpeg and ffprobe

Frame extraction was tried with ffmpeg 9.0.2 on Windows, on synthetic media. Download is on the
order of 100 MB or more depending on OS. Both `ffmpeg` and `ffprobe` must be found; the packages
below ship both.

| OS | Command |
| --- | --- |
| Windows | `winget install --id Gyan.FFmpeg -e` |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Fedora and others | use the distribution package, for example `sudo dnf install ffmpeg` (may need RPM Fusion) |

- Linux needs `sudo`: show the command and let the user run it in their own terminal. Do not run
  `sudo` yourself.
- After a winget install the current terminal may not see the new PATH. `digest.py` also looks in
  the WinGet folders, `C:\ffmpeg\bin`, Chocolatey, Scoop, Homebrew and `/usr/local/bin`, so just
  re-run it. `MEDIA_DIGEST_FFMPEG_DIR` points it at any other folder.
- No winget or brew? Download a build from https://ffmpeg.org/download.html and set
  `MEDIA_DIGEST_FFMPEG_DIR` to the folder containing both programs.

## uv

Developed with uv 0.12.x; any recent uv should work. Small download (tens of MB). Needed only for the PEP 723 adapters
(`faster_whisper`).

| OS | Command |
| --- | --- |
| Windows | `winget install --id astral-sh.uv -e` or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| macOS | `brew install uv` |
| Linux and macOS | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

The curl and PowerShell commands download and run a script from astral.sh; say so when asking for
consent. Open a new terminal afterwards if `uv` is not found.

## faster-whisper (local, easiest)

Written against faster-whisper 1.2.1. **Not yet tested with a real engine and model**: only the
probe and unit tests with fakes exist, so expect rough edges on a first real run. Needs `uv` (or the `faster-whisper` Python package already
installed in the Python that runs the scripts).

- No manual install: `digest.py` runs the adapter with `uv run --script`, which fetches
  `faster-whisper` and its dependencies into a cache on first use. That first run downloads
  the Python packages (a few hundred MB, approximate) and then the model.
- Model download, once, from Hugging Face:

  | model (`model =` in config) | download |
  | --- | --- |
  | `tiny` | ~75 MB |
  | `base` | ~145 MB |
  | `small` | ~485 MB |
  | `medium` | ~1.5 GB |
  | `large-v3-turbo` (default) | ~1.6 GB |
  | `distil-large-v3` | ~1.5 GB |
  | `large-v3` | ~3.1 GB |

  Smaller models are faster and less accurate; `small` is a reasonable choice on slow machines.
  `python -m mdcore.asr list` shows `model_cached` and `download_size` before anything is fetched.
- CPU is fine (int8). An NVIDIA GPU is used automatically when CTranslate2 can see it.
- Offline or restricted machines: put a converted CTranslate2 model folder anywhere and set
  `model = "D:/models/my-ct2-model"`.

## whisper.cpp (local)

Written against whisper.cpp v1.9.5 (`whisper-cli -oj`). **Not yet tested with a real binary and
model**, only with fakes. Two parts: the `whisper-cli` program and a ggml model file.

| OS | Program |
| --- | --- |
| macOS | `brew install whisper-cpp` |
| Windows | download the prebuilt Windows zip from https://github.com/ggml-org/whisper.cpp/releases, unzip it, put the folder with `whisper-cli.exe` on PATH |
| Linux | build from source (needs git, cmake and a C++ compiler): see the repository README |

Model: from a whisper.cpp checkout run `sh ./models/download-ggml-model.sh large-v3-turbo` (about
1.6 GB; `base` is ~150 MB, `small` ~480 MB), or download a `ggml-*.bin` file from the
`ggerganov/whisper.cpp` repository on Hugging Face. Then set it in the config:

```toml
[adapters.whisper_cpp]
model_path = "~/models/ggml-large-v3-turbo.bin"
```

Without `model_path` the adapter reports itself unavailable.

## Cloud and self-hosted services

No install, only an account and an API key in an environment variable. Audio leaves the machine
for cloud services, so get consent first. Recipes and presets are in [config.md](config.md).

- OpenAI: `OPENAI_API_KEY`, 25 MB upload limit.
- Groq: `GROQ_API_KEY`, 25 MB on the free tier.
- vLLM or Speaches on your own server: usually `http://localhost:8000/v1`, no key.

## Check the result

```
cd "<SKILL_DIR>/scripts"
python -m mdcore.asr list
```

At least one row with `available: true` means transcripts will work. Then re-run `digest.py` on
the file: runs that lacked a transcript are retried automatically.
