# SwitchBot 智能居家整合指南

## 概述

在現有的家庭 AI 管家系統上新增 SwitchBot 智能居家控制功能，支援：
- **冷氣控制**（透過 SwitchBot Hub IR）：開關、溫度、模式、風速
- **溫濕度查詢**（SwitchBot Hub 內建感應器）：即時讀取
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

| 名稱 | 類型 | 位置 | Device ID | 狀態 |
|------|------|------|-----------|------|

欄位說明：
- **名稱**：自訂的友善名稱（例如「客廳冷氣」、「客廳 Hub」）
- **類型**：`冷氣` 或 `感應器`
- **位置**：放置位置（例如「客廳」、「臥室」）
- **Device ID**：SwitchBot 的設備 ID（見下一步取得）
- **狀態**：`啟用` 或 `停用`

### 4. 取得設備 Device ID

部署更新後，你可以直接在 LINE 上對管家說：

> 「列出所有設備」

或者用以下方式手動取得：

在瀏覽器打開（替換你的 Render 網址）：
```
https://你的服務名稱.onrender.com/switchbot/devices
```

這會回傳你 SwitchBot 帳號下的所有設備及其 Device ID。

找到你的 Hub 和冷氣，把 Device ID 填入 Google Sheets 的「智能居家」分頁。

**範例：**

| 名稱 | 類型 | 位置 | Device ID | 狀態 |
|------|------|------|-----------|------|
| 客廳冷氣 | 冷氣 | 客廳 | 02-2024xxxxxxxx-xxxxxxxx | 啟用 |
| 客廳 Hub | 感應器 | 客廳 | ABCDEF123456 | 啟用 |

> 注意：冷氣的 Device ID 通常是 `02-` 開頭（IR 虛擬設備），Hub 的 Device ID 通常是 MAC 地址格式。

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

### 溫濕度查詢
| 你說 | 系統動作 |
|------|---------|
| 「現在幾度？」 | 回報溫度和濕度 |
| 「室內溫度多少」 | 回報溫度和濕度 |
| 「濕度高嗎？」 | 回報濕度並給建議 |

### 每日推播
每天早上自動推送的訊息會多一行溫濕度資訊，例如：
```
🔴 今天到期：牛奶（2026-03-14）
🟡 3天內到期：優格（2026-03-16）

🌡️ 客廳 Hub：26.5°C / 68%
```

---

## 新增的檔案

| 檔案 | 說明 |
|------|------|
| `switchbot_api.py` | SwitchBot API v1.1 封裝（認證、設備控制、感應器讀取） |
| `main.py` | 更新版（新增 control_ac、query_sensor、query_devices action） |
| `requirements.txt` | 新增 httpx 套件 |

## 新增的 Render 環境變數

| 變數名稱 | 說明 |
|----------|------|
| SWITCHBOT_TOKEN | SwitchBot 開發者 Token |
| SWITCHBOT_SECRET | SwitchBot 開發者 Secret Key |

## 新增的 Google Sheets 分頁

**智能居家**

| 名稱 | 類型 | 位置 | Device ID | 狀態 |
|------|------|------|-----------|------|

---

## 問題排查

| 問題現象 | 排查方向 |
|---------|---------|
| 冷氣沒反應 | 確認 Device ID 正確、SwitchBot Hub 在線、IR 學習正常 |
| 溫度讀不到 | 確認 Hub 的 Device ID 正確、Cloud Service 已開啟 |
| 說「開冷氣」但沒動作 | 檢查 Render Log，確認 SwitchBot API 回傳的錯誤訊息 |
| Token 錯誤 | 在 SwitchBot App 重新取得 Token 和 Secret |

## 未來擴展

新增設備只需三步：
1. 在 SwitchBot App 新增設備
2. 取得 Device ID
3. 填入 Google Sheets「智能居家」分頁

如果是新的設備類型（例如燈光、窗簾），需要額外在 `switchbot_api.py` 新增對應的控制函數，並更新 SYSTEM_PROMPT。