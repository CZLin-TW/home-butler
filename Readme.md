# 家庭 AI 管家系統

## 系統架構
- **介面**：Line Bot（Messaging API）
- **大腦**：Claude API（claude-sonnet-4-6）
- **資料庫**：Google Sheets
- **Server**：Render.com（Python + FastAPI）
- **排程**：Google Apps Script（每日推播 + 即時提醒 + 晚間天氣）
- **防冷啟動**：UptimeRobot（每 5 分鐘 ping）
- **智能居家**：SwitchBot API v1.1（冷氣 IR 控制 + Hub 溫濕度 + DIY IR 設備）
- **除濕機**：Panasonic Smart App API（電源 / 模式 / 目標濕度控制）
- **天氣**：中央氣象署開放資料 API（全台鄉鎮一週預報）

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
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 |
|------|------|------|--------|------|------|
- 狀態值：待辦 / 已完成
- 類型值：公開 / 私人

**待辦封存**
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 |
|------|------|------|--------|------|------|
- 已完成的待辦自動移至此分頁，Claude 不會讀取

**家庭成員**
| 名稱 | Line User ID | 狀態 | 稱謂 |
|------|-------------|------|------|
- 狀態值：啟用 / 停用
- 稱謂例如「父親,老公,爸爸」（逗號分隔）
- Line User ID 取得方式：家人加好友後傳訊息，從「訊息紀錄」分頁複製（U 開頭）

**訊息紀錄**
| 時間 | 用戶ID | 訊息 |
|------|--------|------|

**對話暫存**
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 角色值：user / assistant
- 每位用戶取最近 6 則帶入 Claude API，超過自動移至對話封存

**對話封存**
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 超過 6 則的舊對話自動移至此分頁，Claude 不會讀取

**智能居家**
| 名稱 | 類型 | 位置 | Device ID | 狀態 | 按鈕 | Auth |
|------|------|------|-----------|------|------|------|
- 類型值：冷氣 / 感應器 / IR / 除濕機
- 狀態值：啟用 / 停用
- 按鈕欄：僅 IR 設備需要填寫，逗號分隔（例如「電源,風速+,風速-,擺頭」）
- Auth 欄：僅 Panasonic 除濕機需要填寫
- SwitchBot Device ID 取得方式：瀏覽器打開 `https://home-butler.onrender.com/switchbot/devices`

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

冷氣（IR 虛擬設備）的 Device ID 通常是 `02-` 開頭，Hub 的 Device ID 是 MAC 地址格式。

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

天氣功能免費，支援全台 22 縣市所有鄉鎮，預報範圍一週。

---

### 十一、防冷啟動

前往 https://uptimerobot.com 註冊免費帳號：
1. 新增 HTTP monitor
2. URL 填 `https://home-butler.onrender.com`
3. 間隔設 5 分鐘
4. Monitor Type 保持 HTTP(s)（免費方案預設 HEAD，Server 已支援）

---

### 十二、排程推播（Google Apps Script）

前往 https://script.google.com 建立新專案「家庭管家推播」，貼入：

```javascript
function sendDailyNotification() {
  var url = "https://home-butler.onrender.com/notify";
  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({}),
    muteHttpExceptions: true
  };
  var response = UrlFetchApp.fetch(url, options);
  Logger.log(response.getContentText());
}

function sendRealtimeNotification() {
  var url = "https://home-butler.onrender.com/notify_realtime";
  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({}),
    muteHttpExceptions: true
  };
  var response = UrlFetchApp.fetch(url, options);
  Logger.log(response.getContentText());
}

function sendWeatherNotification() {
  var url = "https://home-butler.onrender.com/notify_weather";
  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({}),
    muteHttpExceptions: true
  };
  var response = UrlFetchApp.fetch(url, options);
  Logger.log(response.getContentText());
}
```

設定觸發條件（左邊時鐘圖示 →「新增觸發條件」）：
- `sendDailyNotification`：日計時器，上午 10~11 點
- `sendRealtimeNotification`：分鐘計時器，每 15 分鐘
- `sendWeatherNotification`：日計時器，晚上 9~10 點

---

### 十三、取得家庭成員 Line User ID

每位家庭成員：
1. 掃 QR Code 加管家好友
2. 傳任何一則訊息
3. 從「訊息紀錄」分頁複製 User ID（U 開頭）
4. 填入「家庭成員」分頁

---

## Render 環境變數

| 變數名稱 | 說明 |
|----------|------|
| LINE_CHANNEL_ACCESS_TOKEN | Line Bot 的 Channel Access Token |
| LINE_CHANNEL_SECRET | Line Bot 的 Channel Secret |
| SPREADSHEET_ID | Google Sheets 的試算表 ID（網址中間那串） |
| GOOGLE_CREDENTIALS | Google Service Account 的 JSON 金鑰（整個內容，從 { 到 }） |
| ANTHROPIC_API_KEY | Claude API Key（sk-ant- 開頭） |
| SWITCHBOT_TOKEN | SwitchBot 開發者 Token |
| SWITCHBOT_SECRET | SwitchBot 開發者 Secret Key |
| PANASONIC_ACCOUNT | Panasonic Smart App 帳號 |
| PANASONIC_PASSWORD | Panasonic Smart App 密碼 |
| CWA_API_KEY | 中央氣象署開放資料授權碼 |

---

## Server 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| / | GET / HEAD | 健康檢查（UptimeRobot 用） |
| /callback | POST | Line Webhook 接收訊息 |
| /notify | POST | GAS 呼叫，觸發每日推播（食品過期 + 待辦 + 溫濕度 + 今日天氣） |
| /notify_realtime | POST | GAS 呼叫，每 15 分鐘檢查即時提醒 |
| /notify_weather | POST | GAS 呼叫，晚上推播明日天氣（含今天比較，Claude 可提醒溫差） |
| /switchbot/devices | GET | 查看 SwitchBot 帳號下所有設備與 Device ID |
| /switchbot/test/{device_id}/{button} | GET | 測試 IR 按鈕（customize 模式） |
| /switchbot/test_turnon/{device_id} | GET | 測試 turnOn 指令 |

---

## Claude API 支援的 action

### 食品庫存

| action | 說明 | 欄位 |
|--------|------|------|
| add_food | 新增食品 | name, quantity, unit, expiry |
| delete_food | 食品全部用完，移至封存 | name |
| modify_food | 修改食品數量 | name, quantity |
| query_food | 查詢食品庫存 | 無 |

### 待辦事項

| action | 說明 | 欄位 |
|--------|------|------|
| add_todo | 新增待辦 | item, date，選填：time, person, type |
| modify_todo | 修改待辦 | item，選填：date, time, person, type |
| delete_todo | 標記完成，移至封存 | item |
| query_todo | 查詢待辦（只顯示自己的私人 + 所有公開） | 無 |

### 智能居家

| action | 說明 | 欄位 |
|--------|------|------|
| control_ac | 控制冷氣（IR） | device_name，選填：power, temperature, mode, fan_speed |
| control_ir | 控制 DIY IR 設備 | device_name, button（開/關自動轉 turnOn/turnOff） |
| query_sensor | 查詢溫濕度感應器 | device_name |
| query_devices | 列出所有已設定設備 | 無 |
| control_dehumidifier | 控制除濕機 | device_name，選填：power, mode, humidity |
| query_dehumidifier | 查詢除濕機狀態 | device_name |

### 天氣

| action | 說明 | 欄位 |
|--------|------|------|
| query_weather | 查詢天氣預報 | 選填：date（YYYY-MM-DD，最多 7 天）, location（鄉鎮或縣市） |

天氣查詢使用兩次 Claude API 呼叫：第一次解析使用者意圖（查哪裡、哪天），第二次根據實際天氣數據用管家語氣回覆。支援自然語言如「週末台北冷嗎」「明天會下雨嗎」。

### 其他

| action | 說明 | 欄位 |
|--------|------|------|
| unclear | 語意不清時反問 | message |

---

## 回覆邏輯

| 情境 | 回覆來源 |
|------|---------|
| 食品 / 待辦的查詢與操作 | Claude 的 reply（管家語氣） |
| 天氣查詢 | 兩次 Claude：第一次解析意圖 → 查天氣 API → 第二次根據數據回覆 |
| 溫濕度查詢、設備列表、除濕機狀態 | 程式的即時數據結果 |
| 設備控制成功 | Claude 的 reply |
| 設備控制失敗（❌） | 程式的實際錯誤訊息 |
| Claude 回傳異常 | 友善的錯誤提示，不顯示 traceback |

---

## 推播機制

| 推播 | 觸發時間 | 內容 |
|------|---------|------|
| 每日推播 | 早上 10 點 | 食品過期提醒 + 本週待辦 + 溫濕度 + 今日天氣 |
| 即時提醒 | 每 15 分鐘 | 未來 20 分鐘內有時間的待辦 + 整點檢查過時未完成任務 |
| 晚間天氣 | 晚上 9 點 | 明日天氣預報（含今天比較，Claude 可提醒溫差變化） |

推播訊息由 Claude 組成自然語氣文字，包含貼心提醒（快過期催促、天氣變化提醒等）。

---

## 資料封存機制

| 分頁 | 觸發條件 | 封存至 |
|------|----------|--------|
| 食品庫存 | delete_food 或 modify_food 數量歸零 | 食品封存 |
| 待辦事項 | delete_todo | 待辦封存 |
| 對話暫存 | 同一用戶超過 6 則 | 對話封存 |

封存分頁 Claude 不會讀取，只作為歷史紀錄保存。

---

## 天氣功能技術細節

- **API**：中央氣象署開放資料 F-D0047 系列（鄉鎮一週逐 12 小時預報）
- **涵蓋範圍**：全台 22 縣市所有鄉鎮，未來 7 天
- **地點解析**：支援模糊比對，「竹北」→ 自動嘗試竹北市/區/鄉/鎮，遍歷所有縣市。台/臺自動轉換
- **顯示資訊**：天氣現象、最高/最低溫度、體感溫度、降雨機率
- **主動查詢**：使用者問天氣 → Claude 解析意圖 → 程式查天氣 API → Claude 用管家語氣回覆
- **推播**：早上帶今日天氣，晚上帶明日+今日天氣（支援溫差比較）

---

## 效能優化

- **Google Sheets 快取**：get_sheet() 快取已認證的 spreadsheet 物件 60 秒，避免每次都重新 OAuth
- **背景寫入**：log_message（訊息紀錄）和 save_conversation（對話暫存）在背景 thread 執行
- **先回覆再存檔**：reply_message 在 save_conversation 之前，使用者體感更快
- **精簡 SYSTEM_PROMPT**：減少 token 數，加速 Claude 回應
- **SSL 驗證關閉**：氣象署 API 的 SSL 憑證有已知問題，使用 `verify=False` 繞過

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| main.py | FastAPI 主程式（Webhook、Claude 串接、所有 handler、推播邏輯） |
| switchbot_api.py | SwitchBot API v1.1 封裝（認證、設備控制、感應器讀取、DIY IR） |
| panasonic_api.py | Panasonic Smart App API 封裝（登入、除濕機控制與狀態查詢） |
| weather_api.py | 中央氣象署 API 封裝（一週預報、全台鄉鎮查詢、體感溫度） |
| requirements.txt | Python 套件 |
| render.yaml | Render.com 部署設定 |

---

## 後續維護

**調整 Bot 行為**：修改 main.py 裡的 SYSTEM_PROMPT，push 後自動重新部署。

**新增功能**：
1. Google Sheets 新增對應分頁
2. 更新 SYSTEM_PROMPT 加入新功能描述
3. 在 main.py 加入對應的 handle 函數
4. 更新 /notify 端點加入新的推播邏輯

**新增 SwitchBot 設備**：
1. 在 SwitchBot App 新增設備
2. 瀏覽器打開 /switchbot/devices 取得 Device ID
3. 填入 Google Sheets「智能居家」分頁
4. 若是新設備類型，需在 switchbot_api.py 新增控制函數並更新 SYSTEM_PROMPT

**費用控管**：
- Claude API 按用量計費，每月約 NT$10~20
- 天氣查詢因為兩次 Claude 呼叫，會比一般操作多消耗約 1 倍 token
- 建議在 Anthropic Console 設定 monthly spend limit $5
- 其他服務（Render、GAS、UptimeRobot、SwitchBot、氣象署）均為免費方案

**開新對話時**：把 README.md、main.py、switchbot_api.py、panasonic_api.py、weather_api.py 的內容一起貼給 AI，即可完整接手這個專案。