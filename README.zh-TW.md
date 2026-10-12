# line-cli-skills：LINE 與媒體附件的 Claude Code skills

[English](README.md) | 繁體中文（台灣） | [日本語](README.ja.md) | [ภาษาไทย](README.th.md)

讓 Claude Code 操作**個人 LINE 帳號**，並處理你收到的媒體附件的 skills。狀態：**早期開發中（v0.0.x）**——讀取、發送與媒體摘要功能可以使用；本機同步與存檔尚在規劃中。

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

```
/plugin marketplace add timliudev/line-cli-skills
/plugin install line-cli@line-cli-skills
/plugin install media-digest@line-cli-skills
```

### 快速上手

1. 在 Claude Code 中可以這樣說，例如：「幫我看一下 LINE 群組 Family 有沒有新訊息。」
2. 第一次使用時，Claude 會檢查 `line` 是否存在，用白話說明它將安裝什麼，並請你同意。接著會請你登入：會開啟一個新的終端機視窗，讓你用手機掃描 QR Code。**登入會讓 LINE 的 Chrome 擴充功能登出。**
3. 之後照常提問即可：讀取聊天室、查找聯絡人、「Anna 傳的那支影片在講什麼？」（需要 `media-digest`），或「回覆 Anna 說 OK」。任何發送、回應表情或收回訊息的動作，每次都會先與你確認。

詳細說明、設定與疑難排解，請見[使用指南](docs/GUIDE.zh-TW.md)。

## 狀態

早期開發階段。已實作：引導式安裝與登入、讀取與搜尋聊天紀錄、下載附件、經確認的發送／回應表情／收回、媒體摘要與語音轉文字後端。規劃中：聊天紀錄的本機同步與存檔（`sync.py`），以保留 LINE 每個聊天室最新 100 則訊息之前的歷史紀錄。詳見[使用指南](docs/GUIDE.zh-TW.md)與[變更紀錄](CHANGELOG.md)。

## 致謝

建構於 [kongesque/line-cli](https://github.com/kongesque/line-cli)（MIT 授權）之上。本 repo 不包含上游程式碼；`line` 需由你自行安裝。

## 授權

MIT，詳見 [LICENSE](LICENSE)。
