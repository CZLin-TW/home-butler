# 家庭 AI 管家系統

> 💡 **配套網頁端**：[Smart Home Dashboard](https://github.com/CZLin-TW/Dashboard) — Next.js + TypeScript 視覺化操作介面，跟本 repo 的 LINE Bot 互補（自然語言 vs 按鈕表格）。兩個 repo 一起運作，不分開使用。

## 這是什麼？

一個用 LINE Bot 操作的家庭智能管家系統。家人只要會用 LINE 就能使用——傳訊息給管家，它就會幫你管理食品庫存、待辦事項、智能家電、天氣查詢，還會每天主動推播提醒。

本專案 100% 由 AI 協作完成，包含架構設計、所有程式碼、文件撰寫。

核心理念：
- **零學習門檻**：LINE 是台灣人每天在用的工具，不需要另外裝 App 或學新介面
- **全自然語言**：說「牛奶快沒了」「明天台北會下雨嗎」「開空調 24 度」就行，不需要記指令格式
- **主動提醒**：食品快過期會催你、待辦到時間會提醒、天氣要變冷會通知
- **零供應商鎖定**：資料存在 Google Sheets，完全透明可查看，隨時可以搬走
- **全架構透明**：不是黑盒子，每一層怎麼運作都清楚，壞了自己也能修
- **AI 可接手**：開新對話時附上 README 和程式碼，AI 即可完整接手繼續開發

### 功能一覽

| 功能 | 說明 |
|------|------|
| 食品庫存管理 | 新增、查詢、消耗、修改（品名/數量/單位/過期日），到期前自動提醒 |
| 待辦事項管理 | 新增、查詢、完成、修改（名稱/日期/時間/負責人/類型），支援私人/公開、指定負責人、指派通知 |
| 週期性待辦 | 每天 / 每週幾 / 每月 N 號 / 間隔 N 天的重複提醒，模板與實例分離，每 5 分鐘自動把當天該出現的生成成普通待辦（完成單次不影響週期、停止整個週期可再啟用），LINE 與 Dashboard 共用。受環境變數 RECURRING_TODO_ENABLED 控制生成（預設關） |
| 外部行事曆整合 | Notion 行事曆整合，自動同步到待辦 Sheet 並標記屬性（唯讀/讀寫），支援 Sheet 自訂篩選條件 |
| 空調控制 | 開關、溫度、模式、風速（SwitchBot Hub IR），記錄最後狀態供 Dashboard 顯示與下次相對調整使用 |
| 除濕機控制 | 開關、模式、目標濕度。支援 Panasonic（Smart App API）與 LG（ThinQ Connect API），多台並存，依「智能居家」品牌欄分流 |
| 除濕機自動模式 | 依綁定感測器的濕度條件式 ON/OFF：獨立的濕度門檻 + 可選立即或持續 T 時間 + hysteresis 防抖動；自動模式期間排他鎖住手動 / LINE / 排程控制，機器跑持續除濕（Panasonic 連續除濕 / LG 智慧除濕，機體目標壓低 10%）並由外部 sensor 完全掌控。Panasonic、LG 皆支援（品牌無關狀態機 + driver 分流） |
| DIY IR 設備 | 電風扇等紅外線家電的開關與自訂按鈕 |
| 溫濕度查詢 | 即時讀取室內溫度與濕度（含 SwitchBot Meter Pro CO2 三合一感測器的 CO2 ppm 讀值） |
| 天氣預報 | 全台鄉鎮一週天氣（含體感溫度與相對濕度），支援自然語言查詢 |
| 廣播訊息 | `@all` 開頭可對全體家庭成員發送訊息 |
| 晚間綜合推播 | 晚上：明日天氣預報 + 食品過期提醒 + 明日與未完成待辦 |
| 即時提醒 | 每 5 分鐘檢查即將到來的待辦 |
| 排程指令 | 定時操作家電（如「11 點關電風扇」「睡前調 27 度，早上 8 點關」），設備排程完成時自動通知 |
| 冷氣防黴 | 關冷氣時若已以冷氣/除濕模式運轉 ≥30 分，自動先送風約 5 分鐘吹乾蒸發器再關，降低長黴；全部空調自動套用、免設定 |
| 自訂風格 | 每位成員可自訂管家回覆風格（語氣、角色扮演等），也可隨時恢復預設 |
| 指派通知 | 指派待辦給其他家庭成員時，對方即時收到 LINE 通知 |
| PC agent | 家中 PC 跑 agent push 指標（CPU/RAM/GPU/CPU 溫/GPU 溫 + F@H 狀態），Dashboard 顯示當下值 + 24h 折線圖；同一支 agent 也建立 WebSocket 即時通道，供 Hue 等區網設備控制使用。agent 內建 watchdog、auto-update 自動拉新版、自管 self-restart 不靠 Task Scheduler（詳見 `agent/README.md`） |
| 劇院 agent 轉送 | 劇院 PC 的 agent 設了 `THEATER_AGENT_URL` 會宣告 theater capability，把 `theater.summary` / `theater.set_flags` 指令轉送到同機 [theater-agent](https://github.com/CZLin-TW/theater-agent)（純內網 :8080，Render 連不到，靠 WebSocket 中繼）。`theater_api.py` 對 Dashboard 提供 `/api/theater/summary`（功能開關 + 設備狀態 + log 尾端）與 `/api/theater/flags`（開關寫入） |
| 自動夜燈 | 依 SwitchBot Hub 2 亮度（lightLevel 1~20）條件式控制 Hue 區域：啟用時段內亮度 ≤ 門檻且燈關著 → 套用指定場景＋亮度；亮度 > 門檻 → 關燈；時段結束關燈一次後不再理會。主路徑走 SwitchBot Webhook 推播（秒級），5 分鐘輪詢兜底時段邊界與漏接。每個 Hue 區域一條規則，Dashboard 照明卡片設定，持久化在 Sheet「照明自動規則」（詳見「自動夜燈機制」章節） |
| Siri 語音控制 | iOS 捷徑把語音聽寫成文字 POST 到 `/api/assistant`，走跟 LINE bot 完全相同的 Claude pipeline（解析 → action 分派 → 回覆），讓你用「嘿 Siri」開冷氣、查濕度、記待辦等。每人捷徑各自帶 Line User ID 以分辨身分（詳見「Siri 語音控制」章節） |

---
## 需要的資源

### 帳號與服務（全部免費方案即可）

| 服務 | 用途 | 費用 |
|------|------|------|
| [LINE Developers](https://developers.line.biz/) | Messaging API Bot | 免費 |
| [Google Cloud](https://console.cloud.google.com/) | Sheets API + Drive API（Service Account） | 免費 |
| [Google Sheets](https://sheets.google.com/) | 資料庫（食品、待辦、設備、對話、排程） | 免費 |
| [Render.com](https://render.com/) | Python FastAPI Server 部署 | 免費（Free Instance） |
| [Anthropic](https://console.anthropic.com/) | Claude API（自然語言理解） | 按量計費（見下方） |
| [UptimeRobot](https://uptimerobot.com/) | 每 5 分鐘 ping `/`：防止 Render 休眠 **＋保持 polling thread 存活**（load-bearing，排程/推播靠它，非可選監控） | 免費 |
| [SwitchBot](https://www.switch-bot.com/) | 智能居家 API（空調 IR + 溫濕度） | 免費（需硬體） |
| [Panasonic Smart App](https://www.panasonic.com/tw/) | 除濕機控制 API | 免費（需硬體） |
| [中央氣象署](https://opendata.cwa.gov.tw/) | 天氣預報開放資料 | 免費 |
| [Notion](https://www.notion.so/) | 外部行事曆整合 | 免費（需 Internal Integration） |

### 硬體設備（依需求選配）

| 設備 | 用途 | 大約費用 |
|------|------|---------|
| SwitchBot Hub 2 / Hub Mini | IR 遙控器 + 溫濕度感應 | NT$1,500~2,500 |
| Panasonic 聯網除濕機 | 除濕機控制（需支援 Smart App） | 依機型而定 |

> 智能居家設備和 Notion 整合都是選配，不裝也能正常使用食品管理、待辦事項、天氣查詢等功能。

### 每月費用

| 項目 | 費用 |
|------|------|
| Claude API | 約 NT$10~30/月（一般家庭使用量） |
| 其他所有服務 | 免費 |

> 建議在 Anthropic Console 設定 monthly spend limit $5（約 NT$160），防止意外爆量。

---
## 系統架構
- **介面**：Line Bot（Messaging API）、Siri 語音（iOS 捷徑 → `/api/assistant`）
- **大腦**：Claude API（claude-sonnet-4-6）
- **資料庫**：Google Sheets
- **Server**：Render.com（Python + FastAPI）
- **排程**：in-process polling thread（`main.py`，每 5 分一 tick：行事曆同步 / 週期待辦 / 提醒 / 設備排程 / 封存 / 每日推播）。GAS 已退場
- **防冷啟動 / 保活**：UptimeRobot（每 5 分鐘 ping `/`）——load-bearing：實例醒著 polling thread 才跑得動，排程與推播都靠它
- **智能居家**：SwitchBot API v1.1（空調 IR 控制 + Hub 溫濕度 + DIY IR 設備）
- **除濕機**：Panasonic Smart App API（電源 / 模式 / 目標濕度控制）
- **天氣**：中央氣象署開放資料 API（全台鄉鎮一週預報，含體感溫度）
- **外部行事曆**：Notion API（同步到待辦 Sheet，支援每人獨立篩選條件與權限設定）
- **Dashboard API**：REST API（供網頁版 Dashboard 直接操作裝置、待辦、食品、排程等）
- **PC agent**：每台 PC 跑 `agent/agent.py`（psutil + pynvml + LibreHardwareMonitor + lufah），每 60 秒 push 指標到 `/api/computers/heartbeat`；home-butler 端用 in-memory ring buffer（24h × 60s）暫存，Dashboard 透過 `/api/computers/status` 拉。agent 同時用 WebSocket 主動連回 `/api/agent/ws`，讓 Render 可以把需要區網執行的任務（例如 Hue）交給家中 PC。

---

## 完整建置流程

### 一、環境準備

安裝以下工具：
- Python 3.11+：https://www.python.org/downloads/ （安裝時務必勾選 **Add Python to PATH**）
- Git：https://git-scm.com/download/win
- VS Code：https://code.visualstudio.com/

確認安裝成功：
```
python --version
git --version
code --version
```

---

### 二、Line Bot 申請

1. 前往 https://developers.line.biz/ 登入
2. 建立 Provider（名稱隨意，例如「家庭管家」）
3. 點「Messaging API」→「Create a LINE Official Account」
4. 填入帳號名稱（例如「家庭管家」）、email、業種（選個人）
5. 回到 Official Account Manager →「設定」→「Messaging API」→ 選剛才的 Provider →「同意」
6. 回到 Line Developers Console，進入 Channel →「Messaging API」分頁
7. 點「Issue」產生 Channel Access Token，複製保存
8. 記下 Channel Secret（同頁面）
9. 關閉自動回覆：Official Account Manager →「回應設定」→「自動回覆訊息」關閉

---

### 三、本機開發環境

```
mkdir C:\projects\home-butler
cd C:\projects\home-butler
code .
```

在 VS Code 終端機（Ctrl + `）：
```
python -m venv venv
venv\Scripts\activate
pip install fastapi uvicorn line-bot-sdk gspread google-auth anthropic pytz httpx websockets
```

建立 `.gitignore`：
```
credentials.json
venv/
__pycache__/
test_notion.py
```

---

### 四、部署到 GitHub + Render

```
git init
git config --global user.email "你的email"
git config --global user.name "你的名字"
git add .
git commit -m "first commit"
```

前往 https://github.com 建立新 repo（名稱 home-butler，Private），然後：
```
git remote add origin https://github.com/你的帳號/home-butler.git
git branch -M main
git push -u origin main
```

前往 https://render.com 用 GitHub 登入：
1. 點「New」→「Web Service」
2. 選 home-butler repo
3. 填入：
   - Language: Python 3
   - Build Command: `pip install -r requirements.lock`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Instance Type: **Free**
4. 環境變數填入（見下方環境變數說明）
5. 部署完成後把 Webhook URL 填回 Line：
   - `https://home-butler.onrender.com/callback`
   - 開啟 Use Webhook

每次修改程式後執行以下指令，Render 會自動重新部署：
```
git add .
git commit -m "說明改了什麼"
git push
```

---

### 五、Google Sheets 設定

前往 https://sheets.google.com 建立試算表「家庭管家」，建立以下分頁（第一行為標題列）：

**食品庫存**
| 品名 | 數量 | 單位 | 過期日 | 新增日 | 新增者 | 狀態 |
|------|------|------|--------|--------|--------|------|
- 狀態值：有效 / 已消耗

**食品封存**
| 品名 | 數量 | 單位 | 過期日 | 新增日 | 新增者 | 狀態 |
|------|------|------|--------|--------|--------|------|
- 已消耗的食品自動移至此分頁，Claude 不會讀取

**待辦事項**
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 | 來源 | 屬性 | 燈光提醒 | 燈光區域ID | 規則ID |
|------|------|------|--------|------|------|------|------|----------|------------|--------|
- 狀態值：待辦 / 已完成
- 類型值：公開 / 私人
- 來源值：本地 / Notion（程式自動填入，本地新增的待辦填「本地」，外部行事曆同步的填來源名稱）
- 屬性值：讀寫 / 唯讀（本地項目為「讀寫」，外部項目依成員的權限設定填入）
- 燈光提醒：TRUE/FALSE。只有有「時間」且已到期、狀態仍為待辦時，PC agent 會每分鐘觸發 Hue breathe 一次，直到該待辦完成
- 燈光區域ID：Hue grouped_light id。Dashboard 用顯示名稱下拉選擇，Sheet 內保存穩定 ID；LINE Bot 未指定區域時預設使用「客廳」
- 規則ID：程式自動加欄（`ensure_columns`）。由「週期待辦模板」生成的當次待辦會帶上模板的規則ID（list 上以 🔁 標記、完成後同日不重生靠它去重），一般待辦留空，手動建 sheet 時不需填

**待辦封存**
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 |
|------|------|------|--------|------|------|
- 已完成的待辦自動移至此分頁，Claude 不會讀取

**家庭成員**
| 名稱 | Line User ID | 狀態 | 稱謂 | 管家風格 | Notion Database ID | Notion 篩選 | Notion 權限 | Google Calendar ID |
|------|-------------|------|------|---------|-------------------|------------|------------|-------------------|
- 狀態值：啟用 / 停用
- 稱謂例如「父親,老公,爸爸」（逗號分隔）
- 管家風格：選填，自訂管家回覆風格（例如「回覆簡短，多用 emoji，語氣活潑」），空白則使用預設風格
- Line User ID 取得方式：家人加好友後傳訊息，從「對話暫存」分頁複製（U 開頭）
- Notion Database ID / Notion 篩選：選填，有填才整合（詳見下方說明）
- Notion 權限：「唯讀」或「讀寫」，會被寫進待辦 Sheet 的「屬性」欄。「唯讀」項目在 modify_todo / delete_todo 會被拒絕（避免改到別人 Notion 的事件）；「讀寫」則允許本地修改／完成。空白預設「唯讀」
- Google Calendar ID：選填，預留欄位

**對話暫存**
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 角色值：user / assistant
- 每位用戶保留最近 6 則（預設值，可在程式碼中調整 `limit` 參數），超過自動移至對話封存
- 6 則為 user + assistant 合計，不分角色，例如 3 問 3 答
- 推播訊息也會存入，Claude 可參考之前推過的內容

**對話封存**
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 超過保留則數的舊對話自動移至此分頁，Claude 不會讀取

**智能居家**
| 名稱 | 類型 | 位置 | Device ID | 狀態 | 按鈕 | Auth | 控制類型 | 最後電源 | 最後溫度 | 最後模式 | 最後風速 | 最後更新時間 | 溫度補償 | 濕度補償 | 自動關機小時數 |
|------|------|------|-----------|------|------|------|---------|---------|---------|---------|---------|------------|---------|---------|------------|
- 類型值：空調 / 感應器 / IR / 除濕機
- 狀態值：啟用 / 停用
- 按鈕欄：僅 IR 設備需要填寫，逗號分隔（例如「電源,風速+,風速-,擺頭」）
- Auth 欄：僅 Panasonic 除濕機需要填寫
- 控制類型值：`command`（標準指令，例如 turnOn/turnOff、setAll）／`customize`（DIY IR 自訂按鈕）。對應 SwitchBot API 的 commandType；空白時 IR 開/關按鈕走 command、其他按鈕走 customize
- 最後電源／最後溫度／最後模式／最後風速／最後更新時間：**僅空調設備使用**，由程式自動寫入，不需手動填。Dashboard 用來顯示最後狀態，LINE bot 用來支援「調低 1 度」這類相對指令。手動建 sheet 時這 5 欄保持空白即可
- 溫度補償／濕度補償：**僅感應器設備使用**。填入數字（正或負），程式讀取 sensor 數值後自動加上此補償值。例如 Hub 2 貼牆導致濕度偏高 5%，填 `-5`。空白 = 不補償。濕度補償後會自動限制在 0~100% 範圍
- 自動關機小時數：**僅空調設備使用**。填入整數，系統會在空調開啟後 N 小時自動加一筆 off 排程。例如填 `8` → 開空調後最晚 8 小時關。空白或 0 = 停用此功能。使用者每次對該空調發送「從關→開」的命令會重置計時；純調整溫度/模式/風速不會重置。如果使用者自己設了 off 排程，系統會清掉自動排程讓使用者的決定優先
- SwitchBot Device ID 取得方式：瀏覽器打開 `https://home-butler.onrender.com/switchbot/devices`

**排程指令**
| 設備名稱 | 動作 | 參數 | 觸發時間 | 建立者 | 建立時間 | 狀態 | 來源 |
|---------|------|------|---------|--------|---------|------|------|
- 動作值：control_ac / control_ir / control_dehumidifier
- 參數：JSON 字串（例如 `{"temperature":27,"power":"on"}`）
- 狀態值：待執行 / 已執行 / 已過期 / 已取消
- 來源值：使用者 / 自動。使用者手動建立的為「使用者」；空調自動關機機制產生的為「自動」。空白視為「使用者」（舊資料相容）

**排程封存**
| 設備名稱 | 動作 | 參數 | 觸發時間 | 建立者 | 建立時間 | 狀態 | 來源 |
|---------|------|------|---------|--------|---------|------|------|
- 設備所有排程完成後統一移至此分頁，Claude 不會讀取

#### 自動建立的分頁（不需手動建）

home-butler 啟動 / 第一次寫入時自動建出來，header 也自動補：

| 分頁 | 寫入者 | 內容 |
|------|--------|------|
| 感測器歷史 | `sensor_state.py` 每 5 min polling 寫入 | timestamp, device_name, location, temp, humidity, co2（24h 後自動 trim） |
| 空調歷史 | `ac_history.py` 每 5 min polling 寫入 | timestamp, device_name, location, power, temp, mode, fan_speed（24h 後自動 trim） |
| 除濕機歷史 | `dehumidifier_history.py` 自動模式 polling 時寫入 | timestamp, device_name, location, power（24h 後自動 trim，給 Dashboard 自動模式 chart 背景畫運轉區段） |
| 除濕機自動規則 | `dehumidifier_auto.py` 規則設定 / 評估時寫入 | device_name, auto_mode, sensor_name, duration_min, threshold, on_mode, auto_phase, countdown_min, last_event, last_event_at（每台一行，覆蓋更新） |
| 週期待辦模板 | `handlers/recurring_todo.py` 設定週期規則時寫入 | 規則ID, 事項, 重複類型, 星期, 月日, 間隔天數, 時間, 負責人, 類型, 燈光提醒, 燈光區域ID, 起始日期, 結束日期, 狀態, 最後生成日期, 建立者, 建立時間 |
| 裝置配對 | `device_auth.py` Dashboard 登入配對時寫入 | user_code, device_token, status, line_user_id, name, picture, created, expires（5 分鐘過期、單次使用） |

---

### 六、Google Cloud 設定

前往 https://console.cloud.google.com：
1. 建立新專案（名稱 home-butler）
2. 搜尋並啟用「Google Sheets API」
3. 搜尋並啟用「Google Drive API」
4. 搜尋「Service Accounts」→「建立服務帳戶」，名稱填 home-butler
5. 進入剛建立的帳戶 →「金鑰」→「新增金鑰」→ JSON → 下載
6. 下載的 JSON 改名 credentials.json，放到專案資料夾（已在 .gitignore，不會上傳 GitHub）
7. 複製 Service Account 的 email（格式：xxx@xxx.iam.gserviceaccount.com）
8. 去 Google Sheets 右上角「共用」，貼上這個 email，權限選「編輯者」
9. 把 credentials.json 的完整內容（從 `{` 到 `}`）貼到 Render 的 GOOGLE_CREDENTIALS 環境變數

---

### 七、Claude API 設定

前往 https://console.anthropic.com：
1. 選 Individual 方案
2. 儲值 $5
3. 設定 monthly spend limit $5（Billing → Spend limits，防止爆量）
4. 建立 API Key，複製保存
5. 貼到 Render 的 ANTHROPIC_API_KEY 環境變數

---

### 八、SwitchBot 智能居家設定

1. 打開 SwitchBot App → 個人 → 偏好設定 → 關於 → 連點 App 版本 10 次 → 開發者選項
2. 複製 Token 和 Secret Key
3. 在 Render.com 新增環境變數 `SWITCHBOT_TOKEN` 和 `SWITCHBOT_SECRET`
4. 部署後瀏覽器打開 `https://home-butler.onrender.com/switchbot/devices` 取得 Device ID
5. 在 Google Sheets「智能居家」分頁填入設備資料

空調（IR 虛擬設備）的 Device ID 通常是 `02-` 開頭，Hub 的 Device ID 是 MAC 地址格式。

DIY IR 設備（電風扇、喇叭等）的開關使用標準 turnOn/turnOff 指令，其他自訂按鈕使用 customize 模式，程式會自動判斷。

---

### 九、Hub 2 物理按鈕整合（建議空調採用）

**動機**：紅外線冷氣是 write-only，home-butler 只能在自己這邊把「最後一次送出的指令」當快取（見「智能居家」分頁的「最後電源／最後溫度／…」欄位）。只要有路徑繞過 home-butler 直接觸發 IR——例如有人按 Hub 2 機身的 On/Off 觸控鈕——快取就會跟真實狀態 desync，下次「調低 1 度」之類的相對指令會算錯。

**解法**：把 Hub 2 兩顆物理按鈕**透過 Matter 接進 Apple HomeKit**，由 Apple Home 的按鈕設定觸發 iOS 捷徑，捷徑用 HTTP POST 回打 home-butler 的 `/api/devices/control/ac`。所有 AC 控制路徑（LINE Bot、Dashboard、Hub 2 按鈕、Siri）強制收斂到 home-butler 這個唯一入口，IR 只有 home-butler 在發，樂觀快取永遠跟實際狀態一致。

> 為什麼一定要繞 Apple Home 不直接在 SwitchBot App 綁？SwitchBot App 的場景只能綁 SwitchBot 自家動作，**沒辦法呼叫 URL**。Apple Home 的按鈕動作可以執行 iOS 捷徑，捷徑能做「取得 URL 內容（POST）」，這是接到 home-butler 的唯一路徑。

**設定步驟**：

1. **Hub 2 啟用 Matter 並加進 Apple Home**：照 SwitchBot 官方說明操作（過程含取得 setup code、配對等具體步驟）。完成後 Hub 2 機身的「On 按鈕」「Off 按鈕」會以 HomeKit 配件形式出現在「家庭」App
2. **設定按鈕動作**：家庭 App → 點該按鈕配件 → 進入設定 →「單次按下」→「執行」→ 選「捷徑」→ 從這裡新增（或選擇）要執行的捷徑
3. **撰寫捷徑內容**：捷徑加入動作「取得 URL 內容」
   - URL：`https://<你的 home-butler 網址>/api/devices/control/ac`
   - 方法：POST
   - 標頭：`X-API-Key: {HOME_BUTLER_API_KEY}`、`Content-Type: application/json`
   - 主體（JSON）範例：
     - 開冷氣到指定狀態：`{"device_name": "客廳空調", "power": "on", "temperature": 26, "fan_speed": "auto"}`
     - 關冷氣：`{"device_name": "客廳空調", "power": "off"}`
   - 型別注意：`temperature` 是**數字**；`device_name`、`power`、`mode`、`fan_speed` 全部是**字串**
4. **驗證**：按 Hub 2 對應按鈕 → 冷氣應該按指定狀態啟動、Dashboard 上「最後電源／最後溫度／最後更新時間」也會跟著更新（代表這次 IR 是 home-butler 發的）

按鈕能帶任何 home-butler 支援的 AC 參數組合，不限於單純 on/off——例如把 On 按鈕設成「開 26 度自動模式」、Off 按鈕設成「關機」。要做更細緻的調整（中途改溫度／模式／風速）還是用 LINE Bot 或 Dashboard。

---

### 十、除濕機設定（Panasonic / LG）

除濕機支援多台並存，依「智能居家」分頁的 **品牌** 欄位分流到對應 API。品牌欄空值預設為 Panasonic（向下相容）。

**Panasonic（Smart App API）**

1. 在 Render.com 新增環境變數 `PANASONIC_ACCOUNT` 和 `PANASONIC_PASSWORD`（Panasonic Smart App 帳密）
2. 抓裝置參數：帶 `X-API-Key` 打 `GET /panasonic/devices`，列出帳號下所有機器的 GWID / Auth
3. 在「智能居家」分頁新增一行：
   - 名稱：自訂（多台請取不同名字，建議用位置區分，例如「客廳除濕機」）
   - 類型：除濕機
   - 品牌：Panasonic（或留空）
   - Device ID：Panasonic 的 GWID
   - Auth：Panasonic 的 Device Auth
   - 狀態：啟用

**LG（ThinQ Connect API）**

1. 那台 LG 除濕機需先在手機 **LG ThinQ App** 加入、能遠端控制
2. 前往 https://thinq.dev，用同一個 LG 帳號登入，產生 **PAT（Personal Access Token）**，勾選裝置讀取 + 控制權限
3. 在 Render.com 新增環境變數 `LG_PAT`（貼上 PAT）、`LG_COUNTRY`（台灣填 `TW`）
4. 抓裝置參數：帶 `X-API-Key` 打 `GET /lg/devices`，找到該除濕機的 **deviceId**
5. 在「智能居家」分頁新增一行：
   - 名稱：自訂（取不同名字）
   - 類型：除濕機
   - 品牌：LG
   - Device ID：LG 的 deviceId
   - Auth：留空（LG 不需要）
   - 狀態：啟用
6. **校準**：LG 除濕機的 property 欄位名 / 值因機型而異。部署後打 `GET /lg/devices/{deviceId}/profile` 與 `GET /lg/devices/{deviceId}/state`，對照回應調整 `lg_api.py` 頂部「校準點」常數（電源 / 模式 / 目標濕度的 node / key / 值）。

> **LG 自動模式策略**：LG 使用 **智慧除濕**（`lg_api.py:AUTO_MODE_JOBMODE`），並把機體目標濕度設成「外部 sensor 目標 − `AUTO_TARGET_OFFSET`(10%)」。巨觀的開關仍由外部 sensor、hysteresis 與等待時間掌控。
> 　不用「快速除濕」是因為 LG 的快速除濕跑一陣子會自己跳回智慧除濕，害自動模式的手動介入偵測（`state_diverged`）誤判 mode 被改而整個關閉；智慧除濕是它會跳回去的穩定模式。機體目標壓低 10% 是補償機體周邊比房間乾的落差，讓機器多跑、不提早停。

---

### 十一、天氣預報設定

1. 前往 https://opendata.cwa.gov.tw 註冊帳號
2. 登入後到「會員中心」→「取得授權碼」
3. 在 Render.com 新增環境變數 `CWA_API_KEY`（貼上授權碼）

天氣功能免費，支援全台 22 縣市所有鄉鎮，預報範圍一週，包含體感溫度。

---

### 十二、Notion 行事曆整合（選配）

Notion 整合會將事件同步到待辦事項 Sheet，並依權限設定標記為唯讀或讀寫。同步會在以下時機自動執行：使用者查詢待辦（query_todo）、每日推播（/notify）、即時提醒 tick（/notify_realtime，每 5 分鐘）。

**建立 Integration：**
1. 前往 https://www.notion.so/my-integrations 建立 Internal Integration（需 Workspace Owner 或 Admin 權限）
2. 複製 Integration Token
3. 在 Render.com 新增環境變數 `NOTION_TOKEN`

**連結 Database：**
1. 打開 Notion 行事曆 Database 頁面
2. 右上角「...」→「Connections」→ 加入剛建立的 Integration
3. 從 Database 頁面網址取得 Database ID（`https://www.notion.so/xxxxx?v=yyyyy` 中的 `xxxxx`）

**設定 Google Sheets：**
在「家庭成員」分頁對應的成員行填入：
- `Notion Database ID`：Database ID
- `Notion 篩選`：篩選條件，格式為 `欄位名:值,欄位名:值`
- `Notion 權限`：「唯讀」或「讀寫」，控制同步進待辦後是否允許本地 modify / delete。一般情況填「唯讀」，避免不小心改到 Notion 來源事件；只有確定要把同步進來的事件當本地待辦操作才填「讀寫」（注意：本地修改不會回寫 Notion，下次同步該事件仍以 Notion 為準）

篩選條件範例：
- `Status:Incoming,person:CZ` — 只顯示狀態為 Incoming 且 person 為 CZ 的事件
- `Status:Incoming,person:CZ,名稱分析:!休假事件` — 再排除名稱分析為「休假事件」的項目
- `!` 開頭表示排除條件

每個家庭成員可以各自設定不同的 Database ID 和篩選條件，沒有填的成員不會整合 Notion。

---

### 十三、防冷啟動

前往 https://uptimerobot.com 註冊免費帳號：
1. 新增 HTTP monitor
2. URL 填 `https://home-butler.onrender.com`
3. 間隔設 5 分鐘
4. Monitor Type 保持 HTTP(s)（免費方案預設 HEAD，Server 已支援）

---

### 十四、排程與推播（in-process scheduler）

時間驅動的工作全部跑在 `main.py` 的 polling thread（每 5 分一 tick），**不需要任何外部 cron**：

- **realtime tick**（`notify.run_realtime_tick`）：同步外部行事曆 + 週期待辦生成 + 待辦提醒 + 設備排程執行 + 封存。每 5 分一次（比舊版 GAS 的 15 分更即時）。
- **每日綜合推播**（`notify.run_daily_push_if_due`）：每天過了 `DAILY_PUSH_HOUR`（環境變數，預設 `21` = 晚上 9 點）後的第一個 tick 觸發一次；用 Sheet「系統狀態」分頁的 `最後每日推播日期` marker 去重，跨 Render 重啟存活——不重發也不漏發。

能這樣做的前提是 **UptimeRobot 每 5 分 ping 保持實例醒著**（見「十三、防冷啟動」）：polling thread 隨行程睡著就停，所以 UptimeRobot 是 load-bearing，**別當成可選監控隨手關掉**。

`/notify`、`/notify_realtime` 端點仍保留，但只當**手動觸發**（debug / 補發；手動 `/notify` 不檢查也不更新每日 marker）：

```bash
curl -X POST https://home-butler.onrender.com/notify_realtime -H "X-API-Key: <key>"
curl -X POST https://home-butler.onrender.com/notify -H "X-API-Key: <key>"
```

> **從舊版 GAS 遷移**：早期版本用 Google Apps Script 每 15 分鐘打 `/notify_realtime`、每日晚間打 `/notify`。改為 in-process 後，請到 https://script.google.com 對應專案，把那兩條觸發條件（時鐘圖示 → 觸發條件）**刪除或停用**，避免與 thread 重複執行。建議順序：先部署新版、確認 Render log 出現 `[notify-tick]` 且排程/推播正常，**再**關掉 GAS——重疊期很短、且工作本身冪等（排程狀態翻「已執行」後第二跑者會 skip、提醒有去重），無害。

---

### 十五、取得家庭成員 Line User ID

每位家庭成員：
1. 掃 QR Code 加管家好友
2. 傳任何一則訊息
3. 從「對話暫存」分頁複製 User ID（U 開頭）
4. 填入「家庭成員」分頁

---

## Render 環境變數

| 變數名稱 | 說明 | 必要 |
|----------|------|------|
| LINE_CHANNEL_ACCESS_TOKEN | Line Bot 的 Channel Access Token | 必要 |
| LINE_CHANNEL_SECRET | Line Bot 的 Channel Secret | 必要 |
| SPREADSHEET_ID | Google Sheets 的試算表 ID（網址中間那串） | 必要 |
| GOOGLE_CREDENTIALS | Google Service Account 的 JSON 金鑰（整個內容，從 { 到 }） | 必要 |
| ANTHROPIC_API_KEY | Claude API Key（sk-ant- 開頭） | 必要 |
| HOME_BUTLER_API_KEY | 自訂的 API 認證金鑰，保護 `/api/*` `/notify*` `/switchbot/*` 端點。建議用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 產生 | 必要 |
| DASHBOARD_URL | Dashboard 部署網址（例如 `https://dashboard.example.com`）。home-butler 啟動後會 runtime 從 `{DASHBOARD_URL}/api/version` 撈使用者體感版本（1 小時 cache）注入到 LINE bot 的 SYSTEM_PROMPT。沒設或撈不到時 LINE 回答版本會是「未知」，其他功能不受影響 | 建議 |
| SIRI_USER_ID | Siri 捷徑（`/api/assistant`）沒帶 `user_id` 時的匿名 fallback 身分。**刻意維持中性，不要設成任何家人的真實 Line ID**——否則「忘了填 user_id」的請求會靜默冒名成那個人並污染其對話記憶。沒設預設字串 `siri`（匿名訪客：能控制家電，但無名字/無風格/對話記憶獨立）。正確用法是每人捷徑各自帶自己的 Line User ID | 選配 |
| LG_PAT | LG ThinQ Connect 的 Personal Access Token（thinq.dev 產生，需勾裝置讀取 + 控制權限）。有 LG 除濕機才需要 | 選配 |
| LG_COUNTRY | LG ThinQ 國碼，台灣 = `TW`（決定區域 endpoint）。預設 `TW` | 選配 |
| LG_CLIENT_ID | LG ThinQ client 識別字串，固定一組即可。預設 `home-butler-client` | 選配 |
| SWITCHBOT_TOKEN | SwitchBot 開發者 Token | 選配 |
| SWITCHBOT_SECRET | SwitchBot 開發者 Secret Key | 選配 |
| PUBLIC_BASE_URL | 本服務的公開網址，startup 用來向 SwitchBot 註冊 webhook（自動夜燈的秒級路徑）。**Render 上不用設**（自帶 `RENDER_EXTERNAL_URL`），部署在其他平台才需要。兩者都沒有時跳過註冊，自動夜燈退化為 5 分鐘輪詢反應 | 選配 |
| PANASONIC_ACCOUNT | Panasonic Smart App 帳號 | 選配 |
| PANASONIC_PASSWORD | Panasonic Smart App 密碼 | 選配 |
| CWA_API_KEY | 中央氣象署開放資料授權碼 | 選配 |
| NOTION_TOKEN | Notion Internal Integration Token | 選配 |
| RECURRING_TODO_ENABLED | 週期性待辦「生成」總開關（kill-switch），預設**關閉**。設為 `1`/`true`/`yes`/`on` 才會啟用「週期待辦模板 → 每 5 分鐘 materialize 成當日待辦」的生成邏輯。關閉時模板 CRUD（新增/修改/停用規則）仍可用，只是不會自動長出當日待辦；上線或收手只需改這個變數，不必 revert code | 選配 |
| DAILY_PUSH_HOUR | 每日晚間綜合推播的觸發鐘點（24h 制整點），預設 `21`（晚上 9 點）。polling thread 每 tick 一旦過了這個鐘點、且當天還沒推過（Sheet marker 判斷）就觸發一次。沿用原本 GAS 晚間時段；要改推播時間改這個變數即可 | 選配 |

---

## Server 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| / | GET / HEAD | 健康檢查（UptimeRobot 用） |
| /callback | POST | Line Webhook 接收訊息 |
| /notify | POST | 手動觸發晚間綜合推播（外部行事曆同步 + 明日天氣 + 食品過期 + 明日與未完成待辦摘要）。日常由 polling thread 每日晚間（預設 21 點）自動驅動，不再靠 GAS |
| /notify_realtime | POST | 手動觸發 realtime tick（外部行事曆同步 + 即將到時的待辦提醒 + 執行已到時間的設備排程 + 封存）。日常由 polling thread 每 5 分鐘自動驅動，不再靠 GAS |
| /switchbot/devices | GET | 查看 SwitchBot 帳號下所有設備與 Device ID |
| /switchbot/test/{device_id}/{button} | GET | 測試 IR 按鈕（customize 模式） |
| /switchbot/test_turnon/{device_id} | GET | 測試 turnOn 指令 |
| /switchbot/webhook | POST | SwitchBot Cloud webhook 接收端，Hub 2 changeReport 觸發自動夜燈秒級評估。無 API key 保護（SwitchBot 不支援自訂 header）；payload 只拿來比對已設定規則的感應器，不匹配即忽略 |
| /switchbot/webhook/status | GET | Debug：查 SwitchBot Cloud 目前註冊的 webhook URL，確認自動夜燈推播路徑活著 |
| /panasonic/devices | GET | 列出 Panasonic 帳號下所有設備（GWID / Auth），新增除濕機抓參數用 |
| /panasonic/dehumidifier/{name}/full_status | GET | Debug：掃某 Panasonic 除濕機 CommandType 0x00~0x1F 全欄位 |
| /lg/devices | GET | 列出 LG ThinQ 帳號下所有裝置，抓 deviceId 用 |
| /lg/devices/{device_id}/profile | GET | LG 裝置能力 profile，校準除濕機 property 欄位用 |
| /lg/devices/{device_id}/state | GET | LG 裝置目前狀態，對照 profile 校準解析 |

### Dashboard REST API（/api）

供網頁版 Dashboard 使用，所有業務邏輯重用現有 handlers，不重複實作。

> **🔒 認證**：所有 `/api/*` 端點都要求 `X-API-Key` header，值為環境變數 `HOME_BUTLER_API_KEY`。
> 沒有 header 或 key 不對會回 401；伺服器端未設定 `HOME_BUTLER_API_KEY` 則回 503（fail-closed）。
> 同樣的保護也套用在 `/notify*` 和 `/switchbot/*` 端點。`/`（健康檢查）、`/callback`（LINE webhook，由 X-Line-Signature 驗證）和 `/switchbot/webhook`（SwitchBot Cloud 推播，SwitchBot 不支援自訂 header，靠感應器比對過濾）不在保護範圍內。

| 端點 | 方法 | 說明 |
|------|------|------|
| /api/devices | GET | 列出所有啟用裝置基本資料（Sheet 欄位 + AC 上次指令快照），不含即時讀值 |
| /api/devices/status | GET | 統一裝置狀態快取，包含空調 last-command、感應器與除濕機狀態，回傳 `{裝置名稱: 狀態}`。無 name 時立即回快取並在背景更新雲端裝置；`?name=` 用於單台命令確認 |
| /api/devices/options | GET | 各類裝置的可用選項（空調模式/風速、除濕機模式/濕度），供前端動態渲染 |
| /api/devices/control/ac | POST | 控制空調（power, temperature, mode, fan_speed） |
| /api/devices/control/ir | POST | 控制 IR 裝置（device_name, button） |
| /api/devices/control/dehumidifier | POST | 控制除濕機（power, mode, humidity）。自動模式啟用時拒收外部控制 |
| /api/devices/sensor | GET | 查詢感測器（device_name） |
| /api/sensors/status | GET | 所有感測器當下讀值 + 24h history（溫度 / 濕度 / CO2），給 Dashboard chart 用 |
| /api/ac/status | GET | 所有空調當下狀態 + 24h history snapshot，給 Dashboard chart 背景畫 AC on 區段用 |
| /api/dehumidifier/auto-rule | GET | 列出所有除濕機的自動規則 + runtime state，並回傳後端計算的 `humidity_on_threshold` / `humidity_off_threshold`，供 Dashboard 共用同一組 hysteresis |
| /api/dehumidifier/auto-rule | POST | 設定 / 更新除濕機自動規則（device_name, auto_mode, sensor_name, duration_min, threshold, on_mode）。toggle ON 時會立即評估 sensor 當下值決定要不要 fire ON/OFF |
| /api/todos | GET | 列出所有待辦事項 |
| /api/todos | POST | 新增待辦事項 |
| /api/todos | PATCH | 修改待辦事項 |
| /api/todos | DELETE | 完成（刪除）待辦事項 |
| /api/todos/light-reminders | GET | 回傳已到期、未完成、且燈光提醒=TRUE 的待辦（含 light_area_id/name），給 PC agent 每分鐘依區域觸發 Hue breathe |
| /api/food | GET | 列出所有有效食品庫存 |
| /api/food | POST | 新增食品 |
| /api/food | PATCH | 修改食品 |
| /api/food | DELETE | 消耗（刪除）食品 |
| /api/schedules | GET | 列出所有待執行排程 |
| /api/schedules | POST | 新增排程 |
| /api/schedules | DELETE | 取消排程 |
| /api/weather | GET | 查詢天氣（date, location） |
| /api/members | GET | 列出所有啟用的家庭成員 |
| /api/recurring-todos | GET | 列出啟用中的週期待辦模板（每筆附後端算好的人類可讀「摘要」字串給前端直接顯示） |
| /api/recurring-todos | POST | 新增週期待辦模板（item, recur_type 每天/每週/每月/間隔天，選填 weekdays/month_day/interval_days/time/person/type/light_notify/light_area/start_date/end_date） |
| /api/recurring-todos | PATCH | 修改週期待辦模板（Dashboard 走 rule_id 精準定位，或用 item + recur_type 消歧） |
| /api/recurring-todos | DELETE | 停整個週期（模板狀態 → 停用，不刪除；可帶 rule_id 或 item + recur_type） |
| /api/auth/device/create | POST | Dashboard 裝置配對登入：發一組 6 位 user_code + device_token（device_token 由 PWA 保管）給前端顯示與輪詢用 |
| /api/auth/device/status | GET | Dashboard 裝置配對登入：PWA 帶自己保管的 `token`（device_token）輪詢配對狀態（pending/approved/expired/consumed/not_found）；approved 時回核准者身分 `{lineUserId, name, picture}` 並把狀態標成 consumed（單次使用） |
| /api/computers/heartbeat | POST | PC agent 每 60 秒 push 指標（cpu_pct, ram_pct, gpu_pct, gpu_temp_c, cpu_temp_c, fah, ip, hostname, cpu_model, gpu_model）|
| /api/computers/status | GET | 列出所有 PC 的 current snapshot + 24h raw history（每 60s 一點），供 Dashboard 折線圖渲染 |
| /api/agent/ws | WebSocket | PC agent 主動連回 Render 的即時通道。第一階段提供 hello / heartbeat / 在線狀態，後續用來承接 Hue 等區網命令 |
| /api/agent/status | GET | 列出目前連線中的 agent 與 capabilities，需 `X-API-Key` |
| /api/lighting/areas | GET | 透過在線 PC agent 讀取 Hue rooms / zones / grouped_light（含各區當下 on/brightness、該區一般 scene / 全天 smart_scene、通知動作、可用燈效），並合併 Sheet「Hue 照明區域」的顯示名稱 |
| /api/lighting/areas/{area_id} | PATCH | 更新 Hue 區域顯示名稱，寫入 Sheet「Hue 照明區域」 |
| /api/lighting/areas/{area_id}/state | PATCH | 控制 Hue 區域 grouped_light 的電源 (on) 與亮度 (brightness 1–100)，透過 agent 的 `hue.set_state` 下發 |
| /api/lighting/scenes/{scene_id}/recall | POST | 套用 Hue App 內已建立的一般 scene 或全天 smart_scene，透過 agent 的 `hue.recall_scene` 下發 |
| /api/lighting/areas/{area_id}/notification | POST | 對 Hue 區域下發通知動作，例如 `alert:breathe` 呼吸燈；若 Bridge 回傳 signaling 支援值也會列入通知清單 |
| /api/lighting/areas/{area_id}/effect | POST | 對區域內支援指定 effect 的燈具套用燈效，透過 agent 的 `hue.set_effect` 下發；部分支援時只套用支援的燈 |
| /api/lighting/breathe | POST | 透過在線 PC agent 對指定 Hue grouped_light 觸發 breathe |
| /api/lighting/auto/rules | GET | 列出所有 Hue 區域的自動夜燈規則 + runtime state（時段旗標、最後亮度值與時間） |
| /api/lighting/auto/rules/{area_id} | PATCH | 設定 / 更新該區域的自動夜燈規則（光感應器、亮度門檻 1–20、場景、開燈亮度 1–100、啟用時段 HH:MM 可跨午夜、啟用開關）。啟用且當下在時段內會立即評估一次 |
| /api/lighting/auto/rules/{area_id} | DELETE | 刪除該區域的自動夜燈規則 |
| /api/lighting/auto/sensors | GET | 自動夜燈可選的光感應器清單（「智能居家」分頁啟用中的感應器） |
| /api/lighting/auto/sensors/{device_id}/light-level | GET | 系統當下可得的最新 lightLevel（1~20）：6 分鐘內的 webhook 快取優先（附 `age_seconds` 資料年齡），否則打 SwitchBot status 雲端快取（樣本時間未知，`age_seconds=null`）。`light_level=null` 表示該設備不回報亮度（不是 Hub 2） |
| /api/assistant | POST | 自然語言入口（Siri 捷徑用）。body `{text, user_id?}`，走跟 LINE bot 相同的 Claude pipeline，回 `{reply}`；對話歷史背景存檔支援多輪。`user_id` 不帶則用 `SIRI_USER_ID` |

---

## Siri 語音控制（iOS 捷徑）

讓你用「嘿 Siri」就能控制家電、查詢、記待辦。Siri 只負責**把語音聽寫成文字**，POST 到 `/api/assistant`，剩下交給後端跑跟 LINE bot 一模一樣的 Claude pipeline（解析 → action 分派 → 組句），回傳 `reply` 給 Siri 朗讀。

### 運作原理

```
語音 →(Siri 聽寫)→ 文字 →POST /api/assistant→ process_message（共用 LINE 那套）→ {reply} →(Siri 朗讀)
```

`/api/assistant` 與 LINE webhook 共用 `assistant.py:process_message`，所以 LINE 能做的指令 Siri 都能做，行為一致。

### 身分辨識（重要）

捷徑送的 HTTP request 除了你寫進去的內容**沒有任何身分資訊**（不像 LINE 有簽章帶 user_id），所以「誰在講話」只能靠捷徑裡帶的 `user_id`：

- **每位家人（含自己）的捷徑都各自帶自己的 Line User ID** → 後端認得出是誰，給對應名字、自訂風格、各自的對話記憶。
- 沒帶 `user_id` → fallback 成 `SIRI_USER_ID`（預設中性 `siri`）：當匿名訪客，照常控制家電，但無名字/無風格、記憶獨立一份，不會冒名任何人。
- 因此 **`SIRI_USER_ID` 環境變數不要設成任何人的真實 Line ID**，維持中性即可。
- Line User ID 怎麼拿：見上方「取得家庭成員 Line User ID」，或直接從「家庭成員」分頁複製。

### iPhone 捷徑設定

「捷徑」App → **+** 新增捷徑，依序加 4 個動作：

1. **聽寫文字（Dictate Text）**
   - 把語音轉成文字。語言設成「國語（台灣）」。
   - 展開把 **「停止聽寫」設成「暫停後（After Pause）」**，這樣講完停頓就自動結束，不會卡在等待狀態。

2. **取得 URL 內容（Get Contents of URL）**
   - URL：`https://<你的 render 網址>/api/assistant`
   - 展開「顯示更多」：
     - **方法**：`POST`
     - **標頭**：新增一列 `X-API-Key` = 你的 `HOME_BUTLER_API_KEY`
     - **請求內文**：選 `JSON`，新增欄位：
       - 文字 `text` = 步驟 1 的「聽寫文字」變數
       - 文字 `user_id` = **你自己的 Line User ID**（每人填自己的）

3. **取得字典值（Get Dictionary Value）**
   - 取得「值」，鍵 `reply`，來源是步驟 2 的「URL 內容」。

4. 讓步驟 3 的**「字典值」當捷徑的最終輸出** → 用「嘿 Siri」喊出來時，Siri 會**自動朗讀**這段 reply。
   - ⚠️ 不要靠「朗讀文字（Speak Text）」動作：它只在編輯頁測試時出聲，透過 Hey Siri 觸發時會被 Siri 的 audio session 壓掉而不朗讀。把回覆文字當最終輸出交給 Siri 唸才可靠。

把捷徑取個好喊的名字（例如「管家」），之後說「**嘿 Siri，管家**」→ 聽到提示音 → 講指令（「把冷氣調到 26 度」）→ Siri 朗讀回覆。

### 家人共用

把捷徑分享給家人（捷徑右上分享），他們在自己手機把步驟 2 的 `user_id` 改成**自己的** Line User ID 即可。多支沒帶 ID 的捷徑會共用同一個匿名 `siri` 身分與記憶（多輪對話會互相串），所以匿名只適合一次性單句指令。

---

## Claude API 支援的 action

### 食品庫存

| action | 說明 | 欄位 |
|--------|------|------|
| add_food | 新增食品 | name, quantity, unit, expiry |
| delete_food | 食品全部用完，移至封存 | name |
| modify_food | 修改食品 | name，選填：name_new, quantity, unit, expiry |
| query_food | 查詢食品庫存 | 無 |

### 待辦事項

| action | 說明 | 欄位 |
|--------|------|------|
| add_todo | 新增待辦（指派他人時自動通知） | item, date，選填：time, person, type, light_notify, light_area |
| modify_todo | 修改待辦（唯讀項目會被拒絕） | item，選填：item_new, date, time, person, type, light_notify, light_area |
| delete_todo | 標記完成，移至封存（唯讀項目會被拒絕） | item |
| query_todo | 查詢待辦（自動同步外部行事曆） | 無 |
| add_recurring_todo | 新增週期提醒（自動在對的日子產生當日待辦） | item, recur_type（每天/每週/每月/間隔天），選填：weekdays（每週，[1,3,5]，一=1…日=7）, month_day（每月，1~31）, interval_days（間隔天，>=1）, time, person, type, light_notify, light_area, start_date, end_date |
| modify_recurring_todo | 修改週期提醒（多筆同名加 recur_type 消歧） | item，選填：item_new, recur_type_new, weekdays, month_day, interval_days, time, person, type, end_date |
| stop_recurring_todo | 永久停止整個週期（模板改停用，可再啟用；執行前先反問確認） | item，選填：recur_type |
| query_recurring_todo | 列出啟用中的週期提醒 | 無 |

有時間的家事/起身處理類待辦（例如收衣服、倒垃圾、拿包裹、關瓦斯）在對話新增時會預設開啟 `light_notify=true`；使用者明確說不要燈光提醒時會優先關閉。若只說要燈光提醒但沒指定區域，預設使用客廳。

週期 vs 單次：說「每天/每週X/每月N號/每隔N天提醒」走 `add_recurring_todo`；說「明天/某個日期」走 `add_todo`。對週期產生出的當次待辦說「做完了」用 `delete_todo`（只完成當次，模板不動、下次照常出現）；說「不要再…了/停掉每天的X」才用 `stop_recurring_todo`（永久停整個週期，執行前會先反問確認）。

### 智能居家

| action | 說明 | 欄位 |
|--------|------|------|
| control_ac | 控制空調（IR） | device_name，選填：power, temperature, mode, fan_speed |
| control_ir | 控制 DIY IR 設備 | device_name, button（開/關自動轉 turnOn/turnOff） |
| query_sensor | 查詢溫濕度感應器 | device_name |
| query_devices | 列出所有已設定設備 | 無 |
| control_dehumidifier | 控制除濕機 | device_name，選填：power, mode, humidity |
| query_dehumidifier | 查詢除濕機狀態 | device_name |
| set_dehumidifier_auto | 設定 sensor 條件式自動除濕模式；可說「主臥除濕機開自動，目標55%」或「全家除濕機都開自動模式，目標55%」。未指定 sensor 時依除濕機位置配對同位置感應器，沒有同位置感應器會回報 | device_name 或 scope=all, auto_mode(on/off), threshold，選填：duration_min, sensor_name |

### 天氣

| action | 說明 | 欄位 |
|--------|------|------|
| query_weather | 查詢天氣預報（含體感溫度） | 選填：date（YYYY-MM-DD，最多 7 天）, location（鄉鎮或縣市） |

天氣查詢使用兩次 Claude API 呼叫：第一次解析使用者意圖（查哪裡、哪天），第二次根據實際天氣數據用管家語氣回覆。支援自然語言如「週末台北冷嗎」「明天會下雨嗎」。

### 廣播

在 LINE 傳送 `@all 訊息內容` 即可對全體家庭成員發送廣播訊息。

- 格式：`@all` + 空格 + 內容
- 全體啟用中的家庭成員都會收到（包含發送者自己）
- 訊息格式：`📢 發送者名稱：內容`
- 廣播內容會存入每位收到者的對話暫存，Claude 可參考上下文
- 不經過 Claude 解析，直接發送

### Dashboard 登入（裝置配對）

在 LINE 傳送 `登入 123456`（6 位驗證碼）即可核准一台 Dashboard PWA 登入。

- 驗證碼由 Dashboard 登入頁顯示，使用者在 Bot 輸入後核准，前端輪詢拿到 session
- 身分（lineUserId / 名字 / 頭像）來自「誰在 Bot 輸入碼」（webhook user_id，已由 LINE 認證）
- 非家庭成員輸入會被拒絕
- 不經過 Claude 解析，regex 抽出 6 位純數字後直接核准（早退、零成本）；對應後端 `device_auth.py` 與 `/api/auth/device/*` 端點

### 排程

| action | 說明 | 欄位 |
|--------|------|------|
| add_schedule | 新增定時排程 | device_name（可唯一設備可省略）, target_action, params, trigger_time |
| delete_schedule | 取消排程（移至封存） | device_name，選填：trigger_time, all |
| query_schedule | 查詢目前所有待執行排程 | 無 |

排程由 polling thread 每 5 分鐘跑一次 realtime tick 負責執行，精準度約 5 分鐘。觸發時間超過 2 小時未執行的排程自動標記為已過期。設備所有排程完成後統一通知建立者（含執行結果與設備目前狀態）。

**冷氣防黴送風**：關冷氣時，若這次以冷氣/除濕（會結露的模式）從最後一次開機算起已運轉 ≥30 分鐘，home-butler 不直接關，而是先切「送風」吹乾蒸發器約 5 分鐘（實際 5~10 分，受 5 分輪詢粒度影響）再由排程自動關閉，降低濕氣悶在機內長黴。全部空調自動套用、免設定；送風期間若重新開冷氣，收尾關會自動取消。經 home-butler 的所有關機路徑（LINE / Dashboard / Siri / Hub 2 按鈕 / 自動關機 timer）都會觸發；唯獨直接用實體遙控器關機因繞過 home-butler 無法攔截。對應排程在「排程指令」分頁以「來源=防黴」標記。

### 風格

| action | 說明 | 欄位 |
|--------|------|------|
| set_style | 設定管家回覆風格 | style（精簡 prompt 指令，30 字以內；空字串 = 恢復預設） |

### 其他

| action | 說明 | 欄位 |
|--------|------|------|
| unclear | 語意不清時反問 | message |

---

## 回覆邏輯

| 情境 | 回覆來源 |
|------|---------|
| 食品 / 待辦的查詢 | Sheet 真實數據（含同步的外部行事曆）→ 第二次 Claude 用管家語氣回覆 |
| 食品 / 待辦的操作（新增、修改、刪除） | Claude 的 reply（管家語氣） |
| 操作唯讀項目（外部行事曆） | 系統回傳提示，請使用者到原本的日曆上操作 |
| 天氣查詢 | 兩次 Claude：第一次解析意圖 → 查天氣 API → 第二次根據數據回覆 |
| 溫濕度查詢 | 查感應器 API → 第二次 Claude 用管家語氣回覆 |
| 設備列表、除濕機狀態 | 程式的即時數據結果 |
| 設備控制成功 | Claude 的 reply |
| 排程操作（新增、取消、查詢） | Claude 的 reply |
| 排程自動執行 | notify_realtime 執行，設備排程全部完成時通知建立者 |
| 設備控制失敗（❌） | 程式的實際錯誤訊息 |
| 廣播（@all） | 直接轉發，不經 Claude |
| 指派待辦給他人 | Claude 的 reply + 自動推送通知給被指派者 |
| 查看風格（「查看風格」「我的風格」「目前風格」） | 直接讀取 Sheet 原值回覆，不經 Claude |
| 設定風格（set_style） | Claude 的 reply（管家語氣） |
| Claude 回傳異常 | 友善的錯誤提示，不顯示 traceback |

---

## 推播機制

| 推播 | 觸發時間 | 內容 |
|------|---------|------|
| 晚間綜合推播 | 晚上 9 點 | 明日天氣預報（含今日比較與體感溫度）+ 食品過期提醒 + 明日與未完成待辦（含明日才會生成的週期待辦預告 🔁，直接從模板算，不必等明天 tick） |
| 即時提醒 | 每 5 分鐘 | 未來 20 分鐘內有時間的待辦 + 整點檢查過時未完成任務 + 排程指令執行 |
| Hue 燈光提醒 | PC agent 每 60 秒 | 有時間、已到期、未完成且燈光提醒=TRUE 的待辦，對每筆設定的 Hue grouped_light 觸發 breathe；同一區域同一輪多筆待辦只呼吸一次 |
| Agent 即時通道 | PC agent 常駐 WebSocket | PC agent 每約 25 秒 heartbeat 到 `/api/agent/ws`，後端可用 `/api/agent/status` 確認在線狀態 |

推播訊息由 Claude 組成自然語氣文字，包含貼心提醒（快過期催促、天氣變化提醒等）。原本分為早上每日推播與晚間天氣兩次推播，現已合併為單一晚間綜合推播，減少 LINE 推播額度消耗。

---

## 自動夜燈機制

每個 Hue 區域可設定一條規則（Dashboard 照明卡片下方）：光感應器（SwitchBot Hub 2）+ 亮度門檻（lightLevel 1~20）+ 觸發場景 + 開燈亮度（1~100）+ 啟用時段（支援跨午夜）。

**時段內邏輯**（規則引擎 `lighting_auto.py`，跑在 home-butler 後端，網頁關閉仍運作）：

| 條件 | 動作 |
|------|------|
| 亮度 ≤ 門檻 且 該區燈是關的 | recall 場景 → 設定開燈亮度 |
| 亮度 ≤ 門檻 且 燈已開著 | 不動作（不覆蓋使用者手動設定） |
| 亮度 > 門檻 且 燈開著 | 關燈 |

時段結束時關燈一次，之後到下個時段前不再理會。

**兩條評估路徑**：

- **SwitchBot Webhook（主）**：startup 自動向 SwitchBot 註冊 `/switchbot/webhook`（一個 token 只能註冊一個 URL）。Hub 2 任一數值（溫/濕/亮度）變化都推 changeReport、皆附當下 lightLevel → 收到就評估，秒級反應。
- **5 分鐘輪詢（輔）**：處理時段開始（主動拉一次評估）、時段結束（關燈）、webhook 漏接兜底。

**已知限制（實測，已接受）**：

- Hub 2 對雲端的亮度回報是「變化幅度驅動」——劇變（如 3→11）秒級更新；小幅變化（如 1↔3）要等數分鐘的定期同步。webhook 訊息雖然秒級送達，裡面搭車的 lightLevel 可能仍是 Hub 上次登錄的舊值，低亮度區間與 SwitchBot APP 直讀值（手機藍牙直連，官方雲端 API 拿不到）可差 ±1~2 級。
- 因此**調門檻一律以 Dashboard 偵測按鈕的數字為準**（規則引擎看的就是同一份數據），不要以 APP 為準。
- 開燈後環境變亮可能跨過門檻造成開關循環（閃爍）——刻意不做抑制，由使用者把門檻設在「夜燈開著時的環境亮度」之上、「天亮亮度」之下迴避（兩個值都可用偵測按鈕實測）。

---

## 資料封存機制

| 分頁 | 觸發條件 | 封存至 |
|------|----------|--------|
| 食品庫存 | delete_food 或 modify_food 數量歸零 | 食品封存 |
| 待辦事項 | delete_todo | 待辦封存 |
| 對話暫存 | 同一用戶超過 6 則（預設值，可調整） | 對話封存 |
| 排程指令 | 排程執行完成或被取消 | 排程封存 |

封存分頁 Claude 不會讀取，只作為歷史紀錄保存。

---

## 外部行事曆整合

### 設計原則

- **同步快取**：外部行事曆事件同步寫入待辦事項 Sheet，讓 Claude 隨時可見
- **屬性控制**：透過「來源」和「屬性」欄位區分本地/外部、可讀寫/唯讀
- **每人獨立**：每個家庭成員可設定不同的外部行事曆來源、篩選條件和權限
- **Sheet 控制**：透過「家庭成員」分頁的欄位控制整合行為，不用改程式碼
- **定期同步**：query_todo、/notify、/notify_realtime 都會觸發同步（先清舊資料再寫入最新）

### 同步機制

每次同步時（`sync_external_events`）：
1. 刪除待辦 Sheet 中所有「來源」欄不為「本地」的項目
2. 根據每位成員的 Notion 設定拉取最新事件
3. 寫入待辦 Sheet，自動填入來源（Notion）和屬性（依成員的「Notion 權限」欄位）

同步頻率：
- 使用者查待辦時即時同步
- /notify（每日推播）觸發同步
- /notify_realtime（realtime tick，每 5 分鐘）觸發同步

### 目前支援

| 來源 | 設定欄位 | 篩選欄位 | 權限欄位 |
|------|---------|---------|---------|
| Notion | Notion Database ID | Notion 篩選 | Notion 權限 |

### 篩選語法

格式：`欄位名:值,欄位名:值`（逗號分隔，AND 關係）

- 包含條件：`Status:Incoming` — Status 欄位包含 "Incoming"
- 排除條件：`名稱分析:!休假事件` — 名稱分析欄位不包含 "休假事件"
- 比對不分大小寫

### 顯示方式

- Claude 的 context（`{todo_info}`）中，唯讀項目以 `[唯讀]` 前綴顯示
- 使用者嘗試刪除或修改唯讀項目時，系統直接回傳提示而非「找不到」
- 推播中外部行事曆事件以 📅 標記

---

## 天氣功能技術細節

- **API**：中央氣象署開放資料 F-D0047 系列（鄉鎮一週逐 12 小時預報）
- **涵蓋範圍**：全台 22 縣市所有鄉鎮，未來 7 天
- **地點解析**：支援模糊比對，「竹北」→ 自動嘗試竹北市/區/鄉/鎮，遍歷所有縣市。台/臺自動轉換
- **顯示資訊**：天氣現象、最高/最低溫度、體感溫度、降雨機率
- **主動查詢**：使用者問天氣 → Claude 解析意圖 → 程式查天氣 API → Claude 用管家語氣回覆
- **推播**：早上帶今日天氣（含體感溫度），晚上帶明日+今日天氣（支援溫差比較）

---

## 效能優化

- **Google Sheets 批次讀取**：RequestContext 使用 values_batch_get 一次讀取所有分頁，取代原本多次個別 API 呼叫
- **Google Sheets 快取**：get_sheet() 快取已認證的 spreadsheet 物件 60 秒，避免每次都重新 OAuth
- **統一裝置狀態快取**：`/api/devices/status` 從 in-memory cache 立即回應；空調控制、5 分鐘背景輪詢與雲端查詢共同更新同一份狀態，避免 Dashboard 等待完整 Sheet 與其他裝置 API
- **Google Sheets 集中寫入**：新增資料統一走 `append_record()`，多欄位修改統一走 `update_row_fields()` 的 batch update，減少 API 呼叫也避免欄位位置散落在 handler 裡
- **背景寫入**：save_conversation（對話暫存）在背景 thread 執行
- **先回覆再存檔**：reply_message 在 save_conversation 之前，使用者體感更快
- **精簡 SYSTEM_PROMPT**：減少 token 數，加速 Claude 回應
- **SSL 驗證關閉**：氣象署 API 的 SSL 憑證有已知問題，使用 `verify=False` 繞過
- **Notion API 日期預過濾**：在 API 層加 `on_or_after` 過濾，只拉今天以後的事件，大幅減少回傳資料量
- **外部行事曆批次寫入**：sync_external_events 使用 `append_rows` 一次寫入所有事件，減少 API 呼叫次數
- **快取索引同步**：多筆刪除操作時同步更新記憶體快取，避免行號偏移

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| main.py | FastAPI 主程式（LINE Webhook、SwitchBot Cloud webhook 接收與 startup 註冊、啟動 polling thread、SwitchBot debug 端點）。訊息處理委派給 `assistant.py` |
| assistant.py | 自然語言處理核心：`process_message`（Claude 解析 → action 分派 → 組句）與 action 路由表。LINE webhook 與 `/api/assistant`（Siri）共用，避免邏輯複製兩份 |
| config.py | 環境變數、LINE/Claude 初始化、時區設定 |
| sheets.py | Google Sheets 存取封裝（RequestContext 批次讀取、快取、append_record / update_row_fields 集中寫入） |
| device_status.py | Dashboard 共用的統一裝置狀態 in-memory cache、裝置目錄與背景刷新 single-flight 控制 |
| prompt.py | SYSTEM_PROMPT 與 Claude 提示詞組裝 |
| conversation.py | 對話暫存管理、Claude API 呼叫、推播訊息生成 |
| notify.py | 推播端點（/notify 晚間綜合推播、/notify_realtime 即時提醒與排程執行） |
| calendar_sync.py | 外部行事曆同步（Notion → 待辦 Sheet） |
| handlers/food.py | 食品庫存 handler（新增、刪除、修改、查詢） |
| handlers/todo.py | 待辦事項 handler（新增、刪除、修改、查詢） |
| handlers/recurring_todo.py | 週期性待辦：模板/實例分離，每 5 分鐘 materialize 當日待辦（冪等查重含活表+封存表）+ add/modify/stop/query 的 CRUD handler，受 RECURRING_TODO_ENABLED 控制生成 |
| handlers/todo_helpers.py | 待辦 / 週期待辦共用工具（燈光提醒自動判斷、布林解析、照明區域解析），從 todo.py 抽出供兩邊複用 |
| handlers/device.py | 智能居家 handler（空調、IR、感應器、除濕機、天氣） |
| handlers/schedule.py | 排程指令 handler（新增、刪除、查詢） |
| handlers/style.py | 自訂風格 handler |
| switchbot_api.py | SwitchBot API v1.1 封裝（認證、設備控制、感應器讀取 含 Meter Pro CO2、DIY IR、webhook 註冊管理） |
| panasonic_api.py | Panasonic Smart App API 封裝（登入、除濕機控制與狀態查詢） |
| lg_api.py | LG ThinQ Connect API 封裝（PAT 認證、裝置探索、除濕機控制與狀態查詢）。除濕機 property 校準點集中在檔案頂部常數 |
| weather_api.py | 中央氣象署 API 封裝（一週預報、全台鄉鎮查詢、體感溫度） |
| observation_api.py | 中央氣象署觀測站即時資料 API（補 weather_api 預報以外的「現在實際多少」） |
| notion_api.py | Notion API 封裝（唯讀查詢、Sheet 篩選條件解析、事件格式化） |
| web_api.py | REST API（裝置控制、待辦、週期待辦、食品、排程、天氣、成員查詢、PC 監控、感測器/空調歷史、除濕機自動規則、Dashboard 裝置配對登入，以及 Siri 自然語言入口 `/api/assistant`） |
| device_auth.py | Dashboard 裝置配對登入（OAuth Device Grant 風格）：發 user_code/device_token、LINE Bot 端核准、PWA 輪詢領 session。狀態存 Sheets「裝置配對」分頁。解 iOS PWA 登入被踢去 Safari 的問題 |
| agent_ws.py | PC agent WebSocket registry（agent hello / heartbeat / 在線狀態），讓 Render 有一條可回到家中區網的即時通道 |
| lighting_api.py | Hue 照明 API（列區域含 on/brightness/場景/通知/燈效、更新顯示名稱、控制電源/亮度、套用場景/通知/燈效、觸發 breathe）＋自動夜燈規則 CRUD / 光感應器清單 / lightLevel 偵測端點，實際 Hue LAN 呼叫交給 PC agent 執行 |
| lighting_auto.py | 自動夜燈規則引擎：SwitchBot Hub 2 亮度條件式控制 Hue 區域。Webhook 推播主路徑（秒級）+ 5min tick 兜底時段邊界與漏接；規則持久化 Sheet「照明自動規則」、runtime in-memory；webhook 進來順手快取各感應器 lightLevel 供偵測端點用。Hue 指令從 sync thread 經 run_coroutine_threadsafe 橋接到 agent WebSocket |
| hue_area_settings.py | Sheet「Hue 照明區域」讀寫：保存 Hue ID 與 Dashboard 顯示名稱的對應 |
| pc_state.py | PC 監控 in-memory ring buffer（24h × 60s/PC），給 `/api/computers/heartbeat` 寫、`/api/computers/status` 讀 |
| sensor_state.py | SwitchBot 感測器 in-memory ring buffer（24h × 5min/sensor）+ Sheet append/backfill。home-butler 每 5min 主動 polling SwitchBot API 寫入 |
| ac_history.py | 空調狀態 in-memory ring buffer（24h × 5min/AC）+ Sheet append/backfill。每 5min snapshot「智能居家」的最後電源/溫度/模式/風速 |
| ring_buffer.py | pc_state / sensor_state / ac_history / dehumidifier_history 共用的純機制（`to_float_or_none`、24h `trim_sheet`）。各模組資料形狀差異刻意不抽繼承基類，只共用這兩段逐字重複的工具 |
| dehumidifier_auto.py | 除濕機條件式自動 ON/OFF（hysteresis + sensor 失聯 fallback + 排他鎖）。品牌無關狀態機，控制/狀態委派給 dehumidifier_driver。runtime state in-memory，rule 設定值持久化到 Sheet「除濕機自動規則」 |
| dehumidifier_driver.py | 除濕機品牌無關 driver：把 Panasonic（auth+gwid+CommandType）與 LG（deviceId+ThinQ property）的控制/狀態差異收斂成統一介面，給 dehumidifier_auto 用。LG 自動模式用「智慧除濕」+ 機體目標 = 外部目標 −`AUTO_TARGET_OFFSET`(10%) |
| dehumidifier_history.py | 除濕機 ON/OFF 狀態 in-memory ring buffer（24h）+ Sheet append/backfill，給 Dashboard 自動模式 chart 背景畫運轉區段 |
| agent/ | Windows PC 端 monitoring agent（agent.py + agent_config.example.py + README）含 watchdog 防 hang + 預設每 5 分鐘自動 git pull + py_compile 驗新 code syntax + 自己 spawn detached process 重啟（不靠 Task Scheduler restart-on-fail）。Render 部署不會 import 這個目錄 |
| requirements.txt | 直接依賴（程式直接 import 的，已 pin 版本）。升級從這裡改起 |
| requirements.lock | 完整鎖檔（含 transitive，共 51 套），由 requirements.txt 在乾淨環境 pip freeze 重生。render.yaml 實際安裝走這個檔以求每次 rebuild 可重現 |
| render.yaml | Render.com 部署設定 |

---

## 後續維護

**調整 Bot 行為**：修改 prompt.py 裡的 SYSTEM_PROMPT，push 後自動重新部署。

**新增功能**：
1. Google Sheets 新增對應分頁
2. 更新 prompt.py 的 SYSTEM_PROMPT 加入新功能描述
3. 在 handlers/ 目錄加入對應的 handle 函數
4. 在 assistant.py 的 `ACTION_HANDLERS` 註冊新的 action 路由（query 類另需視情況加進 `SEMANTIC_ACTIONS` / `REALTIME_ACTIONS`）
5. 如需推播，更新 notify.py 的推播邏輯

**修改 Google Sheets 寫入**：新增列請用 `sheets.append_record()`；同一列多欄位修改請用 `sheets.update_row_fields()`，不要在 handler 裡分散呼叫多次 `update_cell()`。欄位名稱應集中放在該分頁的 header 對照，避免日後調整欄位順序時要到多個 handler 找行號與欄位號。

**新增 SwitchBot 設備**：
1. 在 SwitchBot App 新增設備
2. 瀏覽器打開 /switchbot/devices 取得 Device ID
3. 填入 Google Sheets「智能居家」分頁
4. 若是新設備類型，需在 switchbot_api.py 新增控制函數並更新 SYSTEM_PROMPT

**新增外部行事曆**：
1. 在 Render.com 設定對應的環境變數（如 NOTION_TOKEN）
2. 在 Google Sheets「家庭成員」分頁填入對應欄位和權限設定
3. 若是新的行事曆來源（如 Google Calendar），需新增 API 封裝模組並更新 sync_external_events

**新增監控 PC**：
1. 在那台 PC 上 clone 本 repo
2. 依 `agent/README.md` 流程設置（裝 Python、LibreHardwareMonitor、agent_config.py 填本機 model + API key）
3. Task Scheduler 註冊 ButlerAgent 開機自啟
4. Dashboard `/devices` 頁的「電腦」區塊會自動出現新卡（按 IP 字串排序）

**費用控管**：
- Claude API 按用量計費，每月約 NT$10~30
- 天氣查詢因為兩次 Claude 呼叫，會比一般操作多消耗約 1 倍 token
- 建議在 Anthropic Console 設定 monthly spend limit $5
- 其他服務（Render、UptimeRobot、SwitchBot、氣象署、Notion）均為免費方案
