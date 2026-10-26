# 系統導覽

整個家庭系統由哪些部分組成、各自在哪裡跑、資料怎麼流、怎麼部署。**設計原則**（新功能放哪、什麼絕不能做）見 [架構原則](local-hub-architecture.md)；
架構為什麼長成這樣（想用自然語句叫 Siri → FP2 的狀態要回傳 → Aqara 的 API 要實名申請 → 只剩 HomeKit → 需要本地橋接）見 [為什麼是 Home Assistant](why-home-assistant.md)。

本頁描述 2026-10-21 切換後的新系統。設備位址、配對資料與憑證以各部署端設定為準，不寫進公開文件。

## 組成

| 部分 | 在哪裡跑 | 責任 | 入口文件 |
| --- | --- | --- | --- |
| [home-butler](https://github.com/CZLin-TW/home-butler)（HB） | Render（雲端），資料在 Supabase（PostgreSQL） | 家庭資料、排程、LINE 管家、通知、Dashboard 的後端；`homeassistant/` 有 HA 的自訂整合，`agent/` 有電腦監控程式 | [AGENTS](../AGENTS.md)、[重構計畫](refactor-plan.md) |
| [Dashboard](https://github.com/CZLin-TW/Dashboard) | Vercel（雲端） | 家人的操作畫面、LINE 配對登入、到 HB 的唯一通道（白名單） | [AGENTS](https://github.com/CZLin-TW/Dashboard/blob/main/AGENTS.md)、[README](https://github.com/CZLin-TW/Dashboard/blob/main/README.md) |
| Home Assistant（HA） | 家中主機上的虛擬機 | 唯一的設備層：空調、紅外線、感測器、照明、劇院開關；即時自動化；Apple 家庭的 HomeKit 橋接 | [HA 整合](../homeassistant/README.md) |
| [theater-agent](https://github.com/CZLin-TW/theater-agent)（私人） | 家中主機 | AVR／KEF 連動、Bravia 畫面恢復、Apple TV 監控；經 HA 的整合對外 | 該 repo 的 README、AGENTS（需要存取權） |
| 攝影機存在感測（私人） | 家中主機 | 從攝影機畫面判斷各區域有沒有人，經 MQTT 送進 HA | 該 repo 的文件 |
| 電腦監控 | Mac：家中主機上的只讀小服務（`agent/mac_metrics_server.py`）；Windows：預定淘汰的 PC | Mac 由 HA 每分鐘讀取、經 HA 的連線轉給 HB（不帶金鑰）；Windows 每 60 秒用自己的金鑰直接回報 | [mac_metrics_server.py](../agent/mac_metrics_server.py)、[PC agent](../agent/README.md) |

## 資料與控制路徑

```text
家人 ─ 瀏覽器 ─> Dashboard（/api/hb 白名單＋身分簽章）─┐
家人 ─ LINE ──────────────────────────────────────────┼─> HomeButler ─> Supabase
                     Notion、天氣、Claude <─────────────┘      │  ▲
                     除濕機雲端 <── HB 的除濕機隔離模組         │  │ HA 主動連進來的 WebSocket
                                                              ▼  │ （狀態一變就送、每 30 秒心跳）
                                               Home Assistant（唯一的設備層）
                                                ├─ SwitchBot 雲端：空調、紅外線、Hub 2
                                                ├─ Hue、FP2、攝影機存在感測（MQTT）
                                                └─ 劇院（Theater Agent 整合 → theater-agent）
家人 ─ Apple 家庭／Siri ─> HA 的 HomeKit 橋接器
HA 本地自動化 ─> HA 的設備整合
SwitchBot 雲端 webhook ─> 家中主機的 Tailscale Funnel（只放行 HA 的 webhook 路徑）─> HA
家中主機的監控 <─ HA 讀取 ─> HB（附在快照裡）
Windows 電腦監控 ─> HB（只能回報指標；淘汰時移除）
```

- HB 從不直接連設備；HA 斷線、或超過 90 秒沒有新資料，所有設備狀態顯示「未知」，指令一律拒絕。
- Dashboard 關掉不影響任何排程或自動化；它只是畫面。
- 各層「在線」意義不同：HA 連著不代表某台設備正常，電腦有心跳不代表劇院正常。只有實際操作過才算通過。

## 誰呼叫誰、用什麼證明身分

| 呼叫者 | 怎麼證明 | 能做什麼 |
| --- | --- | --- |
| Dashboard | Dashboard 自己的金鑰，加上每個請求一張 60 秒內有效、代表某位成員或某台遙控器的簽章 | 那個人能做的事；遙控器只到裝置頁 |
| LINE | LINE 的簽章 | 管家（只接受啟用中的成員） |
| HA 的 HomeButler 整合 | HA 專用金鑰 | 回報設備與狀態、接收指令 |
| 每台電腦的監控 | 各自一把 | 只能回報指標 |
| 管理 | 管理金鑰 | `GET /v2/system/status`：資料庫結構版本、背景工作狀態、已載入的模組 |

公開、不需要身分的只有 `/` 與 `/v2/health`（給外部監控用，不透露家裡任何資訊）。

## 背景工作（HB，同一個程序裡）

每項工作各自一條執行緒、各自的間隔；一項失敗不影響其他項，跑太久不會重疊，錯過的週期不會一口氣補跑。
**只能有一個程序**：多開一個，每項工作都會跑兩次。

| 工作 | 間隔 | 做什麼 |
| --- | --- | --- |
| `schedules` | 60 秒 | 空調自動關機的本輪判斷，派送到期的排程（送出前先標 claimed，結果不明就停在「待確認」） |
| `devices` | 60 秒 | 讓設備清單與 HA 一致 |
| `light_reminders` | 60 秒 | 待辦的燈光提醒 |
| `history` | 5 分鐘 | 記錄感測器與空調的歷史 |
| `todo_reminders`／`todo_recurring` | 5 分鐘 | 週期待辦產生下一筆、到期提醒 |
| `notion` | 5 分鐘 | Notion 待辦同步 |
| `daily_summary` | 5 分鐘檢查 | 每日推播（每天一次） |
| `backup` | 5 分鐘檢查 | 每日備份到雲端硬碟（要設 `BACKUP_FOLDER` 才做） |
| `health` | 5 分鐘 | 失聯告警（電腦、劇院、備份、HA 超過 15 分鐘沒連上），只在狀態翻轉時推播 |
| `config` | 60 秒（`CONFIG_CHECK_SECONDS`） | 試算表三張手動設定分頁有被改過才同步進資料庫 |
| `dehumidifiers` | 5 分鐘 | 除濕機模組：讀狀態、跑自動規則 |
| `prune` | 1 小時 | 清掉超過 90 天的管家對話紀錄 |

Dashboard 的更新頻率：裝置頁開著時每 7 秒讀一次，圖表歷史每 5 分鐘；首頁沒展開設備時每 30 秒。

## 部署與退回

| 推上 `main` | 會發生什麼 |
| --- | --- |
| home-butler | CI 通過後 Render 部署（文件、`agent/`、`homeassistant/`、`tests/` 不觸發）；啟動時自動套用資料庫結構版本。HA 整合要手動安裝；Mac 監控程式只在它的檔案有變動時，由它自己的更新程式在 CI 通過後安裝 |
| Dashboard | Vercel 先跑 lint 與測試，過了就部署，改文件也會部署 |
| theater-agent | 家中主機約五分鐘內自動拉下、驗證、重啟；只改文件時只更新檔案 |

- 有 API 或資料格式相依時**先部署 HB、再部署 Dashboard**；退回時反過來，先退 Dashboard。
- 系統版本以 Dashboard 的 `package.json` 為準，LINE 管家顯示的版本也從那裡讀。
- 一般程式退回用 `git revert` 產生新提交，照正常流程部署。
- **已經套用到正式資料庫的遷移版本檔不能拿掉。**資料庫停在程式不認得的版本時，啟動會直接失敗（`Can't locate revision`，2026-10-22 實測）。
  退回含遷移的提交時保留那個版本檔，或另加一個新版本把結構改回去；結構只往前加。
- 推送成功、CI 通過、實際執行的版本是三件事，要分開確認。

## 新的工作階段先讀什麼

1. 目標 repo 的 `AGENTS.md`，再依本頁找相依的部分。不要依賴舊對話，也不要假設本機有服務在跑。
2. 畫面的工作用 Dashboard 的測試模式（模擬家庭）；後端用 `venv/bin/python -m homebutler.tests.run`（假的 HA 與雲端服務）。**不用家裡的實體家電測試。**
3. 改了 API 或資料格式，同步改路由、型別、測試模式的模擬資料與測試；改了行為，同步改各 repo 文件原本的段落與驗證紀錄。
4. 回報時把靜態檢查、離線測試、測試模式畫面、部署觀察、實機驗證分開寫；只有實際做過的才能說通過。

驗證紀錄：[home-butler](verification.md)、[Dashboard](https://github.com/CZLin-TW/Dashboard/blob/main/docs/verification.md)、[theater-agent](https://github.com/CZLin-TW/theater-agent/blob/main/docs/verification.md)（私人）。

## 舊系統的文件

下面幾份描述的是 2026-10-21 切換前的系統（試算表當資料庫、Siri 捷徑、homebridge、逐台遷移的開關），只作為歷史參考，與本頁或 [AGENTS](../AGENTS.md) 矛盾時以後者為準：
[舊系統說明](legacy-agents.md)、[後端參考](backend-guide.md)、[版本選擇](version-selection.md)、[HA 遷移盤點](ha-migration-audit.md)、[README](../Readme.md) 的大部分內容。
