# HomeButler 維護入口

這份檔案是**現況**與**不可違反的約束**。歷史版本的逐版決定移到
[版本變更紀錄](docs/agents-history.md)，那裡按時間保留原始描述，不隨新版改寫。

**加新行為時請修正本檔的現況章節與 docs 的原段落，不要在開頭追加新的版本章節**——
那正是過去讓同一件事出現兩種說法的原因。變更內容另外寫進版本變更紀錄與
[驗證紀錄](docs/verification.md)。

先讀 [README](Readme.md)、[系統導覽](docs/system-overview.md) 與 [驗證紀錄](docs/verification.md)。
**動手簡化架構之前先讀 [為什麼是 Home Assistant](docs/why-home-assistant.md)**——
現在的形狀是一串外部限制推出來的，那些限制不在程式碼裡。
背景週期以 `main.py` 的 `jobs.add` 為準，設備控制權以 Render 環境變數與 HA 實際選取為準。

## 目前控制權（2026-09-14 / 系統 v1.57.0）

下表是**控制權歸屬，不是實機驗收狀態**；逐台逐模式的驗收缺口見
[驗證紀錄](docs/verification.md) 與 [HA 遷移盤點](docs/ha-migration-audit.md)。改行為時連這張表一起改。

| 範圍 | 目前由誰控制 | 決定權的旗標／來源 |
| --- | --- | --- |
| 三台空調 | HA（原生 SwitchBot Cloud climate） | `HOME_ASSISTANT_AC_NAMES`；整數溫度、無回饋補償、無防黴 |
| 空調排程 | HB 保管，經 HA 下達 | 手動「使用者（HA）」、自動關機「自動（HA）」＋Sheet「自動關機小時數」 |
| 三台 IR 電扇 | HA 本機 button | `HOME_ASSISTANT_IR_NAMES`；只有電源／風速±，無實體回讀 |
| 溫濕度／CO₂／Hub 光照 | HA 為即時來源，HB 每 300 秒採樣留歷史 | `HOME_ASSISTANT_SENSOR_NAMES`；Hub 光照是 1–20 級，不是 lux |
| Hue 照明（含色溫／HSV） | HA（需 home_butler 1.5.0 宣告 `color_control`） | `HOME_ASSISTANT_HUE_ENABLED`；PC agent Hue 僅備援 |
| FP2 存在／光照 | HA → HB 記憶體快照 → Dashboard | 斷線或超過 90 秒即未知；無雲端備援（Aqara API 需實名制，已放棄） |
| 除濕機 | **完整留在 HB**（Panasonic／LG 直連） | 使用者 2026-09-14 決定不遷移，新家改中央除濕 |
| 自動夜燈規則 | 已退役（v1.53.0） | 舊 Sheet 保留不執行，規則寫入回 410 |
| 劇院／PC 指標 | theater-agent／PC agent，未遷移 | HA 日後最多當中繼，不重寫同一套連動 |
| Apple Home | HA HomeKit Bridge | `homebridge/` 插件降為歷史相容，不列入驗收 |

## HB 與 HA 的分工原則

- **即時互動與連續條件判斷歸 HA**：存在感測、夜燈、日出、亮度觸發、設備冷卻等待。
  這些要在本地毫秒級反應，不該繞 Render。
- **需要在 Dashboard 查看、記住、修改的一次性排程歸 HB**：使用者要看得到、改得動、
  刪得掉的東西留在 Sheet，由 HB 每 60 秒派送、經 HA 下達。
- 固定時段依上面兩條判斷，**不是一律歸 HA**：純本地即時反應寫在 HA；
  需要家人查看或調整的做成 HB 排程。
- 每台設備、每條規則同一時間只能有一個控制主體。禁止 HB → HA → HB 的循環命令路徑，
  禁止同一條自動化在兩邊同時執行。
- Dashboard 與任何語音入口都**不能編輯 HA 自動化**，也不要另外長出第二套排程引擎。

## 排程來源（三種，互相誤刪就是靜默失效）

| 來源 | 誰建立 | 適用設備 | 使用者可否編輯 |
| --- | --- | --- | --- |
| 使用者 | Dashboard／LINE／完整 Siri | 一般設備與未遷移空調 | 可 |
| 使用者（HA） | 同上，對 HA 空調（v1.55.0 起） | HA 管理的空調 | 可 |
| 自動（HA） | `ac_auto_off.reconcile`，依 Sheet 時數 | HA 管理的空調 | 可改時間／參數或刪除，刪除後本輪不補回 |

舊的「自動」（`maintain_ac_auto_schedule`，只服務未遷移空調）與「防黴」來源已移除；
Sheet 上若還有這兩種舊列，到期會照一般規則標成已過期，不會被執行。

`schedule_execution.py` 派送前會重驗來源與該設備目前的 provider，不一致直接取消，
**不能 fallback 成直接 IR**。未知／失敗結果一律不重送，也不能靠編輯重新排入。
完整語意見 [自動關機與排程來源](docs/ac-auto-off.md)。

## 權限邊界（四把獨立金鑰，不可互通）

| 金鑰 | 入口 | 能做什麼 |
| --- | --- | --- |
| `HOME_BUTLER_API_KEY` | 完整 `/api/assistant`、Dashboard BFF | 家庭全功能，含建立／修改排程。`user_id` 只決定對話身分，不是降權機制 |
| `DEVICE_VOICE_API_KEY` | 家電專用 `/api/assistant/devices` | 只有 `device_voice.ALLOWED_ARGS` 的控制與查詢。**沒有任何 schedule 動作，這是刻意的限制** |
| `HOMEBRIDGE_API_KEY` | `homebridge_api.py` | 只有 `HOMEBRIDGE_DEVICE_NAMES` 內的空調 |
| `HOME_ASSISTANT_API_KEY` | HA 主動連回的 WSS | 只有選定觀測與明確允許的設備動作 |

**絕不可把後三把加進 `verify_api_key`，或讓它們進入完整 assistant pipeline。**
家電專用入口不讀家庭／待辦／食品／對話，不接受 `user_id` 覆寫；新增能力要同步
專用 schema、驗證、handler 白名單、README 與 `tests/test_device_voice.py`。
完整 prompt 的動作擴充**不會**自動授權家電捷徑。

## 背景工作（以 `main.py:jobs.add` 為準）

- **每 60 秒**：`schedules`（設備排程／封存，也是 HA 空調自動關機 reconcile 的入口）、
  `lighting-reminders`（HA Hue 待辦燈光提醒，僅 HA Hue 啟用時執行）
- **每 300 秒**：`sensors`（一般感測器／歷史）、`notion`、`todo-reminders`、
  `daily-push`、`agent-health`

v1.53.0 移除夜燈引擎後**已無每 300 秒的照明工作**。細節與歷史事故見下方「排程 / 推播架構」。

## 不可違反的約束

這些是歷次版本累積下來、到現在仍然成立的硬規則。放這裡是因為它們原本散在各版本章節裡，
搬進歷史紀錄後就沒人看得到了。

- **未知結果不自動重送。** 送出後沒拿到明確結果就維持待確認，讓使用者先檢查設備。
  重啟後也不補送。這條適用所有設備通道。
- **不 fallback 到另一條路徑。** HA 失聯、設定錯誤、provider 不一致時一律拒絕或取消，
  不可改走直接 IR 或雲端。感測器同理：HA 失聯就是未知，不回頭讀雲端。
- **不新增 Sheet 欄位或 HA 自動化**去實作 HB 的功能；HA 組件版本是明確相依，不默默忽略請求。
- **1–20 光照等級不是 lux**，不可標成 lux 或匯入既有 illuminance 通道。
- **無簽章 payload 不能寫進 HA 狀態**；值只能來自原生 authenticated refresh。
- **不可把 Homebridge 匯入的實體再導回 HB** 控制，那會繞成迴圈。
- **禁止用家裡的實體家電做測試。** 離線測試用假 Sheets／SDK；demo 用模擬家庭。
- **後端維持單一 process／單 worker。** RLock、工作排程與記憶體快取都不是跨主機鎖，
  Sheets 也沒有多步交易。要多 worker 必須先抽出唯一 scheduler／writer 並換成可交易的儲存。
- **色彩／色溫不附帶開機**，白光與彩色不同時下發，整批預驗證後才寫入。
- **空調目標是整數 16–30°C。** 語音的半度輸入 half-up 收整，其餘小數拒絕；
  半度舒適目標與室溫回饋補償已於 v1.58.0 移除，不要重建。
- **除濕機留在 HB**，不列入 HA 遷移待辦（使用者 2026-09-14 決定）。

## 既有維護規則

IR 名稱修正見 `device_name_resolution.py` 與 `tests/test_ir_names.py`：完整名稱優先，僅等價化結尾「電風扇／電扇」，保留房間；歧義不送出。只有省略名稱時可用單一設備 fallback，明確錯誤名稱不可改控另一台。Siri 漏字與後端名稱解析分開驗證，勿由裸設備名稱自動補上開／關。

Siri 精簡回覆：`/api/assistant` 呼叫 `process_message(..., voice=True)` 後經 `voice_reply.format_voice_reply`，請保持 `{reply}` 契約。純設備控制用實際 handler 結果；錯誤／未知／部分成功／追問不可為縮短而刪除。LINE 不啟用 voice。格式整理不可改掉溫度、負號、百分比與時間，不新增 LLM 呼叫；測試見 `tests/test_voice_reply.py`。

家電專用語音：`device_voice_api.py` 的 `/api/assistant/devices` 只接受獨立 `DEVICE_VOICE_API_KEY`。**不得把此金鑰加入 `verify_api_key` 或讓它進入完整 assistant pipeline**。模型僅見設備目錄投影，不讀家庭／待辦／食品／對話，不接受 `user_id`；先驗證整批白名單動作及參數才執行。未遷移空調的防黴／舊來源自動關機仍是控制指令的副作用（HA 空調不執行防黴），除濕機不能繞過自動鎖。新增能力必須同步專用 schema、驗證、handler 白名單、README 及 `tests/test_device_voice.py`；完整 prompt 的動作擴充不會自動授權家電捷徑。

# 版本管理

系統版本不在 home-butler 管。Source of truth 是 **Dashboard 的 `package.json:version`**，本 repo 透過 `config.py:get_app_version()` 在 runtime 撈 Dashboard `/api/version`（1 小時 cache，失敗 fallback「未知」），由 `prompt.py` 注入 `SYSTEM_PROMPT`，讓 LINE bot 能回答「目前版本是？」之類的問題。

需要設定環境變數 `DASHBOARD_URL`（Dashboard 部署網址）才撈得到；沒設或撈不到就會回「未知」，但 bot 仍能正常運作。

**bump 時機**：使用者**體感得到**的變化才 bump（新功能、UI/行為改動、會被察覺的 bug fix）。純 refactor、註解、文件、type 整理**不 bump**。

**bump 流程**：只動 Dashboard `package.json:version` + push 一次。**home-butler 完全不用 push 也不用改任何檔案**。最壞情況是 cache 還沒過期、LINE 回答的版本舊一陣子，1 小時內自動同步。

本專案不使用 git tag / GitHub Releases；版本以 Dashboard `package.json` 為準，git history 自己就是版本軌跡。

# 意圖解析（Claude）：thinking + 強制 JSON schema

`conversation.ask_claude`（LINE / Siri 共用的意圖解析）跑 Sonnet 5 + **adaptive thinking**，
輸出正確性不靠模型自律，靠 **structured outputs**（`output_config.format` constrained
decoding）：曾發生 thinking 開著時模型改吐自然語言取代 JSON（甚至空回應）→ 指令沒執行
卻「講得像做了」。強制 schema 後這個失敗模式在 API 層就不可能發生。

**schema 是 key/value 陣列文法，不是自然的扁平物件**：structured outputs 有 grammar
編譯限制（**全 schema optional 參數 ≤24、union 型別 ≤16**），本系統 40+ 參數的扁平
schema 實測直接 400（55 個 optional 被拒，bot 全掛）。改成每個 action 帶
`args: [{key, value}]`（key 走 enum、value 一律字串），所有欄位 required → 0 optional、
0 union，參數再多也不撞牆。字串 value 由 `assistant.py:_coerce_arg` 依
`prompt.py:ARG_KEY_TYPES` 還原型別；add/modify_schedule 平鋪的指令參數由
`_flatten_action` 收回 params / params_new。

**維護鐵則**（additionalProperties=False → 沒列的東西模型永遠發不出來、且靜默失效）：
- 新增參數：`ARG_KEY_TYPES` 加一筆（schema 的 key enum 自動跟著長）＋ SYSTEM_PROMPT 描述
- 新增 action：`ACTION_NAMES` ＋ `assistant.ACTION_HANDLERS` ＋ SYSTEM_PROMPT
- schema 若再被 API 拒（400）：ask_claude 自動降級成「無 schema + 關思考」，bot 不斷線
  但失去強制 JSON 保證——log 看到「structured outputs 被 API 拒絕」就要回頭修 schema

另外兩顆 Claude 呼叫（`ask_claude_semantic`、`generate_notify_message`）輸出是給人看的
自然語言，維持 adaptive thinking、不上 schema。`requirements.txt` 的 `anthropic==0.107.1`
已支援 `output_config.format`／adaptive（升級 SDK 前先確認仍支援）。

# 排程 / 推播架構（in-process scheduler；GAS 已退場）

頻率見上方「背景工作」，以 `main.py` 的 `jobs.add` 為準。不需要外部 cron；每項不重疊、按固定期限運行，錯過週期不密集補跑。

- **工作隔離**：`run_schedule_tick` 執行設備排程與封存；`run_todo_tick` 生成週期待辦與提醒；Notion 獨立同步。`run_realtime_tick` 僅保留相容入口，正式背景執行不串在一起。
- **每日綜合推播**：`notify.run_daily_push_if_due(ctx)`——每天過了 `DAILY_PUSH_HOUR`（env，預設 21 點）後第一個 tick 觸發一次。去重 marker 存在 Sheet「系統狀態」分頁的 `最後每日推播日期`（跨 Render 重啟存活，不重發不漏發；睡整晚跨午夜才醒則當天不補）。
- `/notify`、`/notify_realtime` 端點**保留**但只當手動觸發（debug / 補發）；不再有外部 cron 打它們。手動 `/notify` 不檢查也不更新每日 marker。

**startup 不做任何 Sheets 工作**：backfill（四張歷史表）／`load_rules`／`ensure_columns`
集中在 `main.py:_warm_up()`，由 sensors 工作在背景首次執行，**不是** startup handler；其他工作可獨立啟動。
理由：FastAPI 的 sync startup handler 是直接 `handler()` 呼叫（不丟 threadpool），而 uvicorn
在 ASGI lifespan startup 完成前不服務任何請求——四張歷史表整張 `get_all_records()` 在冷啟動的
free instance 上要好幾秒到數十秒，那段期間**全站 5xx**。實際咬過（2026-08-28）：一次部署重啟
就讓 Dashboard 的裝置配對登入整段失敗，症狀是「LINE 回授權成功、但網頁一直轉」——前端輪詢
`/api/auth/device/status` 連續吃到 5xx，而空窗撐過 `CODE_TTL`（5 分）配對碼還會直接過期。
搬走之後 startup 幾乎立刻返回，代價只有部署後前幾秒 Dashboard 歷史圖是空的（登入 / LINE bot /
設備控制都不碰那些 ring buffer）。順帶修掉另一個雷：**原本任一個 backfill 拋例外會讓整個 app
起不來**，現在每步各自 try/except，失敗的下個 tick 自動重試。

**為什麼能拿掉 GAS**：這些工作全是 Sheet-anchored / 冪等（觸發時間、狀態、marker 都在 Sheet），重啟後 thread 讀同一份 Sheet 就能補上，不依賴外部時鐘的精準或存活（code 本就容忍漂移：`is_near_hour` ±5 分、排程 2h 過期窗）。GAS 當年的唯一價值是「喚醒睡著的 Render ＋幹活綁同一個 HTTP beat」，但 thread 要能跑的前提（實例醒著）本來就由 UptimeRobot 扛——GAS 的保溫只是跟它**重複**。

**UptimeRobot 是 load-bearing 保溫，不是普通監控**：每 5 分 ping `/` 防止 Render idle-sleep（Readme 標「防冷啟動」）。拿掉 GAS 後，「保持實例醒著、讓 polling thread 不被凍住」這件事**完全靠它**。所以**別把 UptimeRobot 當可有可無的監控隨手關掉**——關了它，排程與推播會跟著 Render 一起睡死。

唯二的記憶體計時（除濕機去抖 `above_since`/`below_since`、照明 `window_active` 邊緣）本來就在這條 thread 上、且自我修正，重啟最多晚一個去抖窗，無資料損失。

**切換注意**：部署後要去 Google Apps Script 把舊的兩條觸發（`/notify` 日計時器、`/notify_realtime` 15 分計時器）**刪除或停用**，否則跟 thread 雙跑。重疊期短且工作冪等，無害，但別長期掛著。

# Agent 失聯告警（`health_alert.py`）

`pc_state` 一直算得出每台 PC 的 `online`，但那個值**只餵給 Dashboard 畫灰點**——
沒人盯著 Dashboard 的時候等於沒有監控。2026-07-19 兩台 agent 的 self-restart 孤兒
hard-crash 就是這樣**躺了三天**沒人知道（Task Scheduler 早已 exit(0) 回 `Ready`
不再觸發）。`health_alert` 把那條線接到 LINE，由 `main.py` 的獨立 `agent-health` 工作每 300 秒呼叫。

**這個模組只觀察、不控制任何設備**，副作用只有 LINE 推播 + 「系統狀態」KV 的 marker。

兩項檢查，各自 try/except 隔離：

| 檢查 | 判定 | 門檻 |
|---|---|---|
| PC agent | `pc_state.snapshot()` 的 `last_heartbeat_at` 有多久沒動 | `AGENT_OFFLINE_ALERT_SECONDS`（env，預設 900 秒；下限鎖 300） |
| 劇院 agent | 打 `theater.summary`（走 agent WS 中繼）連續失敗 | `THEATER_FAIL_STREAK = 3` 個 tick（約 15 分） |

**最容易改壞的幾點**：

- **只在狀態翻轉時推播**。LINE 免費方案每月 200 則推播額度，tick 是 5 分一次——拿掉
  去重的話一台機器離線一天就燒掉 288 則，額度當天見底，**待辦提醒會跟著發不出去**。
- **marker 存 Sheet（`系統狀態` KV），值是 `ok` / `bad:<第一次偵測到的 epoch>`**。存
  Sheet 是為了跨 Render 重啟；存 epoch 是為了恢復時算得出「失聯多久」（7/19 那次的重點
  正是「三天」這個數字）。in-memory 的 `_marker_cache` 只是讀取快取，讓穩定狀態下每個
  tick 完全不打 Sheet。
- **`_report` 的 `now` 必須跟呼叫端同一顆時鐘**。marker 存的是絕對時間，兩邊時鐘不一致
  的話恢復訊息的時長會是錯的（開發時實際踩過：`_report` 內部自己讀 `time.time()`、
  caller 用注入的 `now`，算出來永遠是 0 分鐘）。
- **告警門檻刻意比 `pc_state.OFFLINE_THRESHOLD_S`（5 分）寬**。Dashboard 畫灰點寧可靈敏，
  推播寧可遲鈍：agent 的 auto-update self-restart、網路抖動、Render 重啟後 backfill 還沒
  跑完，都可能造成幾分鐘空窗，5 分鐘門檻會誤報。
- **marker 寫在推播成功之後**。全數推播失敗時不寫，下個 tick 重試——標記成已通知卻其實
  沒送出去，就是又製造一次靜默失效。
- **劇院檢查有兩道前置**：`_loop` 沒就緒（startup 未完成）直接跳過、不算失敗；那台 PC 的
  butler agent 不在線也跳過——整台失聯時 PC 那條已經報過了，再補一則「劇院沒回應」只是
  噪音，而且把因果講反。
- **收件人**：「家庭成員」分頁的 `系統告警` 欄勾 TRUE 的啟用成員（欄位由 `main.py:_warm_up` 背景呼叫
  `ensure_columns` 自動補）。**沒人勾就退回全部啟用成員**——這是刻意的 fail-loud，因為這個
  功能存在的唯一理由就是「不要靜悄悄地沒人知道」，設定沒做就靜音等於把要修的洞原封不動
  搬進新程式碼。
- **推播不寫進「對話暫存」**（待辦提醒會寫，是因為它拿對話內容做去重）——維運告警灌進對話
  會污染 Claude 的上下文。
- **已知限制**：身分認 IP（與 `pc_state` 的 keying 一致）。DHCP 換 IP 會讓舊 IP 看起來永遠
  離線、報一次假警。家裡兩台 IP 固定，接受。另外從沒 push 過 heartbeat 的機器不在
  `pc_state` 裡，這裡也看不到——刻意不維護「應該有哪些機器」的清單，那會是第二處要同步的
  設定，忘了更新一樣是靜默失效。

# Notion 待辦：完成的記號蓋在 Sheet，主鍵是 Notion page id

公司的 Notion 只能讀（`notion_api.py` 沒有任何寫入函式），所以「完成」是在本地蓋章：
`handle_delete_todo` 對 `來源=Notion` 或 `屬性=唯讀` 的列**只改狀態成「已完成」、把列留著**。那列是給
`sync_external_events` 看的記號——下一輪 sync 收集這些列的 **Notion page id**（存在
待辦事項分頁的「外部ID」欄），從 Notion 拉到同一個 id 就跳過不寫回，任務因此不會復活。

**sync 使用差異更新**：`notion_reconcile.plan_changes` 以外部ID與成員定位，只更新 Notion 管理欄位、增刪有差異的列；不變時零資料寫入，保留待辦ID、燈光提醒與其他本地欄位。成功查詢後已不符合篩選的項目會移除；查詢失敗的成員完全不動。

## 為什麼主鍵一定要是 page id（2026-08-31 查了一整天的 bug）

原本的 key 是 `(事項, 日期, 時間)` 三元組，任一格對不上就會**同時**觸發兩件事：
步驟3 的 skip 失效（任務被重新寫成待辦）＋ 步驟4 判定「Notion 上已不存在」把已完成
那列刪掉。使用者體感是「明明說了完成、五分鐘後又變回待辦、逾時提醒每小時繼續響」。
在 Notion 改個標題就會踩到——舊列的名字對不上新拉回來的名字，記號當場報銷。
page id 在改標題／改日期／改時間之後都不變，是唯一穩定的識別碼。

沒有外部ID的舊列使用三元組比對，匹配成功便補外部ID，包含完成記號。比不到的舊資料仍受既有完成去重與成功成員範圍保護。

## 幾個非顯而易見、改壞就會靜默失效的點

- **`get_upcoming_events` 回 `None` 是「查不到」、回 `[]` 是「Notion 上真的沒有」**，
  兩者絕不能混為一談。舊版一律回 `[]`，於是一次 timeout／429／分頁中任一頁失敗，就會
  讓 `new_event_keys` 變空 → 步驟4 把該成員**所有**完成記號當過期刪光 → 下一輪任務
  全數復活。現在 `_fetch_members` 只收查詢成功的成員，失敗的那位**整輪不動他任何一列**
  （fail-closed：寧可留著陳舊資料，也不要誤刪使用者的完成記錄）。
- **完成記號要無條件收集，但只對「這輪查成功的成員」動列**。記號漏收會造成重複寫入
  同一筆任務；動到沒查成功的成員的列就是上一條那個雷。
- **列號一律用即時 `sheet.get_all_values()` 定位，不信任 ctx 快取的 index**。背景 tick
  與 LINE 請求並行改同一張表，快取列號過期就會刪到別人的列。`handle_delete_todo` 早在
  `297599f` 就改成即時定位，`calendar_sync` 是後來才補上的漏網之魚。sync 結束時用同一份
  即時內容重建 ctx 快取（走 `sheets._parse_sheet_values`，型別才會跟 `ctx.load()` 一致）。
- **`append_rows` 要帶 `insert_data_option="INSERT_ROWS"`**。預設的 OVERWRITE 會先找
  「表格範圍」再往下寫，表中間若有一整列空白，判定會提早結束而**蓋掉既有資料列**。
- **`Notion 權限` 讀法是 `str(member.get(...) or "").strip() or "唯讀"`**，兩層 fallback
  缺一不可：欄位存在但**儲存格空白**時 `.get()` 拿到的是 `""`，dict 的預設值不會頂上 →
  `屬性` 寫成空字串 → 標完成時走成「本地」分支（封存＋刪列）→ 活表上沒有記號 → 復活。
- **Notion 讀寫項目完成也留記號**；本地修改不回寫 Notion，同步欄位仍以來源為準。不可把 Notion 完成改成本地封存刪列，否則任務會復活。

## 使用者說「做完了」但列已經不在了

**「完成的記號」跟「Notion 狀態改變」在搶時間**：使用者若在 Notion 先結案，列就沒了，
本地根本沒機會蓋章。這不是 bug，但會讓使用者事後回「那件事做完了」時撲空——所以
`_explain_missing_todo` 把定位不到的情況拆成三種：已完成的列還在 → ✅；列沒了但對話暫存
裡今天發過該任務的提醒（`⏰`/`⚠️` 開頭）→ ✅ 說明它已不在清單上；兩者皆非才 ❌ 找不到。

## 為什麼上面那個 bug 查了一整天：對話裡看不到 handler 說了什麼

`assistant.py` 的 `claude_reply` 跟 `actions` 是**同一份 JSON 一次生成**的——Claude 在
動手之前就先寫好「已經幫你劃掉了 ✅」。而分派邏輯原本只在結果含 `❌` 時才顯示 handler
的真實回傳值，於是任何「不是 ❌ 的靜默失敗」都被那句樂觀台詞蓋掉：使用者連續好幾天
看到 ✅、提醒卻每小時照響，連「到底走了唯讀分支還是本地分支」都無從判斷。

現在 `TRUTHFUL_ACTIONS`（`delete_todo` / `modify_todo`）的 handler 只要回了**不是 ✅
開頭**的字串（找不到、外部項目不可改…），就直接把原文顯示出來，不再用 `claude_reply`
蓋掉；`[5b] results=` 也會把所有 handler 的原始回傳值印進 log。**新增寫入型 action 時
考慮一起加進 `TRUTHFUL_ACTIONS`**——回一句「好的，已完成 ✅」卻什麼都沒做，比報錯難查
一個數量級。

# Google Sheets 暫時性錯誤（503/429）重試

Sheets 是這個系統唯一的資料庫，而 Google 偶爾會回 `503 The service is currently
unavailable` / `429`（實際發生過：Dashboard 打 `/api/dehumidifier/auto-rule`，503 從
`open_by_key` 一路冒成 ASGI 500 traceback，看起來像「HomeButler 掛了」，其實 process
好好的、Google 幾秒後就恢復）。`sheets.py` 因此裝了**有界短重試**（3 次，0.8s→1.6s
＋jitter，最壞多花約 2.5s）。

**裝的位置很反直覺**：重試包在 `gspread.http_client.HTTPClient.request` 上
（`sheets.py:_install_gspread_get_retry`，import 時安裝、可重入）。這是 monkeypatch，
但那是 gspread 全部 API 呼叫的唯一收斂點——包這裡等於 repo 裡 15+ 個模組、60+ 個
`get_all_records()` / `row_values()` 呼叫點通通有重試，不必逐一改。

**只重試 GET**。非 GET 原樣放行，因為 `append_row` / `add_worksheet` / `update_cell`
**非冪等**：503 有可能是「其實寫進去了、只是回應掉了」，重試會生出重複待辦、重複排程
指令。只有 `ensure_columns` / `update_row_fields` 的 `batch_update`（寫死絕對 range +
絕對值、重寫同值無害）在 `sheets.py` 裡明確包了 `_with_retry`。動這塊前先確認你要包的
呼叫是不是冪等。

沒用 gspread 內建的 `BackOffHTTPClient`：它不分方法一律重試（append 會重複），退避
2s 翻倍到 128s，一個 LINE webhook 可能被卡好幾分鐘。

其他相關行為：
- `RequestContext.load()` 的逐頁 fallback 用 `sheets.no_retry()`（thread-local context
  manager）**明確關掉重試**——因為重試裝在 HTTP 層，不關的話這裡會被乘一輪
  （3 + 6 分頁 × 2 個 GET × 3 次），單一請求卡到 30s+ 超時。batch 已經重試過 3 次了，
  Google 真的掛掉時再試也是白試。但**這次請求的分頁全讀不到時會拋錯**（子集載入見
  下一節；不帶參數時就是那六張），
  不再靜靜回一堆空 list——那會讓 bot 回「沒有待辦事項」、Dashboard 顯示 0 台設備，
  比報錯更誤導人。
- 撐過重試仍失敗 → `main.py` 的 `GSpreadException` handler 回 **503**（不是 500 +
  traceback）；LINE 端回「資料庫暫時連不上」而不是籠統的「未知錯誤」。分類靠
  `sheets.is_transient_error`。
- log 關鍵字：`[SHEETS RETRY]`（吸收掉的抖動）、`[SHEETS ERROR]`（重試用盡）、
  `[SHEETS UNAVAILABLE]`（回 503 給 client）。前者偶爾出現是正常的。

# `RequestContext.load(sheets=...)`：只讀用得到的分頁

`load()` 不帶參數＝原本的六分頁 `values_batch_get`（BATCH_SHEETS），行為沒變。
帶一個名稱 list 進去就只讀那幾張。

**為什麼加這個**：Dashboard 載入一次首頁會觸發 **4 次**完整六分頁讀取
（`/api/dashboard`、`/api/devices`、`/api/schedules`、`/api/dehumidifier/auto-rule`）、
裝置頁 **3 次**，而後三支各自其實只用得到一張表：

| 端點 | 真正用到 |
|---|---|
| `/api/devices` | `智能居家` |
| `/api/schedules` | `排程指令` |
| `/api/dehumidifier/auto-rule` | `智能居家`（`get_all_rules` 只讀感應器的「濕度控制規則」欄） |

其餘四張（對話暫存 / 待辦事項 / 食品庫存 / 家庭成員）整張抓回來後直接丟掉。
`/api/devices` 尤其在關鍵路徑上——Dashboard 的家電控制卡片要等它才畫得出來。

**傳漏了不會靜默失效**：`get()` 讀到沒載入的分頁會自己補讀那一張（多一次
round-trip，但資料正確），刻意不回空 list——回空的話呼叫端會當成「這張表真的沒東西」，
於是 bot 回「沒有待辦事項」、Dashboard 顯示 0 台設備，比慢一次糟得多。所以**優化這件事
最壞只會變慢，不會變錯**；也因此新端點大可放心只列自己要的分頁。

**`set()` 會一併標記成已載入**。少了這行，`calendar_sync` sync 完手動塞回 ctx 的內容
會在下次 `get()` 被判定「沒讀過」而重新抓、把剛寫進去的覆蓋掉。

`_loaded` 布林旗標已換成 `_loaded_sheets` 集合（只在 `sheets.py` 內部使用）。逐頁
fallback 與「全部讀不到就拋錯」的語意不變，只是範圍從固定六張改成「這次請求的那幾張」。

# 天氣資料快取（`ttl_cache.py`）

中央氣象署的預報與觀測都走 `ttl_cache.TTLCache`，**只快取成功結果**。

**歷史背景（快取與首頁拆分前）**：當時 `/api/dashboard` 每次載入實測會打 **6 次** CWA，
每次 timeout 10~15 秒，而拿回來的是同一份資料：

| 呼叫 | 來源 |
|---|---|
| ×2 | `_resolve_location` 為了確認「竹北市」屬於哪個 `data_id`，先打一次探路 |
| ×2 | `get_weather_summary` 拿到 `data_id` 後，用**同一組 (data_id, 地名)** 再打一次 |
| ×2 | `get_observation_for_location`（「明天」的預報其實用不到當下觀測，但 payload 一併附上） |

（今天／明天各一輪，所以每項 ×2。一週預報是同一份 payload，today/tomorrow 只是從
裡面挑不同天解析。）

快取裝在 `_fetch_forecast`（TTL 30 分）與 `get_observation`（TTL 10 分）兩層。實測
6 次 → 首次載入 2 次 → TTL 內 0 次。觀測的 TTL 對齊測站約 10 分鐘的更新頻率：再短
只是重複打同一個值，再長就會讓「當下讀值」名不副實。

**幾個改壞就會靜默失效的點**：

- **失敗絕對不能進快取**。`_fetch_forecast` 失敗回 `{"error": ...}`、`get_observation`
  失敗回 `None`，兩者都不呼叫 `set()`。快取住失敗會讓「CWA 已經恢復了，但我們還在回
  錯誤」這種狀況延續整個 TTL——而這正是最難查的那種 bug。
- **快取命中時回的是同一個 dict 物件**。週預報很大，每次深拷貝會抵銷掉快取的意義。
  現有呼叫端都只讀不寫；要在 caller 裡改動回傳值請先自己複製一份。
- **`TTLCache` 的 `max_entries`（預設 32）是防呆不是效能考量**：正常只有一兩個地點，
  但 `_resolve_location` 對沒見過的鄉鎮會遍歷 22 個縣市，設上限免得意外把 free
  instance 的記憶體吃光。滿了丟最舊寫入的那筆；更新既有 key 會把它移到尾端，不會被
  提前淘汰。
- **有鎖**。polling thread 與 FastAPI threadpool 會並行讀寫同一份快取。

**取捨**：天氣最多落後 30 分鐘、觀測 10 分鐘。對「今明兩天的預報」完全無感，但如果
之後要拿它做接近即時的判斷（例如依當下降雨自動收衣服），記得這裡有這層延遲。

# Git push 環境差異

這個 repo 會被多種 harness 操作（本機 VS Code、claude.ai/code web UI 等）。
如果 `git push` 失敗、錯誤是認證相關（no credentials / permission denied / could not read Username），**立刻停下來，不要繞路**：

- 不要設 git credential helper、token、或改寫 remote URL
- 不要用 curl 打 GitHub API 繞過
- 不要改 SSH

如果當下環境有 GitHub MCP 工具（`mcp__github__*`），直接切過去用；沒有就回報「這個環境沒有 push 權限」由 User 處理。

`main` 跟 feature branch 都可以直接 CLI push，不需要 MCP（過去曾有一次 main protection 卡住 CLI 的記錄，但只是當時的特例，目前不存在）。

# PC monitoring agent 部署現況

家裡兩台 Windows PC 跑 `agent/agent.py` 監控本機指標，每 60s push 到 home-butler `/api/computers/heartbeat`。詳細 setup 看 `agent/README.md`，這裡只記**本家**部署現況跟踩過的雷。

**Hue 中繼已退居備援**：v1.51.0 家庭啟用 `HOME_ASSISTANT_HUE_ENABLED` 之後，照明走 HB → HA → Hue Bridge，
`lighting_transport` 不再打 PC agent；agent 的 Hue 能力留給沒切換的部署。下面表格裡 Hue 502／504 那幾行是
當時的診斷紀錄，現在照明異常**先查 HA**，別直接照那幾列去殺 agent。

**任何推上 `main` 的 commit 都可能觸發 agent 自我更新**：`check_for_updates()` 比對的是 `HEAD` vs
`origin/main` 的整個 SHA，**沒有依修改路徑過濾**，所以只改文件也算。`[skip render]` 只擋 Render 部署，
擋不住這個。實際會不會重啟還要看該台的 `AUTO_UPDATE` 是否開著、以及 `git pull` 與編譯檢查是否成功——
所以是「可能各重啟一次」，不是保證。

## 共用 layout

- repo clone: `C:\butler-agent\repo`
- Task Scheduler bat: `C:\butler-agent\start-agent.bat`
- log: `C:\Users\<user>\butler-agent.log`（RotatingFileHandler 5MB × 3 = ~15MB）
- Task name: `ButlerAgent`

## 各台差異

| Hostname | IP | Python | Windows user | 額外 capability |
|---|---|---|---|---|
| `A7600X_N4070Ti` | 192.168.68.53 | `C:\Python313\python.exe` | `chuan` | — |
| `XEON-1230V2` | 192.168.68.55 | `C:\Program Files\Python314\python.exe` | `User1` | `theater`（agent_config.py 設 `THEATER_AGENT_URL="http://127.0.0.1:8080"` + `THEATER_AGENT_KEY`，轉送到同機 `C:\theater-agent` 的 theater_agent.py；那個 repo 是 github.com/CZLin-TW/theater-agent，有自己的 auto-update） |

bat 範本（python 路徑要對應該台，不要兩台共用同一個 bat）：
```bat
@echo off
cd /d C:\butler-agent\repo\agent
"<該台 python 路徑>" -u agent.py
exit /b %errorlevel%
```

## Auto-update + self-restart（從 SHA `758cba1` / 2026-05-14 起）

agent 自己用 `subprocess.Popen` spawn detached 新 process 取代 `os._exit(1)`，不再仰賴
Task Scheduler restart-on-fail（實測那條路太脆——使用者沒勾／3 次 attempt 用完／
設定漂移都會讓 agent 永久死到下次重開機）。watchdog 跟 auto-update 兩條路都走
`_restart_self()`。

**重要陷阱**：第一次從**舊版**升到**新版** self-restart 邏輯時，跑的還是記憶體裡的
舊 code，會踩到舊 `os._exit(1)` 死亡路徑——所以那一次更新**注定死一次**，必須在那台
PC 手動 `schtasks /end + /run` 把新 code 載進記憶體。之後的更新才會自動 self-restart。

AUTO_UPDATE=False 可在 `agent_config.py` 關掉，push 壞 code 想暫停推送時用。

**單一實例鎖（防重複）**：agent 啟動會對 `C:\butler-agent\agent.lock` 取 OS 獨佔鎖，已有實例在跑就乾淨退出 → 每台只會有一隻；self-restart 時短暫重試讓後繼接手。修掉了「self-restart 孤兒＋手動 `schtasks /run` → 多隻同 hostname 連 `/api/agent/ws` 互踢 (close 1012) → Hue 指令時好時壞」這個雷。

**已知殘留風險：孤兒 crash 沒人救（2026-07-19 兩台都中，躺 3 天）**。self-restart spawn 出的 detached orphan 若自己 hard-crash，watchdog 跟著死，但 Task Scheduler 早就看到原本的 task 乾淨 exit(0)、回到 `Ready`——**不會重新觸發，PC 開著也不會自己活過來**，直到手動介入或重開機。症狀是 log 停在一行正常的 `[push] ok` 之後完全沒再長、而 `State: Ready`（跟「task 根本沒啟動」那類 `Last Result: 3 / 9009` 完全不同，別搞混）。

**「躺三天沒人知道」這半邊已經修掉了**：`health_alert.py` 會在 heartbeat 斷 15 分鐘後推 LINE 告警（見上方「Agent 失聯告警」）。但它只負責**通知**，agent 仍然不會自己活過來——收到告警還是要人去該台跑 `Start-ScheduledTask -TaskName ButlerAgent`。下面那條 repeating trigger 的修法因此仍然值得做。

已做的緩解：`agent.py` 裝了 `sys.excepthook` / `threading.excepthook`，未捕捉例外會先寫 `[fatal] uncaught exception ...` + 完整 traceback 進 log file 才死（以前只進 stderr → log 無聲斷尾、死因查不到，正是 7/19 難查的原因）；主迴圈的 auto-update check 也包了 try/except（`[update] check failed` 後繼續跑）。**但這只讓下次可診斷，不解決「沒人重新觸發」本身**——硬殺（OOM／斷電／防毒結束 process）連 hook 都跑不到。

**已知修法（尚未實作，刻意 deferred）**：幫 `ButlerAgent` Task 加一條每 10~15 分的 repeating trigger 當保險。單一實例鎖讓這樣做是安全的：活著時新實例撞鎖乾淨退出、死了就被自動拉起。注意這是**本機 Task Scheduler 設定、不在 repo 裡**，所以 git push 不會散佈，要逐台設。

## 標準診斷三連發（agent 失聯時）

在那台 PC PowerShell 跑：
```powershell
# A. disk 上 repo HEAD 對不對
cd C:\butler-agent\repo
git rev-parse --short HEAD

# B. Task 狀態
# 註：別用 `schtasks ... | findstr /i "Status Last"`——中文版 Windows 的 schtasks
# 輸出是中文欄位名，英文關鍵字一行都抓不到（會得到空白，誤以為 task 不存在）。
# 用語言無關的 PowerShell cmdlet：
Get-ScheduledTask     -TaskName ButlerAgent | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName ButlerAgent | Select-Object LastRunTime, LastTaskResult, NextRunTime

# B2. agent process 到底在不在（比 Task 狀態更可靠：State=Ready 不代表 agent 活著）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*butler-agent*agent.py*' } |
  Select-Object ProcessId, CreationDate

# C. log 最後幾行
Get-Content "$env:USERPROFILE\butler-agent.log" -Tail 10
```

### 症狀 → 處理對照

| 症狀 | 原因 | 處理 |
|---|---|---|
| log 停在 `[update] X → Y, restarting`、無後續 `agent start:` 行 | 舊 broken `os._exit(1)` 路徑死亡，Task Scheduler 沒接住 | 該台 `schtasks /end + /run` 手動 kick；disk 上 code 已是新版的話一次就活 |
| **PC 開著、`State: Ready`，但 log 停在正常的 `[push] ok` 之後完全沒再長，數天不會自己活** | self-restart 的 detached orphan hard-crash，watchdog 陪葬、Task Scheduler 早已 exit(0) 不再觸發（見上方「已知殘留風險」） | 先用 B2 確認 process 真的不在 → `Start-ScheduledTask -TaskName ButlerAgent` 踢起來。新版會留 `[fatal] ... traceback` 可查死因 |
| Task `Status: Ready` + `Last Result: 3` 或 `9009` | bat 找不到 python.exe，啟動瞬間死 | 對照「各台差異」表修 bat 的 python 路徑 |
| 前景手動 `python agent.py` OK、Task Scheduler 死 | bat 路徑問題（最常見）或 Task Scheduler 環境變數差異 | 同上，看 bat 內容 |
| log 持續 `[push] ok` 但 dashboard 顯示失聯 | server 端／網路問題，非 agent | 看 home-butler render log、確認 `/api/computers/status` 回什麼 |
| 跑著但 `cpu_temp_c` 永遠是 None | LibreHardwareMonitor 沒啟／沒 admin | 看 `agent/README.md` 雷點表 |
| log 反覆 `[ws] connected` 後立刻 `disconnected … 1012`、Hue 指令時好時壞（502 `Unsupported command type` / 504 timeout 交替） | 同台多隻 agent（self-restart 孤兒＋手動 `schtasks /run`）搶同一 agent_id 連線互踢 | Admin PowerShell 按 PID 殺掉所有 butler 的 `agent.py`（**別誤殺其他 agent 如 `theater_agent.py`**）再 `schtasks /run`；單一實例鎖上線後不會再發生 |
| 改 `agent_config.py` 後 `schtasks /end + /run`，log 只多 `[lock] another agent instance is already running`、新 config 沒生效 | 跑著的 agent 是 self-restart 孤兒，`/end` 殺不到；`/run` 的新實例被單一實例鎖正確擋退，但本尊還抱著舊 config（2026-06-10 加 theater capability 時實測） | 按 PID 殺 butler 的 `agent.py`（同上行，別誤殺 `theater_agent.py`）再 `schtasks /run`，看 `[ws] connected` 行確認新 capability |

# 排程結果與非阻塞端點（v1.36.0）

- `schedule_execution.py` 在送設備指令前，先將 Sheet 狀態寫成「待確認」並保存 `執行識別碼`、`執行結果`（自動補欄位）。寫入失敗不發指令；成功則依 `CommandResult.status` 更新為「已執行／執行失敗／待確認」，不可再靠 emoji 或有回字串判斷成功。
- 「已執行」代表設備服務接受，不是 IR 實體狀態讀回。多步指令失敗可能已部分變更設備。例外、回應中斷、完成狀態寫入失敗都不可自動重送；重啟後保留待確認，讓使用者先檢查設備。Panasonic 控制只允許明確 token 417 的認證重試，空回應／傳輸例外不重送。
- 冷氣控制可能增刪排程，結果寫回必須重新依執行識別碼定位，不可沿用舊列號。排程執行器的 process lock 防止同程序 tick 重疊，但 Sheets 不是交易式佇列，不能保證跨實例或外部直接編輯時的原子性；增加 workers／副本前先遷移到可原子認領的儲存。
- 失敗／待確認留在排程列表；Dashboard 用 `include_attention=true` 取得，預設 GET 保留舊契約。移除這些紀錄須傳 `execution_id`，封存保留原狀態與原因，不重新啟動自動關機排程。不要將失敗列直接改回待執行。
- `control_*_result` 給排程與 HTTP API 用；`handle_control_*` 仍回字串，維持 LINE／自動控制的相容性。
- LINE callback 以 async lock 依序處理，耗時 SDK 交給 Starlette threadpool，簽章驗證仍在 SDK 內。照明 Sheets／SwitchBot 呼叫也移入 threadpool；WebSocket 命令仍在 event loop await。手動 notify 使用同步路由，由 FastAPI threadpool 執行。不要在 async 路由直接做同步網路工作。
- 離線驗證：`python -m unittest discover -s tests -v`；使用 fake Sheet／SDK，不啟動 app、不發 LINE 或家電指令。CI 包含排程失敗、重啟不重送、列位移、控制結果與事件迴圈可繼續服務的測試。

## v1.37.0 架構改善與維護邊界

- 待辦寫入：`todo_coordination.todo_write` 將即時讀取、ID 補齊、權限檢查、定位和寫入放在同一個 RLock。一般待辦、週期生成與 Notion 同步共用；Notion 網路查詢在鎖外，另有同步鎖避免舊結果覆蓋新結果。
- 私人待辦：Dashboard BFF 驗證 session，轉送 `X-Dashboard-User` 的 LINE ID；後端以啟用家庭成員精確匹配，不接受前端姓名前綴當權限。私人事項與週期規則在回應之前過濾，修改／完成也重驗。LINE 的 request context 同樣帶 actor。沒有此 header 的既有 API-key 系統呼叫仍有家庭級權限；API key 只能留在受信任伺服器／agent，不能交給瀏覽器。
- 穩定身分：Sheet 新增「待辦ID」欄，舊資料在首次讀取或寫入時補 UUID；Dashboard 修改／完成傳 `todo_id`，舊呼叫仍可用明確的名稱日期時間。匹配多筆一律拒絕，不能取第一筆。
- 天氣：`weather_budget` 的單次查詢預算 10 秒，HTTP timeout 使用剩餘時間；`weather_service` 最多 4 個工作、同日期地點共享進行中請求、滿載立即 503，整批最多等 12 秒後回 504。已開始的同步 HTTP 不能強制中止，但不會無限排隊；失敗不進成功快取。生活摘要 `include_weather=false` 完全不等天氣。
- 工作健康：帶 API key 的 `GET /api/system/jobs` 提供週期、執行中、開始／成功／下次時間、耗時及最近錯誤類別。這表示 callback 的完成情況，不是每個外部裝置已成功；子流程自行捕捉的錯誤仍需看服務 log。狀態在重啟後重建；業務去重仍在 Sheet。
- 部署先 home-butler 再 Dashboard。新版前端需要後端 ID／成員邊界。若要復原，先退 Dashboard，再退後端；新增 Sheet 欄位可以保留，不需刪資料。
- 執行 `python -m unittest discover -s tests -v` 與編譯檢查。測試使用假 Sheets／SDK，不向家電或 LINE 發送訊息。
- 部署仍限定單一 Python process／worker。RLock、工作排程與記憶體快取不是跨主機鎖；Sheets 也沒有多步交易，手動直接改表不受鎖保護。需要多 worker 或多實例時，必須先抽出唯一 scheduler／writer 並導入可交易的資料庫或分散式協調，不能只增加 worker 數。
