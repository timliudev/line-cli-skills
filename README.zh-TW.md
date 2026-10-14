# line-cli-skills：LINE 與媒體附件的 Claude Code skills

[English](README.md) | 繁體中文（台灣） | [日本語](README.ja.md) | [ภาษาไทย](README.th.md)

讓 Claude Code 操作**個人 LINE 帳號**，並處理你收到的媒體附件的 skills。狀態：**v0.1.2（早期軟體）**——讀取、本機同步／存檔與發送文字訊息已在 Windows 上實測，媒體摘要僅以合成媒體測試；發送只在部分使用舊版 Letter Sealing 金鑰的對象會失敗。詳見[狀態](#狀態)。

<a id="disclaimer"></a>

## 免責聲明

- **非官方專案。** 與 LINE Corporation／LY Corporation 沒有任何關係，也未獲其認可或贊助。「LINE」是其所有人的商標，本專案僅用來描述軟體所操作的對象。
- 本專案依賴**非官方**的 [kongesque/line-cli](https://github.com/kongesque/line-cli) 用戶端，它在官方 App 之外與 LINE 連線。**使用它可能導致你的 LINE 帳號被停權。** 風險自負，建議使用一個即使損失也能承受的帳號。
- 存檔的聊天紀錄是**私人對話**。請把存檔放在本 repo 之外，**絕對不要 commit 或公開**。

## 本 repo 的內容

兩個彼此獨立的 Claude Code plugin：

- **line-cli** - 透過 [kongesque/line-cli](https://github.com/kongesque/line-cli) 用戶端，讀取與搜尋你的**個人 LINE** 聊天室、群組與聯絡人、下載附件，並且（每次都需明確確認）發送、回應表情或收回訊息。
  - 以白話文引導首次設定：你同意後會替你安裝 `line`（固定版本、以檢查碼驗證、不需系統管理員權限），接著帶你完成登入。
  - 設計上力求安全：Claude 會先顯示聊天室、確切文字與動作，等你說「好」才執行。寫入操作永遠不會自動重試。
  - 聊天室名稱會解析為 ID；若名稱有歧義，會交由你決定，絕不猜測。
  - 本機存檔：`sync.py` 會把每個聊天室的訊息（一份 `.jsonl` 紀錄加上一份易讀的 `.md`）存到你電腦上的資料夾，並立即下載新附件，讓 Claude 能告訴你有哪些新訊息，也讓歷史紀錄從你開始同步的那天起持續保留，而不只是 LINE 最新的 100 則。
  - 誰回覆、誰按了表情：`thread.py` 列出某則已存檔訊息的回覆與表情回應；`emoji.py` 為同步時（每種表情只存一次、各聊天室共用）快取的 LINE 表情圖保存簡短標籤。
  - 群發、即時存檔與 VIP 通知：`broadcast.py` 把同一則訊息傳給多個聊天室（先試跑、你說「好」才送出、遇到第一個失敗就停、絕不重試）；`watch.py` 在背景為你選定的聊天室即時存檔（其他聊天室一律忽略）、漏掉事件後補同步，並對 VIP 聊天室的新訊息顯示 Windows 通知；兩次同步之間若來了超過 100 則，同步現在會回報無法挽回的 `gap`。
- **media-digest** - 把影片與音訊轉成關鍵影格，加上附時間戳的逐字稿，供 Claude 閱讀。適用於任何本機檔案，不限 LINE。
  - 影片：以場景偵測擷取關鍵影格（附時間戳），並產生逐字稿，合併成一份 `digest.md` 時間軸。
  - 音訊與語音訊息：附時間戳的逐字稿。
  - 可替換的語音轉文字引擎：本機的 `faster-whisper` 與 `whisper.cpp`、任何 OpenAI 相容服務（OpenAI、Groq、vLLM、Speaches），或你自己的程式。預設優先使用本機；只有你設定了雲端服務才會使用雲端。
- 圖片與 PDF 不需要額外處理，Claude 可以直接讀取。

`line-cli` 不依賴 `media-digest`。若兩者都已安裝，Claude 會用 `media-digest` 處理 LINE 的影片與語音訊息。

### 需求

- Claude Code，以及 PATH 中可用的 Python 3.11 或更新版本（Windows、macOS 或 Linux）。
- `line-cli` plugin：需要 `line` 程式。你不必手動安裝，Claude 會主動詢問是否協助安裝。
- `media-digest` plugin（皆為選用，需要時 Claude 會提議協助安裝各項）：影片與音訊需要 `ffmpeg`；逐字稿需要 `uv` 加上一個語音轉文字引擎（預設為本機模型，約 1.6 GB 的下載）。

## 安裝

**方式 A（建議）：在 Claude Code 裡面輸入。** 開啟 Claude Code（終端機版，或 Claude 桌面版的 Code 分頁），在它的對話框一行一行輸入，每行按 Enter：

```text
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

接著開一個新的 Claude Code 對話，外掛才會載入。

**方式 B：從終端機輸入。** 已安裝 `claude` 指令的話，開啟 PowerShell、Terminal 或 bash 執行：

```bash
claude plugin marketplace add timliudev/line-cli-skills
claude plugin install line-cli@line-cli-skills
claude plugin install media-digest@line-cli-skills
```

**注意：** 以 `/` 開頭的行**不是**終端機指令。貼到 PowerShell、Terminal 或 bash 會出現「'/plugin' is not recognized」。它們只能在 Claude Code 裡面用；在終端機請用方式 B 的 `claude plugin ...`。

**方式 C：其他 AI 代理（Codex、Gemini CLI、Cursor、Copilot、OpenCode…）。** 兩個技能都採用開放的 [Agent Skills](https://agentskills.io) 格式，支援該格式的代理都能使用。在終端機執行（需要 Node.js）：

```bash
npx skills add timliudev/line-cli-skills
```

- 它會找到兩個技能（`line-cli`、`media-digest`），詢問要裝給哪些代理，並安裝到 `.agents/skills/`（再連結到各代理自己的技能資料夾，Claude Code 是 `.claude/skills/`）。
- 範圍：預設只裝在**目前的專案資料夾**；加上 `-g`（`npx skills add -g timliudev/line-cli-skills`）則裝給**所有專案**（`~/.agents/skills/`）。
- 之後用 `npx skills update` 更新。
- **同一台電腦不要用兩種方式安裝同一個技能。** 若已用方式 A 或 B，又用方式 C，Claude Code 會看到兩個 `line-cli` 技能。請擇一；要換方式，先移除另一種（`claude plugin uninstall line-cli@line-cli-skills`，或 `npx skills remove line-cli`）。

### 快速上手

1. 在 Claude Code 中可以這樣說，例如：「幫我看一下 LINE 群組 Family 有沒有新訊息。」
2. 第一次使用時，Claude 會檢查 `line` 是否存在，用白話說明它將安裝什麼，並請你同意。接著會請你登入：會開啟一個新的終端機視窗，讓你用手機掃描 QR Code。**登入會讓 LINE 的 Chrome 擴充功能登出。**
3. 之後照常提問即可：讀取聊天室、查找聯絡人、「Anna 傳的那支影片在講什麼？」（需要 `media-digest`），或「回覆 Anna 說 OK」。任何發送、回應表情或收回訊息的動作，每次都會先與你確認。

詳細說明、設定與疑難排解，請見[使用指南](docs/GUIDE.zh-TW.md)。

## 狀態

**v0.1.0 為首次發行，屬早期軟體。** 各部分的測試程度如下：

- **已實測**（真實 LINE 帳號，**僅限 Windows**）：引導式安裝、登入、聊天室名稱解析、同步與存檔、附件下載、群組名稱。
- **macOS 與 Linux：** 只有單元測試，尚未在真實的 macOS 或 Linux 機器上試過。
- **發送可正常使用**（已在 Windows、line-cli v0.4.0 實測：文字訊息成功送達一對一聊天室，並為端對端加密）。只有對方的 Letter Sealing 金鑰是舊版時才會失敗（LINE 以 TalkException 86 拒絕，上游不會降版重試）。此時訊息不會送出，Claude 會明確告訴你「沒有送出」，請改用 LINE App 傳送。回覆（`--reply-to`）走同一條發送路徑，但未另外實測。回應表情與收回已實作（每次都需確認），尚未實測。
- **media-digest：** 關鍵影格擷取是用 ffmpeg 產生的合成媒體測試的，尚未用真實錄影測試。語音轉文字轉接器（faster-whisper、whisper.cpp、OpenAI 相容服務、自訂指令）**尚未**對真實的引擎或服務執行過；它們的單元測試只使用模擬（fake）物件。
- **Evals：** 7 個 plugin eval（[docs/EVALS.md](docs/EVALS.md)）各執行過一次（每個案例跑 3 次），7 個全數通過。它們檢查的是 skill 的觸發與寫入前先詢問的行為，並不涉及 LINE 本身。
- **已知限制：** 上游 `chats --search` 失敗時，名稱搜尋只涵蓋活躍聊天室（使用指南的疑難排解表另有兩項相關修正）。
- **已知限制：** 不支援 @提及（上游沒有提及功能）：「@名字」會以純文字送出，不會標示，也不會通知對方。

詳見[使用指南](docs/GUIDE.zh-TW.md)與[變更紀錄](CHANGELOG.md)。

## 致謝

建構於 [kongesque/line-cli](https://github.com/kongesque/line-cli)（MIT 授權）之上。本 repo 不包含上游程式碼；`line` 需由你自行安裝。

## 授權

MIT，詳見 [LICENSE](LICENSE)。
