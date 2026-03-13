# 家庭 AI 管家系統

## 系統架構
- **介面**：Line Bot（Messaging API）
- **大腦**：Claude API（claude-sonnet-4-5）
- **資料庫**：Google Sheets
- **Server**：Render.com（Python + FastAPI）
- **排程**：Google Apps Script（每日推播）
- **防冷啟動**：UptimeRobot（每 5 分鐘 ping）

## Render 環境變數
| 變數名稱 | 說明 |
|----------|------|
| LINE_CHANNEL_ACCESS_TOKEN | Line Bot 的 Channel Access Token |
| LINE_CHANNEL_SECRET | Line Bot 的 Channel Secret |
| SPREADSHEET_ID | Google Sheets 的試算表 ID |
| GOOGLE_CREDENTIALS | Google Service Account 的 JSON 金鑰（整個內容） |
| ANTHROPIC_API_KEY | Claude API Key |

## Server 端點
| 端點 | 方法 | 說明 |
|------|------|------|
| / | GET | 健康檢查 |
| /callback | POST | Line Webhook 接收訊息 |
| /notify | POST | GAS 呼叫，觸發每日推播 |

## Google Sheets 分頁結構

### 食品庫存
| 品名 | 數量 | 單位 | 過期日 | 新增日 | 新增者 | 狀態 |
|------|------|------|--------|--------|--------|------|
- 狀態值：有效 / 已消耗

### 待辦事項
| 事項 | 日期 | 時間 | 負責人 | 狀態 |
|------|------|------|--------|------|
- 狀態值：待辦 / 已完成

### 家庭成員
| 名稱 | Line User ID | 狀態 |
|------|-------------|------|
- 狀態值：啟用 / 停用

### 訊息紀錄
| 時間 | 用戶ID | 訊息 |
|------|--------|------|

### 對話暫存
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 角色值：user / assistant
- 每位用戶取最近 6 則帶入 Claude API

## Claude API 支援的 action
| action | 說明 | 必要欄位 |
|--------|------|----------|
| add_food | 新增食品 | name, quantity, unit, expiry |
| delete_food | 刪除食品 | name |
| query_food | 查詢食品庫存 | 無 |
| add_todo | 新增待辦 | item, date（選填：time, person） |
| delete_todo | 刪除待辦 | item |
| query_todo | 查詢待辦 | 無 |
| unclear | 語意不清反問 | message |

## GAS 設定
- 函數：sendDailyNotification
- 觸發：每天定時（上午 10~11 點）
- 動作：POST 到 /notify 端點

## UptimeRobot 設定
- 監控網址：https://home-butler.onrender.com
- 間隔：每 5 分鐘