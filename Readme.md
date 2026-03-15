# 家庭 AI 管家系統

## 系統架構
- **介面**：Line Bot（Messaging API）
- **大腦**：Claude API（claude-sonnet-4-6）
- **資料庫**：Google Sheets
- **Server**：Render.com（Python + FastAPI）
- **排程**：Google Apps Script（每日推播 + 即時提醒）
- **防冷啟動**：UptimeRobot（每 5 分鐘 ping）
- **智能居家**：SwitchBot API v1.1（冷氣 IR 控制 + Hub 溫濕度 + DIY IR 設備）+ Panasonic Smart App API（除濕機）

---

## 核心設計哲學
- **零學習成本**：介面就是每天在用的 Line，會用 Line 就會用這個系統
- **自然語言輸入**：說人話，不需要固定格式
- **主動推播**：系統主動告知，使用者不需要主動查詢
- **資料透明**：所有資料存在 Google Sheets，隨時可直接查看與編輯
- **零廠商鎖定**：每個元件都可替換，沒有單點依賴
- **接近零成本**：每月約 NT$ 10~20 元（僅 Claude API 有費用）

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| `main.py` | FastAPI 主程式（Webhook、Claude 串接、所有 handler、推播邏輯） |
| `switchbot_api.py` | SwitchBot API v1.1 封裝（認證、冷氣控制、感應器讀取、DIY IR） |
| `panasonic_api.py` | Panasonic Smart App API 封裝（帳密登入、除濕機狀態查詢與控制） |
| `requirements.txt` | Python 套件 |
| `render.yaml` | Render.com 部署設定 |

---

## Google Sheets 資料結構

試算表名稱：**家庭管家**，包含以下分頁：

### 食品庫存（Claude 讀取）
| 品名 | 數量 | 單位 | 過期日 | 新增日 | 新增者 | 狀態 |
|------|------|------|--------|--------|--------|------|
- 狀態值：`有效`（消耗後自動移至食品封存）

### 食品封存（Claude 不讀取）
- 欄位同食品庫存，保留歷史紀錄

### 待辦事項（Claude 讀取）
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 |
|------|------|------|--------|------|------|
- 狀態值：`待辦`（完成後自動移至待辦封存）
- 類型值：`公開` / `私人`
- 時間為選填（HH:MM 24小時制）

### 待辦封存（Claude 不讀取）
- 欄位同待辦事項，保留歷史紀錄

### 家庭成員
| 名稱 | Line User ID | 狀態 | 稱謂 |
|------|-------------|------|------|
- 狀態值：`啟用` / `停用`
- 稱謂：逗號分隔（例如「父親,老公,爸爸」）
- Line User ID 取得方式：家人加好友後傳訊息，從「訊息紀錄」分頁複製（U 開頭）

### 訊息紀錄
| 時間 | 用戶ID | 訊息 |
|------|--------|------|

### 對話暫存（Claude 讀取）
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 每位用戶取最近 6 則帶入 Claude API，超過自動移至對話封存

### 對話封存（Claude 不讀取）
- 欄位同對話暫存，保留歷史紀錄

### 智能居家
| 名稱 | 類型 | 位置 | Device ID | Auth | 狀態 | 按鈕 |
|------|------|------|-----------|------|------|------|
- 類型值：`冷氣` / `感應器` / `IR` / `除濕機`
- 狀態值：`啟用` / `停用`
- 按鈕欄：僅 IR 設備需填，逗號分隔（例如「電源,風速+,風速-,擺頭」）
- Auth 欄：僅 Panasonic 除濕機需填（從 `test_panasonic.py` 取得）
- Device ID：
  - SwitchBot 冷氣/IR：`02-` 開頭（IR 虛擬設備）
  - SwitchBot Hub：MAC 地址格式
  - Panasonic 除濕機：GWID（6碼十六進位，如 `2C9FFB636189`）

---

## Render 環境變數

| 變數名稱 | 說明 |
|----------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | Line Bot 的 Channel Access Token |
| `LINE_CHANNEL_SECRET` | Line Bot 的 Channel Secret |
| `SPREADSHEET_ID` | Google Sheets 的試算表 ID（網址中間那串） |
| `GOOGLE_CREDENTIALS` | Google Service Account 的 JSON 金鑰（整個內容，從 `{` 到 `}`） |
| `ANTHROPIC_API_KEY` | Claude API Key（sk-ant- 開頭） |
| `SWITCHBOT_TOKEN` | SwitchBot 開發者 Token |
| `SWITCHBOT_SECRET` | SwitchBot 開發者 Secret Key |
| `PANASONIC_ACCOUNT` | Panasonic IoT TW App 登入 email |
| `PANASONIC_PASSWORD` | Panasonic IoT TW App 登入密碼 |

---

## Server 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| `/` | GET / HEAD | 健康檢查（UptimeRobot 用） |
| `/callback` | POST | Line Webhook 接收訊息 |
| `/notify` | POST | GAS 呼叫，觸發每日推播 |
| `/notify_realtime` | POST | GAS 呼叫，每 15 分鐘檢查即時提醒 |
| `/switchbot/devices` | GET | 查看 SwitchBot 帳號下所有設備與 Device ID |
| `/switchbot/test/{device_id}/{button}` | GET | 測試 IR 按鈕（customize 模式） |
| `/switchbot/test_turnon/{device_id}` | GET | 測試 turnOn 指令 |

---

## Claude API 支援的 action

### 食品庫存
| action | 說明 | 欄位 |
|--------|------|------|
| `add_food` | 新增食品 | name, quantity, unit, expiry |
| `delete_food` | 食品全部用完，移至封存 | name |
| `modify_food` | 修改食品數量 | name, quantity（更新後總數） |
| `query_food` | 查詢食品庫存 | 無 |

### 待辦事項
| action | 說明 | 欄位 |
|--------|------|------|
| `add_todo` | 新增待辦 | item, date，選填：time, person, type |
| `modify_todo` | 修改待辦（只填要改的欄位） | item，選填：date, time, person, type |
| `delete_todo` | 標記完成，移至封存 | item |
| `query_todo` | 查詢待辦（私人只顯示自己的） | 無 |

### 智能居家 - SwitchBot
| action | 說明 | 欄位 |
|--------|------|------|
| `control_ac` | 控制冷氣（IR） | device_name，選填：power, temperature, mode, fan_speed |
| `control_ir` | 控制 DIY IR 設備 | device_name, button |
| `query_sensor` | 查詢溫濕度感應器 | device_name |

### 智能居家 - Panasonic
| action | 說明 | 欄位 |
|--------|------|------|
| `control_dehumidifier` | 控制除濕機 | device_name，選填：power, mode, humidity |
| `query_dehumidifier` | 查詢除濕機狀態 | device_name |

### 其他
| action | 說明 | 欄位 |
|--------|------|------|
| `query_devices` | 列出所有已設定設備 | 無 |
| `unclear` | 語意不清時反問 | message |

#### 除濕機模式對照（control_dehumidifier mode 欄位）
| 說法 | 對應模式 |
|------|---------|
| 連續除濕 | 0 |
| 自動除濕 | 1 |
| 防黴 | 2 |
| 送風 | 3 |
| 目標濕度 | 6 |
| 空氣清淨 | 7 |
| AI舒適 | 8 |
| 省電 | 9 |
| 快速除濕 | 10 |
| 靜音除濕 | 11 |

---

## 推播邏輯

### 每日推播（`/notify`，每天早上 10 點）
- 今天到期食品 🔴
- 3 天內到期食品 🟡
- 本週到期食品 🟢
- SwitchBot Hub 溫濕度 🌡️
- 本週公開待辦（全家人都收到）
- 個人私人待辦（只有負責人收到）
- **過期未完成的待辦也會出現**（標記 ⚠️ 未完成）

### 即時提醒（`/notify_realtime`，每 15 分鐘）
- **情況一**：有時間的待辦，提前 20 分鐘提醒
- **情況二**：整點時（分鐘數 0~4 或 55~59），推播今天所有已過時間但未完成的待辦

### 回覆邏輯
| 情境 | 回覆來源 |
|------|---------|
| 食品 / 待辦的查詢與操作 | Claude 的 reply（管家語氣） |
| 溫濕度查詢、設備列表、除濕機狀態 | 程式的即時數據結果 |
| 設備控制成功 | Claude 的 reply |
| 設備控制失敗（❌） | 程式的實際錯誤訊息 |

---

## 資料封存機制
| 分頁 | 觸發條件 | 封存至 |
|------|----------|--------|
| 食品庫存 | `delete_food` 或 `modify_food` 數量歸零 | 食品封存 |
| 待辦事項 | `delete_todo` | 待辦封存 |
| 對話暫存 | 同一用戶超過 6 則 | 對話封存 |

---

## 效能優化
- **Google Sheets 快取**：`get_sheet()` 快取已認證的 spreadsheet 物件 60 秒
- **背景寫入**：`log_message`、`save_conversation` 在背景 thread 執行
- **先回覆再存檔**：`reply_message` 在 `save_conversation` 之前，使用者體感更快

---

## Google Apps Script 設定

前往 https://script.google.com 建立新專案「家庭管家推播」：

```javascript
function sendDailyNotification() {
  var url = "https://home-butler.onrender.com/notify";
  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({}),
    muteHttpExceptions: true
  };
  UrlFetchApp.fetch(url, options);
}

function sendRealtimeNotification() {
  var url = "https://home-butler.onrender.com/notify_realtime";
  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({}),
    muteHttpExceptions: true
  };
  UrlFetchApp.fetch(url, options);
}
```

觸發條件設定：
- `sendDailyNotification`：日計時器，上午 10~11 點
- `sendRealtimeNotification`：分鐘計時器，每 15 分鐘

---

## SwitchBot 設定

1. 打開 SwitchBot App → 個人 → 偏好設定 → 關於 → 連點 App 版本 10 次 → 開發者選項
2. 複製 Token 和 Secret Key → 填入 Render 環境變數
3. 部署後瀏覽器打開 `/switchbot/devices` 取得 Device ID
4. 填入 Google Sheets「智能居家」分頁

---

## Panasonic 除濕機設定

支援透過 Panasonic IoT TW App 連網的除濕機（使用逆向工程的私有 API）。

1. 在 Render 填入 `PANASONIC_ACCOUNT`、`PANASONIC_PASSWORD`（與 IoT TW App 相同帳密）
2. 執行以下測試腳本取得 Auth 和 GWID：

```python
import panasonic_api
panasonic_api.PANASONIC_ACCOUNT = "你的email"
panasonic_api.PANASONIC_PASSWORD = "你的密碼"
panasonic_api.login()
devices = panasonic_api.get_devices()
for d in devices:
    print(d["NickName"], d["Auth"], d["GWID"])
```

3. 在 Google Sheets「智能居家」分頁新增一列：
   - 類型填 `除濕機`
   - Device ID 填 GWID
   - Auth 填 Auth 值

> **注意**：Panasonic API 為逆向工程的私有 API，若官方更新可能失效。

---

## 後續維護

**調整 Bot 行為**：修改 `main.py` 裡的 `SYSTEM_PROMPT`，push 後自動重新部署。

**新增功能**：
1. Google Sheets 新增對應分頁
2. 更新 `SYSTEM_PROMPT` 加入新功能描述
3. 在 `main.py` 加入對應的 handle 函數
4. 更新 `/notify` 端點加入新的推播邏輯

**新增 SwitchBot 設備**：在 Sheets「智能居家」新增一列，填入 Device ID 即可。

**新增 Panasonic 設備**：重跑測試腳本取得新設備的 Auth 和 GWID，填入 Sheets。

**費用控管**：Claude API 每月約 NT$10~20，建議在 Anthropic Console 設定 monthly spend limit $5。

---

**開新對話時**：把 `README.md`、`main.py`、`switchbot_api.py`、`panasonic_api.py` 的內容一起貼給 AI，即可完整接手這個專案。