# 規劃：LINE CLI 與 Media Digest 的 Claude Code plugin

版本與文件查證日期：2026-10-12。實作前，第 6 節的版本表要再查一次。

## 1. 目標與範圍

- 讓 Claude Code 能透過 [kongesque/line-cli](https://github.com/kongesque/line-cli)（MIT）操作**個人 LINE 帳號**，並完整對應 line-cli 的所有指令。
- 讓 Claude 讀得懂附件的內容：
  - 圖片和 PDF 直接讀。
  - 影片抽關鍵影格，再把音軌轉成文字。
  - 音訊直接轉成文字。
- 公開 repo，Windows、macOS、Linux 都能用。核心只依賴 `line`，其他依賴都是選配。
- **不做**：
  - 不改上游 Go 程式碼，也不 fork。
  - 不處理 LINE OA。OA 由另一支 skill 負責。

## 2. 架構決策

| 決策 | 選擇 | 理由 |
|---|---|---|
| 散布方式 | 一個 marketplace repo，內含 **兩個獨立 plugin**：`line-cli`、`media-digest` | 可以分開安裝；media-digest 跟 LINE 無關，Discord 或本機檔案也能用 |
| 兩個 plugin 的關係 | `line` 不依賴 `media-digest`；有裝就建議使用，沒裝就只處理圖片和 PDF | 核心功能永遠能裝起來 |
| 依賴 line-cli 的方式 | 使用者自行安裝 `line`；skill 先檢查有沒有裝、版本是多少，沒裝就提供安裝指令 | 不重新散布二進位檔，上游更新也不必跟著重新發版 |
| ASR | 統一的 **adapter 介面**：每個 adapter 都是獨立的可執行程式，遵守同一份 JSON 規格 | 重的依賴只在選用該 adapter 時才下載；使用者可以自己接任何引擎 |
| Python 依賴 | 主程式只用標準函式庫；需要套件的 adapter 用 PEP 723 宣告依賴，透過 `uv run` 執行 | 不需要先手動建 venv；沒裝 uv 也能跑核心流程 |
| 秘密值 | 設定檔只記**環境變數的名稱**（例如 `api_key_env = "GROQ_API_KEY"`），不存 key 本身 | plugin `userConfig` 裡的 sensitive 值傳不到 Bash 執行的腳本；而且 key 不該寫進檔案 |

## 3. Repo 結構

```
line-cli-skills/
├─ .claude-plugin/marketplace.json     # 列出 plugins/line-cli 與 plugins/media-digest
├─ plugins/
│  ├─ line-cli/
│  │  ├─ .claude-plugin/plugin.json
│  │  └─ skills/line-cli/
│  │     ├─ SKILL.md                   # 何時使用、流程、安全規則（< 300 行）
│  │     ├─ references/
│  │     │  ├─ commands.md             # 每個指令的全部旗標，對應 line-cli 版本
│  │     │  ├─ json-schema.md          # messages/chats/contacts/watch/send/download 的輸出結構
│  │     │  ├─ errors.md               # 結束代碼 + stderr 字串 → 處理方式
│  │     │  ├─ safety.md               # 寫入操作的確認規則
│  │     │  ├─ archive.md              # 本機存檔格式、設定、限制
│  │     │  └─ setup.md                # 安裝、登入、headless、更新
│  │     └─ scripts/
│  │        ├─ setup.py                # 偵測、協助安裝、doctor 檢查
│  │        ├─ lineq.py                # 包裝層：名稱轉 ID、錯誤分類、輸出統一的 JSON
│  │        ├─ login.py                # 開新的終端機視窗跑 line login，再等 whoami 成功
│  │        ├─ sync.py                 # 增量同步：拉最新 100 則 → 去重 → 寫 jsonl/md → 下載附件
│  │        └─ linecore/               # 只用標準函式庫的共用模組
│  │           ├─ runner.py            # 呼叫 line、分類錯誤（lineq 和 sync 共用）
│  │           ├─ store.py             # jsonl 讀寫、以訊息 ID 去重、寫入同步狀態
│  │           ├─ render.py            # jsonl → messages.md
│  │           ├─ media.py             # 附件下載、補副檔名、magic bytes 校正
│  │           └─ config.py            # 存檔路徑、下載策略（依作業系統決定）
│  └─ media-digest/
│     ├─ .claude-plugin/plugin.json
│     └─ skills/media-digest/
│        ├─ SKILL.md
│        ├─ references/
│        │  ├─ config.md               # 設定檔格式、語言路由
│        │  ├─ adapters.md             # 內建 adapter 清單＋自訂 adapter 的寫法
│        │  └─ install.md              # ffmpeg、uv、各引擎在三個作業系統的安裝方式
│        └─ scripts/
│           ├─ digest.py               # 入口：probe → 判斷類型 → 抽影格、抽音軌 → 呼叫 ASR → manifest
│           ├─ mdcore/                 # 只用標準函式庫的共用模組
│           │  ├─ probe.py             # ffprobe 包裝、判斷媒體類型
│           │  ├─ frames.py            # 場景偵測、均勻取樣，限制影格數量
│           │  ├─ audio.py             # 正規化成 16kHz 單聲道 wav、依大小切段
│           │  ├─ config.py            # 讀設定、合併預設值、依作業系統決定路徑
│           │  └─ asr.py               # adapter 註冊表、自動偵測、備援鏈、語言路由
│           └─ adapters/               # 每個檔案都是獨立的可執行程式，遵守第 4 節的規格
│              ├─ faster_whisper.py    # PEP 723: faster-whisper
│              ├─ whisper_cpp.py       # 呼叫 PATH 上的 whisper-cli
│              └─ openai_compat.py     # 標準函式庫 urllib；OpenAI/Groq/vLLM/Speaches
├─ tests/                              # pytest；假的 line 執行檔 + 合成媒體
├─ evals/                              # claude plugin eval：觸發與流程案例
├─ .github/workflows/ci.yml            # validate、pytest、ruff，三個作業系統都跑
├─ README.md / README.zh-TW.md
├─ CHANGELOG.md
└─ LICENSE                             # MIT，並註明上游 line-cli 的出處
```

## 4. 模組規格

### 4.1 `line-cli` skill

**流程寫在 SKILL.md：**

1. 跑 `setup.py --check`。如果沒裝 `line`，就由 skill **主動協助安裝**，不讓使用者自己照著指令操作（見 4.1.0）。
2. 跑 `line whoami --json` 確認登入狀態。
   - session 存在作業系統的金鑰儲存區，token 大約 168 小時會**自動更新**，平常不需要重新登入（見上游 TOKEN_SESSION.md）。
   - 需要登入時，先提醒使用者：登入會把現有的 Chrome 擴充版 LINE 登出。使用者同意後執行 `login.py`：
     - 開一個新的終端機視窗執行 `line login`：Windows 用 `Start-Process`，macOS 用 `osascript` 開 Terminal，Linux 依序嘗試 `x-terminal-emulator`、`gnome-terminal`、`konsole`。
     - 使用者在那個視窗掃 QR，或自己輸入密碼。Claude 不經手密碼。
     - 腳本每 3 秒檢查一次 `line whoami --json`，成功就回報；最多等 5 分鐘。
     - 開不了視窗（例如透過 SSH 使用）就改成提示使用者自己執行 `line login`。
3. 讀取類操作可以直接跑：`chats`、`contacts`、`messages`、`watch`、`auth status`、`update --check`。
4. 寫入類操作要**每次都先問使用者**：
   - 包括 `send`、`react`、`unsend`、`login`、`logout`、`update`（不帶 `--check`）、`auth migrate`。
   - 詢問時列出聊天室名稱和 ID、內容摘要、動作，等使用者回答「是」才執行。
   - **永遠不自動重試。** 回應遺失時，先跑 `messages` 確認訊息到底有沒有送出。
5. 傳文字一律用 `--stdin`，避免引號跳脫和換行出問題。

**`lineq.py`（只用標準函式庫）：**

- `resolve NAME`：從 `chats --search` 和 `contacts --search` 找出完整 ID。對到多筆時回傳候選清單，交給 Claude 問使用者。
- `run -- <line args>`：統一的包裝層。
  - 回傳 `{ok, exit_code, data, error:{kind, message, retryable}}`。
  - 把 stderr 和結束代碼分類：1、65、69、74、75、78、130、143。
  - 結束代碼 75（session 被占用）時，只有讀取類操作會退避重試。

### 4.1.0 協助安裝（目標：不懂技術的人也能完成）

**`setup.py [--check | --install | --doctor]`：**

**`--check`** 輸出下列狀態，給 Claude 判斷下一步：

```json
{"line": {"found": true, "path": "...", "version": "v0.4.0"},
 "logged_in": false, "keyring": "ok|missing", "os": "windows"}
```

**`--install`** 的流程：

1. 先用白話告訴使用者要做什麼，**等使用者確認**，因為這一步會從網路下載並執行程式：
   - 要從 GitHub 上的 kongesque/line-cli 下載 LINE CLI。
   - 會裝在你的使用者資料夾，不需要管理員權限。
   - 下載後會比對官方的檔案雜湊值，確認檔案沒被竄改。
2. 依作業系統選安裝方式，一律使用**上游官方**的安裝途徑：

   | 作業系統 | 方式 | 裝到哪裡 |
   |---|---|---|
   | Windows | 上游的 `install-release.ps1` | `%LOCALAPPDATA%\line-cli\bin\line.exe` |
   | macOS（有 Homebrew） | `brew install kongesque/tap/line-cli` | brew 的安裝路徑 |
   | macOS（沒有 Homebrew）、Linux | 上游的 `install-release.sh` | `~/.local/bin/line` |

   - 安裝腳本從**固定的 tag** 下載，不跟著 `main` 分支走，降低供應鏈被動手腳的風險。
   - 上游腳本本身會比對 SHA256SUMS。
3. **剛裝完就能用，不必重開**：`linecore/runner.py` 找 `line` 的順序是：設定檔裡的 `line_path`（優先，方便改指向 fork 版）→ `PATH` → 已知的安裝路徑。所以即使 PATH 還沒在目前這個 session 生效，也能直接用。安裝腳本以 SHA-256 釘死在固定版本，並把 `LINE_CLI_DOWNLOAD_BASE` 指到同一個版本的 release，連執行檔也一起固定（Homebrew 例外，無法固定版本）。
4. Linux 桌面環境缺金鑰圈套件時（需要 `sudo apt install libsecret-tools gnome-keyring`）：
   - Claude 不跑 sudo。
   - 改用 `login.py` 同一套機制開一個終端機視窗，讓使用者自己輸入密碼。
5. 裝好後接著跑登入流程，然後問使用者要先同步哪個聊天室。整個首次設定由 Claude 一路帶完。

**`--doctor`**：一次檢查所有項目，每項回報「狀態 + 白話說明 + 怎麼修」，供使用者回報問題時附上。檢查項目包括：
- 執行檔與版本。
- 登入狀態。
- 金鑰圈。
- 存檔目錄能不能寫入。
- 與 references 對應的 line-cli 版本是否一致。
- 有沒有新版（`update --check`）。

**media-digest 的依賴**也比照辦理，同樣**每次都先確認**，並先講清楚檔案大小：

| 依賴 | 安裝方式 |
|---|---|
| ffmpeg | Windows `winget install --id Gyan.FFmpeg -e`、macOS `brew install ffmpeg`；Linux 需要 sudo，改開終端機讓使用者自己輸入 |
| uv | 官方安裝腳本，或 winget / brew |
| ASR 模型 | 第一次使用時下載，事先告知大小，例如 large-v3-turbo 約 1.6 GB |

### 4.1.1 同步與本機存檔

line-cli 本身不存任何訊息，只會寫入 session、lock 和 watch 的進度點。要像既有的 OA 聊天匯出工具那樣保留紀錄，得由 skill 自己存。

**`sync.py CHAT|--all [--types ...] [--max-bytes N]`：**

1. 用 `messages CHAT --json --limit 100` 拉最新 100 則（上游的上限）。
2. 用訊息 ID 去重後追加到 `messages.jsonl`。
3. 重新產生 `messages.md`：時間、發言人顯示名稱（透過 `contacts --mid` 查詢並快取）、內容；附件用相對路徑連結。
4. **主動下載**新附件。
   - 理由：只有最新 100 則找得到，LINE 那邊也會過期，晚一步就可能永久抓不到。
   - 依 `content_type` 補副檔名：1 是 `.jpg`、2 是 `.mp4`、3 是 `.m4a`、14 用原本的 `file_name`。
   - 再讀檔頭的 magic bytes 校正副檔名。
5. 下載失敗的附件在 md 裡標成 `[影片] 超過 20 MiB` 或 `[圖片] 已過期`，同時記進 `state.json`。
6. 輸出摘要 JSON 給 Claude：`{chat, new_messages, new_files[], failed_files[], gap_possible}`。

- **收回偵測（推測）**：
  - 上次存檔有、這次卻不在拉回的 100 則範圍內的訊息，在 jsonl 裡記上 `possibly_unsent_at`，md 裡標成「（可能已收回）」，並保留原文。
  - 只有收回前已經同步到的訊息偵測得到。
  - 已讀、精確的收回事件需要上游 `watch` 多輸出事件參數，暫時不做。
- **可能漏訊息的偵測**：如果這次拉到的 100 則裡，最舊的一則比上次同步的最新一則還新，中間就可能有漏掉的訊息。這時 `gap_possible: true`，並在 md 插入「⚠ 此處可能有缺漏」。
- **比較花時間的處理交給 Claude 視需要再做**：
  - 影片抽影格和語音轉文字不在同步時跑。
  - Claude 需要看內容時，才對單一檔案呼叫 `media-digest:media-digest`。
  - 結果快取在 `files/<檔名>.digest/` 底下，同一個檔案不重複處理。
  - 沒裝 media-digest 時，影片和音訊只回報檔案路徑，並提示可以安裝。
- **圖片和 PDF** 直接交給 Read 工具。

**存檔結構：**

```
<archive_root>/
└─ <chat_id>__<聊天室名稱>/            # 以 ID 為主，群組改名也不會分家；改名時只更新資料夾名稱的後半段
   ├─ messages.jsonl                   # 唯一的事實來源（line-cli 原始 JSON，逐行一則）
   ├─ messages.md                      # 由 jsonl 產生，給人和 Claude 讀
   ├─ state.json                       # 上次同步的時間、最新訊息 ID、下載失敗清單
   └─ files/                           # <YYYYMMDD-HHMMSS>_<message_id>.<ext>
```

**設定（`linecore/config.py`，優先順序由上而下）：**

1. 環境變數 `LINE_SKILL_ARCHIVE`。
2. 設定檔 `config.toml`，裡面的 `archive_root` 欄位。設定檔位置與 media-digest 相同：`$XDG_CONFIG_HOME/line-skill/`，Windows 是 `%APPDATA%\line-skill\`。
3. 預設值：Windows `%LOCALAPPDATA%\line-skill\archive\`；macOS `~/Library/Application Support/line-skill/archive/`；Linux `$XDG_DATA_HOME/line-skill/archive/`。

```toml
archive_root = "D:/somewhere/line-personal"
[download]
types = ["image", "video", "audio", "file"]   # 要主動下載的類型
max_bytes = 20971520                          # 超過就不下載，只標記
```

**隱私：**
- 存檔永遠不放進 plugin 目錄。
- README 提醒存檔是私人對話，不要 commit 到公開 repo。
- `sync.py` 偵測到存檔目錄位於 git repo 內時，在摘要裡附上提醒。

**已知限制（決定：先照現狀做，實務上撞到太多次再找上游）：**
- 第一次同步只拿得到最新 100 則。
- 單檔上限 20 MiB。
- 不支援從外部網址下載的附件。
- line-cli 只看得到「個人帳號有加入」的聊天室，跟 OA 的聊天室清單不一樣。
- 上游 issue 和 PR 目前都沒有人提過翻頁或檔案大小上限（2026-10-12 查證）。

### 4.2 `media-digest` skill

**`digest.py INPUT... --out DIR [--max-frames 24] [--lang auto] [--no-asr]`：**

| 類型 | 處理方式 | 交給 Claude 的東西 |
|---|---|---|
| 圖片 / PDF | 不處理 | 原檔路徑 |
| 影片 | ffprobe 讀時長 → 場景偵測抽影格（門檻 0.3，數量 `-frames:v` 封頂；太少就改成均勻取樣）→ 抽出音軌 | 影格 jpg（附時間戳）＋ 逐字稿 |
| 音訊 | 正規化成 16kHz 單聲道 wav → ASR | 逐字稿（含時間戳） |
| 其他 | 回報不支援的類型 | — |

- 輸出 `digest.json` 和 `digest.md`，把影格時間和逐字稿段落對齊成時間軸，Claude 只要讀這一份就好。
- 每一步都可以降級：
  - 沒裝 ffmpeg：跳過影片處理，提供安裝指令。
  - 沒有任何可用的 ASR：只輸出影格，並說明怎麼設定 ASR。

### 4.3 ASR adapter 規格

每個 adapter 都是可執行程式，介面如下：

```
<adapter> --audio PATH --lang CODE|auto [--prompt TEXT] [--model NAME]
stdout: {"text": "...", "language": "zh", "segments": [{"start": 0.0, "end": 3.2, "text": "..."}]}
stderr: 診斷訊息；exit 0 成功 / 2 環境不符（缺依賴、缺 key，可以換下一個）/ 1 執行失敗
```

另外支援 `<adapter> --probe`，回傳 `{available: bool, reason, install_hint}`，供自動偵測使用。

**內建 adapter：**

- `faster_whisper`：本機。預設模型 `large-v3-turbo`，`zh` 的預設模型可以換成 Breeze-ASR-25 的 CT2 轉檔（社群轉檔，要在設定裡明確選用）。
- `whisper_cpp`：本機，呼叫 `whisper-cli`。
- `openai_compat`：雲端或自架。可設定 `base_url`、`model`、`api_key_env`，內建 OpenAI、Groq、vLLM、Speaches 的預設值。
  - 超過 25 MB 就先壓縮或切段。
  - 遇到 `gpt-transcribe` 這類改用 `languages` 欄位的模型，就不送 `language`。

**自訂 adapter（`type = "command"`）：**

- 任何遵守這份規格的執行檔都能接。設定裡填指令範本，例如 `["my-asr", "--audio", "{audio}", "--lang", "{lang}"]`。
- 使用者私有的服務走這條路，例如自架的 ASR 服務（`/v2/asr` 任務佇列格式），**不放進公開 repo**。`references/adapters.md` 會附一份範例寫法。

**設定檔 `config.toml`：**

- 路徑：`$XDG_CONFIG_HOME/media-digest/` 或 `~/.config/media-digest/`；Windows 是 `%APPDATA%\media-digest\`。
- 讀取用 Python 3.11 以上內建的 `tomllib`，最低 Python 版本訂為 3.11。

```toml
[asr]
order = ["faster_whisper", "whisper_cpp", "groq"]   # 備援順序

[asr.routes]            # 依語言切換；auto 時先用第一個 adapter 偵測語言
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
```

**隱私：**
- 本機 adapter 優先。
- 雲端 adapter 只有在設定裡明確寫進 `order` 或 `routes` 才會用到。
- 第一次把音訊送上雲端前，SKILL.md 要求 Claude 先告知使用者。

## 4.4 分層原則：什麼放在 skill、什麼放在 CLI

| 層 | 職責 | 例子 |
|---|---|---|
| line-cli（上游） | 無狀態的 LINE 存取：協定、加解密、session | 拉訊息、下載、發送、輸出事件 |
| `linecore`（本 repo） | 用 CLI 輸出的資料**推算出來**的有狀態功能 | 存檔、去重、缺漏偵測、收回推測、名稱快取 |
| SKILL.md | 什麼時候呼叫、怎麼確認、怎麼回答使用者 | 寫入操作要先確認、附件交給誰處理 |

- 判斷準則：**資料 CLI 已經有輸出 → 放在 skill；需要 CLI 丟掉的協定資料 → 屬於上游。**
- `linecore` 不依賴 Claude，可以單獨用 `python sync.py` 跑，之後要拆成獨立工具也容易。
- 上游策略：暫時不發 PR。真的需要時先發 PR；被拒的話就 fork，並定期同步上游。`runner.py` 預留 `line_path` 設定，可以指到 fork 版的執行檔。

## 5. 安全與維護原則

- 遵守上游 AGENTS.md：寫入操作只嘗試一次、不印出憑證或伺服器原始回應、真實帳號測試要先取得明確授權。
- `references/commands.md` 開頭標明「對應 line-cli vX.Y.Z」。skill 啟動時比對 `line version`，版本不同就提醒指令可能已經改變。
- CI 每週排程跑 `line update --check --json`（不需要登入），有新版就自動開 issue 提醒更新文件。
- 下載的附件一律放在使用者指定的目錄或暫存目錄，不放進 plugin 目錄。

## 6. 依賴版本（2026-10-12 查證，實作當天再查一次）

| 依賴 | 版本 | 必要性 | 來源 |
|---|---|---|---|
| line-cli | v0.4.0（main 領先 3 個 commit） | `line-cli` plugin 必要 | github.com/kongesque/line-cli/releases |
| Claude Code plugin 格式 | marketplace.json / plugin.json / SKILL.md（`${CLAUDE_SKILL_DIR}`） | 必要 | code.claude.com/docs/en/plugins/* |
| Python | ≥ 3.11（目前最新 3.15.0） | media-digest、輔助腳本 | python.org |
| uv | 0.13.0 | 選配（PEP 723 adapter 才需要） | docs.astral.sh/uv |
| ffmpeg/ffprobe | 9.0.2 | 選配（影片、音訊處理） | ffmpeg.org；winget Gyan.FFmpeg / brew / apt |
| faster-whisper | 1.2.1（ctranslate2 4.8.2） | 選配 | pypi |
| whisper.cpp | v1.9.5（Windows 預編譯檔在滾動的 bNNNN release） | 選配 | github.com/ggml-org/whisper.cpp |
| OpenAI STT | gpt-transcribe / gpt-4o-transcribe / whisper-1，25 MB | 選配 | developers.openai.com |
| Groq STT | whisper-large-v3-turbo，25 MB（免費方案） | 選配 | console.groq.com/docs |
| Qwen3-ASR | qwen-asr 0.0.6；vLLM 有支援 OpenAI 格式 | 選配，走 openai_compat 或 command | github.com/QwenLM/Qwen3-ASR |

**評估後不納入的選項：**

- LiteLLM：依賴太重，而且不支援本機引擎。
- openai-whisper：需要 PyTorch，太重。
- Parakeet：不支援中文、日文、泰文。

## 7. 測試策略

- **單元測試（pytest，三個作業系統的 CI 都跑）：**
  - 用假的 `line` 執行檔，依參數回傳固定的 JSON 和結束代碼，涵蓋名稱對到多筆、結束代碼 75、解密失敗、附件過期。
  - 媒體測試用 ffmpeg 的 `testsrc` 和 `sine` 產生合成影片與音訊。
  - 用假的 adapter 測備援鏈和語言路由。
- **結構檢查：** `claude plugin validate . --strict`，以及兩個 plugin 目錄各自驗證。
- **Evals（`claude plugin eval`，手動觸發，避免每次 CI 都花錢）：**
  - 觸發案例：「幫我看 X 群組昨天傳的影片在講什麼」要觸發 line 和 media-digest。
  - 安全案例：「回覆 OK」必須先問使用者才送出。
- **真實帳號測試：** 只在開發者自己的帳號、自己指定的聊天室跑；送出測試訊息前要先確認。

## 8. 里程碑

1. **M1 骨架**：marketplace 與兩個 plugin 的 manifest、CI，`validate` 通過。
2. **M2 安裝與讀取**：`setup.py`（check/install/doctor）、`login.py`、SKILL.md、references、`lineq.py`，加測試。在三個作業系統的乾淨 VM 或 CI runner 上實測「從零安裝到登入前」這段流程。
3. **M3 同步與存檔**：`sync.py`、jsonl/md/state、附件主動下載、缺漏偵測加測試；圖片和 PDF 能交給 Claude 讀。
4. **M4 line 寫入**：send、react、unsend 的確認流程，加上 safety eval。
5. **M5 media-digest 核心**：probe、frames、audio、`digest.md` 時間軸，此時還不接 ASR。
6. **M6 ASR**：adapter 規格、`faster_whisper`、`whisper_cpp`、`openai_compat`、`command`、設定檔、路由。
7. **M7 發佈**：中英 README、安裝說明、CHANGELOG，打 v0.1.0 tag。

## 9. 待決事項

1. ~~名稱~~（已決定）：repo 與 marketplace 叫 `line-cli-skills`，plugin 與 skill 叫 `line-cli`，呼叫指令是 `/line-cli`；另一個是 `media-digest`。
2. ~~作者~~（已決定）：GitHub `timliudev`，作者欄位寫 Tim Liu，email 一律用 `27921307+timliudev@users.noreply.github.com`（repo 的本地 git config 已設定好）。
   - README 要加免責聲明：line-cli 是非官方客戶端、有被停權的風險、與 LINE Corporation 無關。
   - **文件多語系，比照上游**：英文、繁體中文（台灣）、日文、泰文。
     - 範圍是 README 加上 `docs/GUIDE.*.md` 使用指南，每份開頭都有語言切換列，免責聲明四種語言都要有。
     - 由測試檢查各語言版本都存在，避免翻譯被漏掉。
     - SKILL.md 與 references 維持英文，Claude 讀的部分不需要多語系。
   - **文件與程式碼同步**：每個會改變行為的 commit，都要在同一個 commit 裡更新四種語言的 README、GUIDE 和 CHANGELOG。以下三道防線確保這件事：
     - 規則寫在 repo 根目錄的 CLAUDE.md 與 AGENTS.md，以及 CONTRIBUTING.md。
     - CI 的 `docs-sync` 檢查：PR 動到 `plugins/**`，卻沒有動到任何文件就失敗；加上 `no-docs` 標籤可以豁免。
     - 測試檢查四種語言的 `##` 標題數量一致，確保章節結構對得上。
3. **上游合作**：要不要開 issue 告知上游作者，請對方在 README 加連結。翻頁、20 MiB 上限、watch 輸出事件參數（已讀與收回）的 PR 都先暫緩；需要時先發 PR，被拒就 fork。
4. **自架 ASR 服務**：如果是公開專案，就照它的 `/v2/asr` 任務格式寫成內建 adapter；不公開就維持用 command adapter 接。
5. **待實測的行為**：
   - `chats --search ""` 在 JSON 模式下會不會回傳聊天室名稱。程式碼看起來會走舊格式，沒有名稱。
   - marketplace 的 `source: "./plugins/..."` 能不能正常安裝。

### 上游 bug 紀錄（2026-10-13，實測於 line-cli v0.4.0 debug build；決議：暫緩，先不開 issue／PR）

1. **`send` 到部分一對一聊天室失敗**（更正 2026-10-13：`send` 實測可正常送達，只有對方 Letter Sealing 金鑰為舊版的少數對象才失敗；以下為該失敗情境）：LINE 回 HTTP 400、envelope code 10051、TalkException code 86「sender or receiver not supported e2ee or version mismatch」。`negotiateE2EEPublicKey` 回的對方金鑰 `e2eeVersion` 為 0，但 CLI 一律用 V2 加密，上游沒有降版重試，真正原因還被包成泛用的「LINE request failed; check your connection and LINE account settings」（前綴 `send did not return success; delivery may have occurred…`）。訊息確實沒送出，官方 LINE 用戶端可以傳給同一個人。
   - skill 的處理：明白告訴使用者「沒有送出」，不重試，建議改用 LINE App 傳；要確認可用 `messages` 看最近訊息。已寫進 `references/errors.md`「Known upstream bugs」、SKILL.md、README／GUIDE 四語與 CHANGELOG（Known issues）。
2. **`getContactsV2` 整批被拒**：LINE 對已刪除帳號、非好友群組成員只回 userStatus 殘件（沒有 contact 物件），上游遇到就整批失敗，造成 `chats --search`、`contacts --mid` 也報同一個泛用錯誤。skill 已繞過（`linecore/chatlist.py`、`linecore/msgtext.py`）。
3. 若日後要回報上游：兩者都是「錯誤原因被吞掉」加「缺少降版或略過殘件的容錯」，可併成一個 issue。
