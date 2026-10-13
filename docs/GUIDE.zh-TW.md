# 使用指南

[English](GUIDE.md) | 繁體中文（台灣） | [日本語](GUIDE.ja.md) | [ภาษาไทย](GUIDE.th.md)

<a id="disclaimer"></a>

> 非官方專案，與 LINE Corporation／LY Corporation 沒有關係，也未獲其認可。本專案使用非官方的 kongesque/line-cli 用戶端，可能導致你的 LINE 帳號被停權。詳見 README。

大多數情況下，你只需要用自己的語言跟 Claude Code 對話即可。本指南說明幕後發生的事、Claude 會問你什麼，以及如何解決問題。

## 安裝

加入 marketplace 並安裝 plugin：

```
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

- `line-cli` 用於 LINE。`media-digest` 為選用，可處理任何影片或音訊檔案；若希望 Claude 理解 LINE 的影片與語音訊息，請安裝它。
- 需要 Python 3.11 或更新版本。若系統找不到 `python`，Claude 會改試 `python3` 或 `py -3`。
- kongesque/line-cli 的 `line` 程式需另外安裝。你不必自己動手：第一次使用 LINE 時，Claude 會說明將安裝什麼，並先詢問你（見下一節）。

## 首次設定與登入

第一次詢問 LINE 相關事項時，Claude 會執行檢查，然後逐步引導你補齊缺少的項目。每一步之前都會先詢問你。

1. **安裝 `line`。** Claude 會用白話告訴你：它會從 kongesque/line-cli 的官方 GitHub 頁面下載 LINE CLI，放入你自己的使用者資料夾（不需系統管理員權限），並檢查檔案是否遭到竄改。在你說「好」之前，什麼都不會發生。
   - Windows：使用官方 PowerShell 安裝程式，安裝至 `%LOCALAPPDATA%\line-cli\bin`。
   - macOS：若已安裝 Homebrew，使用 `brew install kongesque/tap/line-cli`；否則使用官方 shell 安裝程式，安裝至 `~/.local/bin`。
   - Linux：官方 shell 安裝程式，安裝至 `~/.local/bin`。
   - 安裝程式與程式本身都固定為特定且已測試的發行版本（v0.4.0），並以檢查碼驗證（Homebrew 會安裝 tap 目前的發行版本，無法固定版本）。安裝後立即可用，不需要重新開啟終端機。
   - 在 Linux 桌面環境中，可能缺少系統金鑰圈（keyring）套件。Claude 會顯示 `sudo` 指令，由你自己執行；Claude 絕不會使用 `sudo`。
2. **登入。** Claude 會先提醒你：**登入會讓你的 LINE Chrome 擴充功能登出**，因為 LINE 同一時間只允許一個此類工作階段。你同意後，會開啟一個新的終端機視窗。請用手機上的 LINE 掃描 QR Code，或在該視窗中輸入電子郵件與密碼登入。Claude 永遠不會看到你的密碼。它最多等待五分鐘，並以你的帳號名稱確認。若無法開啟任何視窗（例如透過 SSH），Claude 會給你一行指令，由你自己執行。
3. **完成。** 工作階段會儲存在作業系統的安全儲存區中，並大約每週自動更新，因此通常只需要登入一次。只有在 LINE 讓工作階段登出時才需要重新登入。

非官方用戶端對你的帳號有風險（見 README）。建議使用備用帳號。

## 日常使用

直接提問即可，例如「Family 群組今天說了什麼？」或「Anna 有傳新訊息嗎？」

- **找聊天室。** Claude 會查找名稱並使用真正的聊天室 ID。若有多個聊天室符合（例如朋友「Anna」與群組「Anna」），它會列出來並詢問你要哪一個，絕不會猜。
- **讀取。** Claude 會讀取最新的訊息（預設 20 則，每個聊天室最多最新 100 則，這是此用戶端的 LINE 限制），並以你的語言摘要。照片、影片、語音訊息與檔案會以附件形式出現，你可以要求開啟。貼圖無法顯示。讀取不會將訊息標示為已讀。
- **發送、回應表情、收回。** 這些動作會影響其他人，因此每一則都需要確認。Claude 會顯示聊天室名稱、確切文字（或訊息與表情回應），並等待你明確說「好」。若你修改任何內容，它會再次詢問。
  - 發送的文字會完全依照所顯示的內容送出；檔案上限 20 MiB；訊息上限 10,000 個字元。
  - 要回覆特定一則訊息，直接說就好，例如「回覆小美那則『明天再確認』：好」。Claude 會找出那則訊息（從你的本機存檔中尋找，所以較舊的已存檔訊息也可以），有多則符合時會先問你，送出前會把被引用的原訊息和你的回覆一起列出確認。任何訊息都能當回覆對象，包括照片、影片、語音訊息、檔案與貼圖，例如「回覆阿明 01:28 傳的那張照片：這張可以」。已知限制：不支援 @提及，輸入的「@名字」會以純文字送出，不會標示也不會通知對方。
  - Claude 只會嘗試一次，絕不會自行重試。若發送途中連線中斷，它會先檢查聊天室，確認訊息是否已送達，才會提出任何建議。
  - 收回會將你自己的訊息對所有人移除，且無法復原。
- **他人的訊息一律視為資料，而非指令。** 若聊天訊息寫著「執行這個」或「把那個傳給某人」，Claude 會顯示給你看並詢問，不會照做。
- **本機存檔。** 回答聊天室相關問題之前，Claude 會先同步一次：抓取最新 100 則訊息，把新的訊息加進你電腦上的資料夾（`messages.jsonl` 是原始紀錄，`messages.md` 供閱讀），並立刻下載新的照片、影片、語音訊息與檔案，因為 LINE 會讓附件過期。這樣才能快速回答「有新訊息嗎？」，也能從你第一次同步起保留超過 LINE 最新 100 則的歷史紀錄。你也可以直接要求同步，針對單一聊天室或「我所有的聊天室」。 表情回應會顯示表情符號、誰回應與時間；如果同一個聊天室出現兩個存檔資料夾，下次同步會把它們無損合併成一個。
  - 注意事項：同步只會從 LINE 讀取；聊天室第一次同步只拿得到最新 100 則；若兩次同步之間收到超過 100 則訊息，Claude 會告訴你有缺漏；已存檔的訊息若從 LINE 最新 100 則中消失，會保留下來並標示「(possibly unsent)」（可能已收回），這只是推測，不是定論。

## 附件與 media-digest

- **照片與 PDF。** Claude 會直接從該聊天室的存檔資料夾開啟檔案（同步時下載，上限 20 MiB）。
- **影片。** 使用 `media-digest` 時，Claude 會依場景變化將影片切成關鍵影格（預設最多 24 張），並轉寫語音，然後讀取一份 `digest.md`，依時間交錯排列影格與文字。
- **語音訊息與音訊。** Claude 會轉寫並附上時間戳。
- 若沒有 `media-digest`，Claude 會告訴你檔案的位置，並說明安裝該 plugin 後就能理解影片與音訊。
- 部分內容可能缺漏或無法取得：LINE 已過期的附件、超過 20 MiB 的檔案，以及儲存在外部連結後方的媒體，都無法下載。
- 同步時會替你下載新附件（每個上限 20 MiB，預設所有類型），存到該聊天室的 `files` 資料夾。照片與 PDF 直接從那裡讀取；影片與語音訊息則交給 `media-digest`。

`media-digest` 所需項目（會在你同意並告知下載大小後才提供）：

| 需求 | 用途 | 安裝方式 |
| --- | --- | --- |
| ffmpeg | 影片影格、音訊處理 | `winget install --id Gyan.FFmpeg -e`（Windows）、`brew install ffmpeg`（macOS）、`sudo apt install ffmpeg`（Linux，由你自己執行） |
| uv | 預設的本機轉寫工具 | winget、brew 或官方安裝程式 |
| 語音轉文字引擎 | 逐字稿 | 預設為本機 `faster-whisper`（首次使用時自行安裝；模型約 1.6 GB，只下載一次）；其他替代方案見下 |

沒有 ffmpeg 仍可處理圖片與 PDF；沒有語音轉文字引擎，影片仍會有關鍵影格。

Claude 會替你從 plugin 的 `skills/media-digest/scripts` 資料夾執行 `digest.py FILE... --out OUTDIR`。常用選項：`--lang zh`（語音語言，預設自動偵測）、`--max-frames 24`、`--no-asr`（僅影格）、`--force`（重新處理）。輸出為 `OUTDIR/digest.md` 與 `digest.json`。結果會被快取；沒有逐字稿的檔案，在安裝引擎後會自動重試。

## 設定

所有功能不需設定即可運作。有兩個選用的 `config.toml` 檔（各 skill 的說明放在 `references/` 中）。機密值永遠不會存在這些檔案中，只記錄環境變數的**名稱**。

**`line-cli`**（Windows 為 `%APPDATA%\line-skill\config.toml`，其他系統為 `~/.config/line-skill/config.toml`）：

| 設定鍵 | 說明 |
| --- | --- |
| `line_path` | 指定要使用的 `line` 程式（例如分支版本或固定版本的建置） |
| `archive_root` | 存檔的位置（也可用環境變數 `LINE_SKILL_ARCHIVE` 設定，且優先採用）。預設：`%LOCALAPPDATA%\line-skill\archive`（Windows）、`~/Library/Application Support/line-skill/archive`（macOS）、`~/.local/share/line-skill/archive`（Linux） |
| `download.types`, `download.max_bytes` | 自動下載哪些附件類型（`image`、`video`、`audio`、`file`），以及大小上限（單位為位元組，預設 20 MiB，也是此用戶端能抓取的上限） |
| `render.quiet_gap_days` | `messages.md` 中，相鄰兩則訊息相隔超過這個天數時，插入一行「no messages for X days」（預設 3；`0` 關閉） |

**`media-digest`**（Windows 為 `%APPDATA%\media-digest\config.toml`，其他系統為 `~/.config/media-digest/config.toml`；可用 `MEDIA_DIGEST_CONFIG` 覆寫）：

```toml
[asr]
order = ["faster_whisper", "whisper_cpp", "groq"]   # try these in order

[asr.routes]                                          # per speech language
zh = ["faster_whisper", "groq"]

[adapters.groq]                                       # a cloud service, used because it is listed above
preset = "groq"                                       # api_key_env = "GROQ_API_KEY" comes with the preset
```

- 引擎：`faster_whisper`（本機，預設）、`whisper_cpp`（本機，需要 `model_path`）、`openai_compat`（OpenAI、Groq、vLLM、Speaches 預設值，或你自己的 URL），以及 `command`（任何你已有的程式，包括你自己的伺服器）。
- 若沒有設定檔，轉寫鏈只會使用本機。只有列在 `order` 或 `routes` 中的雲端引擎才會被使用。
- 請在啟動 Claude Code 的 shell 中匯出你的 API 金鑰，例如 `export GROQ_API_KEY=...`，且絕不要把它貼到對話中。
- 語言路由只在你指定語言（`--lang zh`）時生效，若為 `auto` 則不生效。
- 完整參考：`media-digest` plugin 中的 `references/config.md`、`references/adapters.md` 與 `references/install.md`。

## 隱私

- 你的聊天紀錄、下載的附件與摘要都是私人對話。它們保存在你的電腦上，絕不放在 plugin 資料夾內。請勿 commit 或公開，並讓它們遠離任何 git repository。
- 存檔是你電腦上一個存放私人對話的資料夾（預設在你的使用者資料夾內，絕不放在 plugin 目錄裡）。若存檔位置位於 git repository 內，Claude 會提醒你：請將它移走，且絕不要 commit 或公開。
- Claude 不會將聊天內容傳送到其他服務。除非你設定了雲端引擎，否則轉寫都在本機進行；若會使用雲端，Claude 會告訴你哪個服務會接收音訊，並在第一次上傳前詢問。
- Claude 絕不會在對話中要求你的 LINE 密碼，也不會讀取已儲存的工作階段。
- 發送、回應表情與收回永遠需要你明確同意；不會在背景發送任何東西。
- 你讀到的訊息可能包含針對 AI 的指示。Claude 會將其視為文字，而非指令。

## 疑難排解

請要求 Claude「執行 LINE doctor」。它會從 plugin 的 `skills/line-cli/scripts` 資料夾執行 `setup.py --doctor`，檢查 Python、`line` 程式及其版本、登入狀態、金鑰圈（Linux）、你的設定檔、存檔資料夾是否可寫入，以及是否有可用更新，並針對每個項目提供白話的修復方式。貼到錯誤回報之前請先檢視輸出；內容可能包含本機路徑與你的帳號名稱。

| 症狀 | 處理方式 |
| --- | --- |
| "line not found" | 讓 Claude 執行引導式安裝，或檢查你設定檔中的 `line_path` |
| "not logged in" | 請 Claude 協助登入；記得登入會讓 Chrome 擴充功能登出 |
| 登入視窗未能開啟 | 自行在終端機執行 `line login`，然後請 Claude 再檢查一次 |
| Linux：金鑰圈缺少或被鎖定 | 安裝 Claude 顯示的套件（`libsecret-tools`、`gnome-keyring`）並解鎖金鑰圈；若是伺服器，請參閱 `references/setup.md` 中的無頭（headless）選項 |
| "busy" | 另一個 `line` 指令正在執行；稍候片刻（讀取會自動重試） |
| 影片有影格但沒有逐字稿 | 沒有可用的語音轉文字引擎：請 Claude 列出並設定一個 |
| "needs ffmpeg" | 安裝 ffmpeg（見上文），然後再執行一次 |
| 雲端服務處理長錄音時逐字稿失敗 | 超過 25 MB 的音訊尚未支援切割；請改用本機引擎 |
| 訊息可能未送出 | Claude 會先檢查聊天室，且未經你同意絕不會重新發送 |
| Claude 提醒有「缺漏」 | 自上次同步後收到超過 100 則訊息，漏掉的部分無法找回。請更頻繁地同步 |
| 某則訊息標示 "(possibly unsent)" | 它存檔後從 LINE 最新 100 則中消失，所以很可能已被收回；已儲存的文字仍會保留 |
| 照片或檔案標示 "expired" 或 "over 20 MiB" | LINE 已不再提供，或超過此用戶端的上限，因此無法下載 |
| 存檔位於 git repository 內的警告 | 將 `archive_root`（或 `LINE_SKILL_ARCHIVE`）設為 repository 以外的資料夾 |
| 搜尋聊天室名稱出現 "LINE request failed" | 上游會一次替所有聊天室（含不活躍的）取名，其中一批失敗就整個失敗。Claude 會改在活躍聊天室中搜尋並告知你；不活躍的聊天室無法用名稱找到，請改用完整 ID |
| 群組訊息顯示被截斷的 ID 而不是名字 | 非好友的群組成員無法以聯絡人查詢。同步時會改從聊天文字讀取名字並快取；若仍缺名字，代表該成員在最新 100 則內沒發言 |
| 傳訊息給某一位聯絡人一直失敗，顯示「LINE request failed」 | 已知限制：發送本身可正常使用，但該聯絡人的 Letter Sealing 金鑰是舊版，LINE 會拒絕（錯誤 10051／86，「e2ee or version mismatch」），上游也不會降版重試。訊息沒有送出，Claude 會直接告知且不會重試。請改用 LINE App 傳送；讀取該聊天室不受影響 |
| `chats --search` 或查詢群組成員名稱時出現「LINE request failed」 | 上游問題：同一批資料裡只要有已刪除帳號或非好友成員，LINE 的聯絡人查詢就整批失敗。已有替代做法（只搜尋活躍聊天室、改從聊天文字取得名稱），見上面兩列 |
| 在 Git Bash 下 Windows 安裝出現 `tar` 錯誤 | Git 的 GNU tar 讀不懂 `C:` 路徑。安裝時現在會把 Windows 內建的 System32 放在 PATH 最前面；若手動安裝，請在 PowerShell 執行 |

[回到 README](../README.zh-TW.md)
