# 家庭 AI 管家系統

## 這是什麼？

一個用 LINE Bot 操作的家庭智能管家系統。家人只要會用 LINE 就能使用——傳訊息給管家，它就會幫你管理食品庫存、待辦事項、智能家電、天氣查詢，還會每天主動推播提醒。

本專案 100% 由 Claude AI 協作完成，包含架構設計、所有程式碼、文件撰寫。

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
| 外部行事曆整合 | Notion 行事曆整合，自動同步到待辦 Sheet 並標記屬性（唯讀/讀寫），支援 Sheet 自訂篩選條件 |
| 空調控制 | 開關、溫度、模式、風速（SwitchBot Hub IR），記錄最後狀態供 Dashboard 顯示與下次相對調整使用 |
| 除濕機控制 | 開關、模式、目標濕度（Panasonic Smart App） |
| DIY IR 設備 | 電風扇等紅外線家電的開關與自訂按鈕 |
| 溫濕度查詢 | 即時讀取室內溫度與濕度 |
| 天氣預報 | 全台鄉鎮一週天氣（含體感溫度），支援自然語言查詢 |
| 廣播訊息 | `@all` 開頭可對全體家庭成員發送訊息 |
| 晚間綜合推播 | 晚上：明日天氣預報 + 食品過期提醒 + 明日與未完成待辦 |
| 即時提醒 | 每 15 分鐘檢查即將到來的待辦 |
| 排程指令 | 定時操作家電（如「11 點關電風扇」「睡前調 27 度，早上 8 點關」），設備排程完成時自動通知 |
| 自訂風格 | 每位成員可自訂管家回覆風格（語氣、角色扮演等），也可隨時恢復預設 |
| 指派通知 | 指派待辦給其他家庭成員時，對方即時收到 LINE 通知 |

---
## 需要的資源

### 帳號與服務（全部免費方案即可）

| 服務 | 用途 | 費用 |
|------|------|------|
| [LINE Developers](https://developers.line.biz/) | Messaging API Bot | 免費 |
| [Google Cloud](https://console.cloud.google.com/) | Sheets API + Drive API（Service Account） | 免費 |
| [Google Sheets](https://sheets.google.com/) | 資料庫（食品、待辦、設備、對話、排程） | 免費 |
| [Google Apps Script](https://script.google.com/) | 排程觸發推播 | 免費 |
| [Render.com](https://render.com/) | Python FastAPI Server 部署 | 免費（Free Instance） |
| [Anthropic](https://console.anthropic.com/) | Claude API（自然語言理解） | 按量計費（見下方） |
| [UptimeRobot](https://uptimerobot.com/) | 每 5 分鐘 ping 防止 Render 休眠 | 免費 |
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
- **介面**：Line Bot（Messaging API）
- **大腦**：Claude API（claude-sonnet-4-6）
- **資料庫**：Google Sheets
- **Server**：Render.com（Python + FastAPI）
- **排程**：Google Apps Script（晚間綜合推播 + 即時提醒）
- **防冷啟動**：UptimeRobot（每 5 分鐘 ping）
- **智能居家**：SwitchBot API v1.1（空調 IR 控制 + Hub 溫濕度 + DIY IR 設備）
- **除濕機**：Panasonic Smart App API（電源 / 模式 / 目標濕度控制）
- **天氣**：中央氣象署開放資料 API（全台鄉鎮一週預報，含體感溫度）
- **外部行事曆**：Notion API（同步到待辦 Sheet，支援每人獨立篩選條件與權限設定）
- **Dashboard API**：REST API（供網頁版 Dashboard 直接操作裝置、待辦、食品、排程等）

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
pip install fastapi uvicorn line-bot-sdk gspread google-auth anthropic pytz httpx
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
   - Build Command: `pip install -r requirements.txt`
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
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 | 來源 | 屬性 |
|------|------|------|--------|------|------|------|------|
- 狀態值：待辦 / 已完成
- 類型值：公開 / 私人
- 來源值：本地 / Notion（程式自動填入，本地新增的待辦填「本地」，外部行事曆同步的填來源名稱）
- 屬性值：讀寫 / 唯讀（本地項目為「讀寫」，外部項目依成員的權限設定填入）

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
- Notion 權限：「唯讀」或「讀寫」，控制同步到待辦 Sheet 時的屬性值。目前 Notion 整合為唯讀，填「唯讀」
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
| 名稱 | 類型 | 位置 | Device ID | 狀態 | 按鈕 | Auth | 控制類型 | 最後電源 | 最後溫度 | 最後模式 | 最後風速 | 最後更新時間 |
|------|------|------|-----------|------|------|------|---------|---------|---------|---------|---------|------------|
- 類型值：空調 / 感應器 / IR / 除濕機
- 狀態值：啟用 / 停用
- 按鈕欄：僅 IR 設備需要填寫，逗號分隔（例如「電源,風速+,風速-,擺頭」）
- Auth 欄：僅 Panasonic 除濕機需要填寫
- 控制類型值：雙向 / 單向絕對 / 單向相對 / 唯讀（描述設備的控制能力，供 Claude 和 Dashboard 判斷）
- 最後電源／最後溫度／最後模式／最後風速／最後更新時間：**僅空調設備使用**，由程式自動寫入，不需手動填。Dashboard 用來顯示最後狀態，LINE bot 用來支援「調低 1 度」這類相對指令。手動建 sheet 時這 5 欄保持空白即可
- SwitchBot Device ID 取得方式：瀏覽器打開 `https://home-butler.onrender.com/switchbot/devices`

**排程指令**
| 設備名稱 | 動作 | 參數 | 觸發時間 | 建立者 | 建立時間 | 狀態 |
|---------|------|------|---------|--------|---------|------|
- 動作值：control_ac / control_ir / control_dehumidifier
- 參數：JSON 字串（例如 `{"temperature":27,"power":"on"}`）
- 狀態值：待執行 / 已執行 / 已過期 / 已取消

**排程封存**
| 設備名稱 | 動作 | 參數 | 觸發時間 | 建立者 | 建立時間 | 狀態 |
|---------|------|------|---------|--------|---------|------|
- 設備所有排程完成後統一移至此分頁，Claude 不會讀取

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

### 九、Panasonic 除濕機設定

1. 在 Render.com 新增環境變數 `PANASONIC_ACCOUNT` 和 `PANASONIC_PASSWORD`（Panasonic Smart App 帳密）
2. 在 Google Sheets「智能居家」分頁新增一行：
   - 名稱：自訂（例如「除濕機」）
   - 類型：除濕機
   - Device ID：Panasonic 的 GWID
   - Auth：Panasonic 的 Device Auth
   - 狀態：啟用

---

### 十、天氣預報設定

1. 前往 https://opendata.cwa.gov.tw 註冊帳號
2. 登入後到「會員中心」→「取得授權碼」
3. 在 Render.com 新增環境變數 `CWA_API_KEY`（貼上授權碼）

天氣功能免費，支援全台 22 縣市所有鄉鎮，預報範圍一週，包含體感溫度。

---

### 十一、Notion 行事曆整合（選配）

Notion 整合會將事件同步到待辦事項 Sheet，並依權限設定標記為唯讀或讀寫。同步會在以下時機自動執行：使用者查詢待辦（query_todo）、每日推播（/notify）、每 15 分鐘即時提醒（/notify_realtime）。

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
- `Notion 權限`：填「唯讀」或「讀寫」（目前 Notion 整合為唯讀，填「唯讀」）

篩選條件範例：
- `Status:Incoming,person:CZ` — 只顯示狀態為 Incoming 且 person 為 CZ 的事件
- `Status:Incoming,person:CZ,名稱分析:!休假事件` — 再排除名稱分析為「休假事件」的項目
- `!` 開頭表示排除條件

每個家庭成員可以各自設定不同的 Database ID 和篩選條件，沒有填的成員不會整合 Notion。

---

### 十二、防冷啟動

前往 https://uptimerobot.com 註冊免費帳號：
1. 新增 HTTP monitor
2. URL 填 `https://home-butler.onrender.com`
3. 間隔設 5 分鐘
4. Monitor Type 保持 HTTP(s)（免費方案預設 HEAD，Server 已支援）

---

### 十三、排程推播（Google Apps Script）

前往 https://script.google.com 建立新專案「家庭管家推播」。

**先設定 API Key**：左邊齒輪「專案設定」→ 滑到底「指令碼屬性」→ 新增屬性 `HOME_BUTLER_API_KEY`，值填與 Render 上相同的 key。**不要把 key 寫死在 code 裡**。

然後在編輯器貼入：

```javascript
function callHomeButler(endpoint) {
  var apiKey = PropertiesService.getScriptProperties().getProperty("HOME_BUTLER_API_KEY");
  var url = "https://home-butler.onrender.com" + endpoint;
  var options = {
    method: "post",
    contentType: "application/json",
    headers: { "X-API-Key": apiKey },
    payload: JSON.stringify({}),
    muteHttpExceptions: true
  };
  var response = UrlFetchApp.fetch(url, options);
  Logger.log(response.getResponseCode() + ": " + response.getContentText());
}

function sendDailyNotification() {
  callHomeButler("/notify");
}

function sendRealtimeNotification() {
  callHomeButler("/notify_realtime");
}
```

設定觸發條件（左邊時鐘圖示 →「新增觸發條件」）：
- `sendDailyNotification`：日計時器，晚上 9~10 點（晚間綜合推播，含明日天氣 + 食品過期 + 待辦提醒）
- `sendRealtimeNotification`：分鐘計時器，每 15 分鐘

---

### 十四、取得家庭成員 Line User ID

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
| SWITCHBOT_TOKEN | SwitchBot 開發者 Token | 選配 |
| SWITCHBOT_SECRET | SwitchBot 開發者 Secret Key | 選配 |
| PANASONIC_ACCOUNT | Panasonic Smart App 帳號 | 選配 |
| PANASONIC_PASSWORD | Panasonic Smart App 密碼 | 選配 |
| CWA_API_KEY | 中央氣象署開放資料授權碼 | 選配 |
| NOTION_TOKEN | Notion Internal Integration Token | 選配 |

---

## Server 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| / | GET / HEAD | 健康檢查（UptimeRobot 用） |
| /callback | POST | Line Webhook 接收訊息 |
| /notify | POST | GAS 呼叫，晚間綜合推播（明日天氣預報 + 食品過期提醒 + 明日與未完成待辦） |
| /notify_realtime | POST | GAS 呼叫，每 15 分鐘檢查即時提醒 + 排程執行 |
| /switchbot/devices | GET | 查看 SwitchBot 帳號下所有設備與 Device ID |
| /switchbot/test/{device_id}/{button} | GET | 測試 IR 按鈕（customize 模式） |
| /switchbot/test_turnon/{device_id} | GET | 測試 turnOn 指令 |

### Dashboard REST API（/api）

供網頁版 Dashboard 使用，所有業務邏輯重用現有 handlers，不重複實作。

> **🔒 認證**：所有 `/api/*` 端點都要求 `X-API-Key` header，值為環境變數 `HOME_BUTLER_API_KEY`。
> 沒有 header 或 key 不對會回 401；伺服器端未設定 `HOME_BUTLER_API_KEY` 則回 503（fail-closed）。
> 同樣的保護也套用在 `/notify*` 和 `/switchbot/*` 端點。`/`（健康檢查）和 `/callback`（LINE webhook，由 X-Line-Signature 驗證）不在保護範圍內。

| 端點 | 方法 | 說明 |
|------|------|------|
| /api/devices | GET | 列出所有啟用裝置及即時狀態（感應器溫濕度、除濕機狀態） |
| /api/devices/options | GET | 各類裝置的可用選項（空調模式/風速、除濕機模式/濕度），供前端動態渲染 |
| /api/devices/control/ac | POST | 控制空調（power, temperature, mode, fan_speed） |
| /api/devices/control/ir | POST | 控制 IR 裝置（device_name, button） |
| /api/devices/control/dehumidifier | POST | 控制除濕機（power, mode, humidity） |
| /api/devices/sensor | GET | 查詢感測器（device_name） |
| /api/todos | GET | 列出所有待辦事項 |
| /api/todos | POST | 新增待辦事項 |
| /api/todos | PATCH | 修改待辦事項 |
| /api/todos | DELETE | 完成（刪除）待辦事項 |
| /api/food | GET | 列出所有有效食品庫存 |
| /api/food | POST | 新增食品 |
| /api/food | PATCH | 修改食品 |
| /api/food | DELETE | 消耗（刪除）食品 |
| /api/schedules | GET | 列出所有待執行排程 |
| /api/schedules | POST | 新增排程 |
| /api/schedules | DELETE | 取消排程 |
| /api/weather | GET | 查詢天氣（date, location） |
| /api/members | GET | 列出所有啟用的家庭成員 |

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
| add_todo | 新增待辦（指派他人時自動通知） | item, date，選填：time, person, type |
| modify_todo | 修改待辦（唯讀項目會被拒絕） | item，選填：item_new, date, time, person, type |
| delete_todo | 標記完成，移至封存（唯讀項目會被拒絕） | item |
| query_todo | 查詢待辦（自動同步外部行事曆） | 無 |

### 智能居家

| action | 說明 | 欄位 |
|--------|------|------|
| control_ac | 控制空調（IR） | device_name，選填：power, temperature, mode, fan_speed |
| control_ir | 控制 DIY IR 設備 | device_name, button（開/關自動轉 turnOn/turnOff） |
| query_sensor | 查詢溫濕度感應器 | device_name |
| query_devices | 列出所有已設定設備 | 無 |
| control_dehumidifier | 控制除濕機 | device_name，選填：power, mode, humidity |
| query_dehumidifier | 查詢除濕機狀態 | device_name |

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

### 排程

| action | 說明 | 欄位 |
|--------|------|------|
| add_schedule | 新增定時排程 | device_name（可唯一設備可省略）, target_action, params, trigger_time |
| delete_schedule | 取消排程（移至封存） | device_name，選填：trigger_time, all |
| query_schedule | 查詢目前所有待執行排程 | 無 |

排程由 `notify_realtime`（GAS 每 15 分鐘觸發）負責執行，精準度約 15 分鐘。觸發時間超過 2 小時未執行的排程自動標記為已過期。設備所有排程完成後統一通知建立者（含執行結果與設備目前狀態）。

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
| 晚間綜合推播 | 晚上 9 點 | 明日天氣預報（含今日比較與體感溫度）+ 食品過期提醒 + 明日與未完成待辦 |
| 即時提醒 | 每 15 分鐘 | 未來 20 分鐘內有時間的待辦 + 整點檢查過時未完成任務 + 排程指令執行 |

推播訊息由 Claude 組成自然語氣文字，包含貼心提醒（快過期催促、天氣變化提醒等）。原本分為早上每日推播與晚間天氣兩次推播，現已合併為單一晚間綜合推播，減少 LINE 推播額度消耗。

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
- /notify_realtime（每 15 分鐘）觸發同步

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
| main.py | FastAPI 主程式（Webhook、Claude 串接、action 路由） |
| config.py | 環境變數、LINE/Claude 初始化、時區設定 |
| sheets.py | Google Sheets 存取封裝（RequestContext 批次讀取、快取） |
| prompt.py | SYSTEM_PROMPT 與 Claude 提示詞組裝 |
| conversation.py | 對話暫存管理、Claude API 呼叫、推播訊息生成 |
| notify.py | 推播端點（/notify 晚間綜合推播、/notify_realtime 即時提醒與排程執行） |
| calendar_sync.py | 外部行事曆同步（Notion → 待辦 Sheet） |
| handlers/food.py | 食品庫存 handler（新增、刪除、修改、查詢） |
| handlers/todo.py | 待辦事項 handler（新增、刪除、修改、查詢） |
| handlers/device.py | 智能居家 handler（空調、IR、感應器、除濕機、天氣） |
| handlers/schedule.py | 排程指令 handler（新增、刪除、查詢） |
| handlers/style.py | 自訂風格 handler |
| switchbot_api.py | SwitchBot API v1.1 封裝（認證、設備控制、感應器讀取、DIY IR） |
| panasonic_api.py | Panasonic Smart App API 封裝（登入、除濕機控制與狀態查詢） |
| weather_api.py | 中央氣象署 API 封裝（一週預報、全台鄉鎮查詢、體感溫度） |
| notion_api.py | Notion API 封裝（唯讀查詢、Sheet 篩選條件解析、事件格式化） |
| web_api.py | Dashboard REST API（裝置控制、待辦、食品、排程、天氣、成員查詢） |
| requirements.txt | Python 套件 |
| render.yaml | Render.com 部署設定 |

---

## 後續維護

**調整 Bot 行為**：修改 prompt.py 裡的 SYSTEM_PROMPT，push 後自動重新部署。

**新增功能**：
1. Google Sheets 新增對應分頁
2. 更新 prompt.py 的 SYSTEM_PROMPT 加入新功能描述
3. 在 handlers/ 目錄加入對應的 handle 函數
4. 在 main.py 註冊新的 action 路由
5. 如需推播，更新 notify.py 的推播邏輯

**新增 SwitchBot 設備**：
1. 在 SwitchBot App 新增設備
2. 瀏覽器打開 /switchbot/devices 取得 Device ID
3. 填入 Google Sheets「智能居家」分頁
4. 若是新設備類型，需在 switchbot_api.py 新增控制函數並更新 SYSTEM_PROMPT

**新增外部行事曆**：
1. 在 Render.com 設定對應的環境變數（如 NOTION_TOKEN）
2. 在 Google Sheets「家庭成員」分頁填入對應欄位和權限設定
3. 若是新的行事曆來源（如 Google Calendar），需新增 API 封裝模組並更新 sync_external_events

**費用控管**：
- Claude API 按用量計費，每月約 NT$10~30
- 天氣查詢因為兩次 Claude 呼叫，會比一般操作多消耗約 1 倍 token
- 建議在 Anthropic Console 設定 monthly spend limit $5
- 其他服務（Render、GAS、UptimeRobot、SwitchBot、氣象署、Notion）均為免費方案