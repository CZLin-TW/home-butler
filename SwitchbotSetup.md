# SwitchBot 智能居家整合指南

## 概述

在現有的家庭 AI 管家系統上新增 SwitchBot 智能居家控制功能，支援：
- **冷氣控制**（透過 SwitchBot Hub IR）：開關、溫度、模式、風速
- **溫濕度查詢**（SwitchBot Hub 內建感應器）：即時讀取
- **DIY IR 設備**（電風扇、喇叭等）：開關 + 自訂按鈕
- **每日推播**：自動在早上推送室內溫濕度

---

## 設定步驟

### 1. 取得 SwitchBot API Token 和 Secret

打開 SwitchBot App：
1. 點右下角「個人」
2. 進入「偏好設定」→「關於」
3. 連點「App 版本」10 次，會出現「開發者選項」
4. 進入「開發者選項」
5. 複製 **Token** 和 **Secret Key**

### 2. 在 Render.com 新增環境變數

到 Render.com 的服務設定 → Environment，新增兩個變數：

| 變數名稱 | 說明 |
|----------|------|
| SWITCHBOT_TOKEN | SwitchBot App 取得的 Token |
| SWITCHBOT_SECRET | SwitchBot App 取得的 Secret Key |

### 3. 在 Google Sheets 新增「智能居家」分頁

在你的「家庭管家」試算表中，新增一個分頁命名為 **智能居家**，第一行標題列如下：

| 名稱 | 類型 | 位置 | Device ID | 狀態 | 按鈕 |
|------|------|------|-----------|------|------|

欄位說明：
- **名稱**：自訂的友善名稱（例如「客廳冷氣」、「Hub 2」、「電風扇」）
- **類型**：`冷氣`、`感應器` 或 `IR`
- **位置**：放置位置（例如「客廳」、「臥室」）
- **Device ID**：SwitchBot 的設備 ID（見下一步取得）
- **狀態**：`啟用` 或 `停用`
- **按鈕**：僅 IR 設備需要填，逗號分隔（例如「電源,風速+,風速-,擺頭」）

### 4. 取得設備 Device ID

部署更新後，在瀏覽器打開：
```
https://home-butler.onrender.com/switchbot/devices
```

這會回傳你 SwitchBot 帳號下的所有設備及其 Device ID。

找到你的設備，把 Device ID 填入 Google Sheets 的「智能居家」分頁。

**範例：**

| 名稱 | 類型 | 位置 | Device ID | 狀態 | 按鈕 |
|------|------|------|-----------|------|------|
| 客廳冷氣 | 冷氣 | 客廳 | 02-202509241940-42336740 | 啟用 | |
| Hub 2 | 感應器 | 客廳 | E84F3A500B90 | 啟用 | |
| 電風扇 | IR | 客廳 | 02-202509241953-60857229 | 啟用 | 電源,風速+,風速-,擺頭 |

> 注意：
> - 冷氣的 Device ID 通常是 `02-` 開頭（IR 虛擬設備）
> - Hub 的 Device ID 通常是 MAC 地址格式
> - DIY IR 設備也是 `02-` 開頭

### 5. 更新程式碼並部署

把新的 `main.py`、`switchbot_api.py`、`requirements.txt` 放到你的專案資料夾，然後：

```bash
git add .
git commit -m "新增 SwitchBot 智能居家控制"
git push
```

Render 會自動重新部署。

---

## 使用方式

### 冷氣控制
| 你說 | 系統動作 |
|------|---------|
| 「開冷氣」 | 開機，預設 26 度冷氣模式 |
| 「冷氣 24 度」 | 開機，設定 24 度 |
| 「關冷氣」 | 關機 |
| 「冷氣除濕模式」 | 切換到除濕模式 |
| 「冷氣調到 28 度送風」 | 設定 28 度送風模式 |
| 「冷氣風量調大」 | 風速設為高 |

### DIY IR 設備控制（電風扇等）
| 你說 | 系統動作 |
|------|---------|
| 「開電風扇」 | 送出 turnOn 指令 |
| 「關電風扇」 | 送出 turnOff 指令 |
| 「風速大一點」 | 觸發「風速+」自訂按鈕 |
| 「風速小一點」 | 觸發「風速-」自訂按鈕 |

> **重要**：DIY IR 設備的開關使用標準 turnOn/turnOff 指令，不是自訂按鈕。
> 程式會自動判斷：「開」「關」→ turnOn/turnOff，其他按鈕 → customize 模式。

### 溫濕度查詢
| 你說 | 系統動作 |
|------|---------|
| 「現在幾度？」 | 回報溫度和濕度 |
| 「室內溫度多少」 | 回報溫度和濕度 |
| 「濕度高嗎？」 | 回報濕度並給建議 |

### 每日推播
每天早上自動推送的訊息會包含溫濕度資訊，例如：
```
🔴 今天到期：牛奶（2026-03-14）
🟡 3天內到期：優格（2026-03-16）

🌡️ Hub 2：26.5°C / 68%
```

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| `main.py` | FastAPI 主程式（Webhook、Claude 串接、所有 handler、推播邏輯） |
| `switchbot_api.py` | SwitchBot API v1.1 封裝（認證、設備控制、感應器讀取、DIY IR） |
| `requirements.txt` | Python 套件（含 httpx） |
| `render.yaml` | Render.com 部署設定 |

---

## IR 控制技術細節

SwitchBot API 對 IR 設備有兩種控制模式：

| 模式 | commandType | 用途 | 範例 |
|------|-------------|------|------|
| 標準指令 | `command` | 開關機 | turnOn、turnOff |
| 自訂按鈕 | `customize` | App 裡自訂的按鈕 | 風速+、風速-、擺頭 |

`switchbot_api.py` 的 `ir_control()` 函數會自動判斷：
- 按鈕名稱是「開」「關」「電源」等 → 使用標準 turnOn/turnOff
- 其他名稱 → 使用 customize 模式，按鈕名稱必須與 App 完全一致

---

## 測試端點

部署後可用瀏覽器直接測試 SwitchBot API，繞過 Claude：

```
# 測試自訂按鈕（customize 模式）
https://home-butler.onrender.com/switchbot/test/{device_id}/{按鈕名稱}

# 測試標準 turnOn 指令
https://home-butler.onrender.com/switchbot/test_turnon/{device_id}
```

---

## 問題排查

| 問題現象 | 排查方向 |
|---------|---------|
| 冷氣沒反應 | 確認 Device ID 正確、SwitchBot Hub 在線、IR 學習正常 |
| 溫度讀不到 | 確認 Hub 的 Device ID 正確、Cloud Service 已開啟 |
| DIY 設備開關沒反應 | 用 /switchbot/test_turnon 端點測試 turnOn 是否正常 |
| DIY 按鈕沒反應 | 用 /switchbot/test 端點測試，確認按鈕名稱完全一致 |
| 說「開冷氣」但沒動作 | 檢查 Render Log，確認 SwitchBot API 回傳的錯誤訊息 |
| Token 錯誤 | 在 SwitchBot App 重新取得 Token 和 Secret |

---

## 未來擴展

**新增同類設備**（例如第二台冷氣）：只需在 Sheets 新增一行，填入 Device ID。

**新增 DIY IR 設備**（例如喇叭、MOD）：
1. 在 SwitchBot App 學習 IR 按鈕
2. 取得 Device ID（`/switchbot/devices`）
3. 填入 Sheets，類型填 `IR`，按鈕欄填所有可用按鈕名稱

**新增全新設備類型**（例如 SwitchBot Bot 機械手臂、窗簾）：
需要在 `switchbot_api.py` 新增對應的控制函數，並更新 SYSTEM_PROMPT。