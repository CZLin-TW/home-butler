# 家庭 AI 管家系統

## 系統架構
- **介面**：Line Bot（Messaging API）
- **大腦**：Claude API（claude-sonnet-4-5）
- **資料庫**：Google Sheets
- **Server**：Render.com（Python + FastAPI）
- **排程**：Google Apps Script（每日推播 + 即時提醒）
- **防冷啟動**：UptimeRobot（每 5 分鐘 ping）

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
pip install fastapi uvicorn line-bot-sdk gspread google-auth anthropic pytz
```

建立 `requirements.txt`：
```
fastapi
uvicorn
line-bot-sdk
gspread
google-auth
anthropic
pytz
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
   - `https://你的服務名稱.onrender.com/callback`
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

**待辦事項**
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 |
|------|------|------|--------|------|------|
- 狀態值：待辦 / 已完成
- 類型值：公開 / 私人

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
- 每位用戶取最近 6 則帶入 Claude API

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

### 八、防冷啟動

前往 https://uptimerobot.com 註冊免費帳號：
1. 新增 HTTP monitor
2. URL 填 `https://你的服務名稱.onrender.com`
3. 間隔設 5 分鐘

---

### 九、每日推播 + 即時提醒（Google Apps Script）

前往 https://script.google.com 建立新專案「家庭管家推播」，貼入：

```javascript
function sendDailyNotification() {
  var url = "https://你的服務名稱.onrender.com/notify";
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
  var url = "https://你的服務名稱.onrender.com/notify_realtime";
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

---

## Render 環境變數

| 變數名稱 | 說明 |
|----------|------|
| LINE_CHANNEL_ACCESS_TOKEN | Line Bot 的 Channel Access Token |
| LINE_CHANNEL_SECRET | Line Bot 的 Channel Secret |
| SPREADSHEET_ID | Google Sheets 的試算表 ID（網址中間那串） |
| GOOGLE_CREDENTIALS | Google Service Account 的 JSON 金鑰（整個內容，從 { 到 }） |
| ANTHROPIC_API_KEY | Claude API Key（sk-ant- 開頭） |

---

## Server 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| / | GET | 健康檢查 |
| /callback | POST | Line Webhook 接收訊息 |
| /notify | POST | GAS 呼叫，觸發每日推播 |
| /notify_realtime | POST | GAS 呼叫，每 15 分鐘檢查即時提醒 |

---

## Claude API 支援的 action

| action | 說明 | 必要欄位 |
|--------|------|----------|
| add_food | 新增食品 | name, quantity, unit, expiry |
| delete_food | 刪除食品 | name |
| query_food | 查詢食品庫存 | 無 |
| add_todo | 新增待辦 | item, date（選填：time, person, type） |
| delete_todo | 刪除待辦 | item |
| query_todo | 查詢待辦 | 無 |
| unclear | 語意不清反問 | message |

type 規則：
- 使用者說「提醒我」、「我要」或未指定負責人 → 私人
- 使用者說「提醒大家」、「提醒全家」或明確說「公開」 → 公開
- 預設為私人

---

## 後續維護

**調整 Bot 行為**：修改 main.py 裡的 SYSTEM_PROMPT，用中文描述你要的行為，push 後自動重新部署。

**新增功能**：
1. Google Sheets 新增對應分頁
2. 更新 SYSTEM_PROMPT 加入新功能描述
3. 在 main.py 加入對應的 handle 函數
4. 更新 /notify 端點加入新的推播邏輯

**費用控管**：
- Claude API 按用量計費，每月約 NT$10~16
- 建議在 Anthropic Console 設定 monthly spend limit $5
- Render、GAS、UptimeRobot 免費方案即可

**開新對話時**：把 README.md 和 main.py 的內容一起貼給 AI，即可完整接手這個專案。
