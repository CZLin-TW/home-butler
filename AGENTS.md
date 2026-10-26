# HomeButler 維護入口

這份是**現況**與**不可違反的約束**。HomeButler 在 2026-10 整個重寫（分支 `v2`，`homebutler/` 套件）；
設計與每一項決定的理由在 [重構計畫](docs/refactor-plan.md)，切換步驟在 [切換清單](docs/cutover.md)。
2026-10-21 已切換上線（`main` 就是新系統）。重寫前的系統說明保留在 [舊系統說明](docs/legacy-agents.md)，歷次版本的決定在 [版本變更紀錄](docs/agents-history.md)；
那兩份描述的是舊程式，遇到矛盾以本檔與重構計畫為準。

**加新行為時改本檔對應的段落，不要在開頭追加新章節。**動手簡化架構之前先讀
[為什麼是 Home Assistant](docs/why-home-assistant.md)——現在的形狀是一串外部限制推出來的。
新功能放哪、元件怎麼連的原則在 [架構原則](docs/local-hub-architecture.md)；各部分在哪裡跑、怎麼部署在 [系統導覽](docs/system-overview.md)。

## 分層

- **HA 是唯一的設備層。**空調、紅外線、感測器、照明、劇院都經 HA；HB 自己不直接連任何設備。
  例外只有除濕機（`homebutler/dehumidifier/`，隔離模組，之後整個移除）。
  核心沒有 import 它，但有幾處直接寫著除濕機，移除模組時要一起改（2026-10-23 盤點，因為要淘汰所以先不整理）：
  `assistant/` 的提示詞（三個除濕機動作與用法規則；改提示詞要先問使用者）、`actions.py`（排程種類、`_missing_module`）、`pipeline.py`（`REALTIME`）、`context.py`（種類名稱）；
  `integrations/sheets_config/sync.py`（設備種類）、`jobs/backup.py`（略過的歷史表）、`api/devices_page.py`（裝置頁與歷史圖）；
  Dashboard 的 `src/lib/dehumidifier.ts`、`hb-rules.ts` 的四條規則與裝置頁的卡片。
  模組自己的歷史查詢（`service.py` 的 `history`）只用時間條件、走不到 `(name, t)` 索引；表很小，不修。
- **HB 只當大腦**：家庭資料、排程、LINE 管家、通知、Dashboard 的後端，以及 Notion、天氣、Claude 這類資料服務。
- **分工原則不變**：即時反應歸 HA；家人要事先知道、能調整的排程歸 HB 並在 Dashboard 顯示。
  同一條規則只能有一個控制主體，不要兩邊各做一份。

## 程式在哪

```
homebutler/
  settings.py        所有環境變數，啟動時讀一次；值不對就停並說出變數名（不說值）
  store/             SQLAlchemy 資料表（models.py）與 Alembic 版本（migrations/）；PostgreSQL 用 schema hb，每張表開 RLS
  domain/            規則本身（待辦、食品、設備、排程與自動關機、成員、金鑰、配對、對話）
  integrations/      外部服務（HA 連線、LINE、試算表設定同步、Notion、天氣）
  assistant/         LINE 管家：提示詞（逐字沿用舊版，改之前先問使用者）、動作、回覆規則
  api/               路由：Dashboard 的 /v2、HA 的 WebSocket、LINE webhook、監控回報
  jobs/              背景工作（排程、提醒、Notion、每日推播、告警、備份、設定同步）
  dehumidifier/      除濕機隔離模組；modules.py 列出可選模組，核心不直接 import 它們
  migration.py       從舊系統搬資料（切換時一次）
main.py              進入點（Render 執行 uvicorn main:app）
```

## 不可違反的約束

- **未知結果不自動重送。**送出後沒拿到明確結果就是「待確認」，留給人檢查設備；重啟後也不補送。
  排程送出前先在資料庫標成 claimed，所以不會送兩次。
- **不 fallback 到另一條路徑。**HA 斷線、設定不符就拒絕或等，不改走雲端或直接 IR。
  HA 斷線時一切設備狀態都是「未知」，不是「關」。
- **單一程序、單一 worker。**背景工作在這個程序裡跑；多 worker 會讓每個工作跑兩次。
- **每張表開 RLS**（遷移版本裡做）。新增資料表時照做。
- **已套用到正式資料庫的遷移版本檔不能刪。**資料庫停在程式不認得的版本時啟動會失敗（`Can't locate revision`）。
  退回含遷移的提交時保留版本檔，或另加新版本把結構改回去。
- **schema 用連線後的 `SET search_path` 設定**（`store/db.py`），不要改回連線參數 `options`：Supabase 的連線中繼站會悄悄丟掉它，表就會建到 `public`、撞上舊系統的同名表（2026-10-21 切換時實際發生）。
- **金鑰一個用途一把**（Dashboard、HA、每台電腦、管理），值放在環境變數，啟動時登記；換掉一把，舊的自動失效。
  Dashboard 代表某個人呼叫時，另帶一張 5 分鐘內有效的身分簽章。
- **公開 repo**：不放家裡的網路位址、對外網址、機器名稱、帳號識別或真實的區域名稱；
  測試用中性名稱（冷氣一、感測一、房間一）。這類值只放在環境變數或不進版本控制的地方。
- **不新增試算表欄位。**試算表只剩三張手動維護的分頁（家庭成員、遙控器、智能居家），由背景同步進資料庫；
  程式寫的值（管家風格、照明區域、規則進度……）都在資料庫。
- **LINE 推播額度**：免費方案每月 200 則。告警只在狀態翻轉時推播；不要加「每次檢查都推」的東西。
- **空調目標是整數 16–30°C**；半度輸入四捨五入，其餘小數拒絕。
- **禁止用家裡的實體家電做測試。**測試用假的 HA、假的雲端服務；試跑環境不送排程、不發 LINE。

## 推上 `main` 會觸發哪些事

| 對象 | 什麼變動會觸發 |
| --- | --- |
| Render 後端 | 後端程式與相依檔（Render 後台的 Build Filters 排除了文件、`agent/`、`homeassistant/`、`tests/` 等）。Auto-Deploy 設為 CI 通過後才部署 |
| Mac mini 監控程式 | 舊的 `agent/macos_daemon/` 自動更新只看 `auto_update/update.py` 的 `FILES` 清單（2026-10-27 起改由 HA 讀取，舊機制停用後移除）。新的 `agent/mac_metrics_server.py` 不會自動安裝，由對話在家中主機手動套用 |
| Windows 代理程式 | 任何變動都可能讓它重啟一次（那台預定淘汰） |
| HA 自訂整合 | 不會自動安裝，要手動裝並重啟 HA |

## 測試

- 新系統：`venv/bin/python -m homebutler.tests.run`（本機用 SQLite；CI 另用真的 PostgreSQL 跑同一套）。
- HA 整合：CI 的 HA job（`homeassistant/`，pytest，HA 版本與家裡相同）。
- 舊系統的測試仍在 `tests/`，切換完成並收尾後移除。
- Mac 監控程式：`python agent/macos_daemon/build.py --config agent/macos_daemon/config.example.json --output <新資料夾> --tests-only`。

## 試跑

Mac mini 上的試跑環境在 `~/Services/hb-trial/`（不在 repo）：讀真實的試算表與舊歷史（唯讀，憑證在鑰匙圈），
資料放本機 SQLite，`SCHEDULES=0`、不接 LINE、不備份、不連 HA。用法寫在那裡的 `run.sh`。

## Git push 環境差異

`git push` 認證失敗時立刻停下：不設 credential helper、不改 remote、不用 API 繞過，回報即可。
