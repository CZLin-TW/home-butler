# 接手入口

先讀 [README](Readme.md)、[系統導覽](docs/system-overview.md) 與 [驗證紀錄](docs/verification.md)。下列歷史事故用來解釋設計；目前背景週期以 `main.py` 的 `jobs.add` 為準。更新行為時同步修正舊段落、API 表格與註解，避免只追加版本章節。

IR 名稱修正見 `device_name_resolution.py` 與 `tests/test_ir_names.py`：完整名稱優先，僅等價化結尾「電風扇／電扇」，保留房間；歧義不送出。只有省略名稱時可用單一設備 fallback，明確錯誤名稱不可改控另一台。Siri 漏字與後端名稱解析分開驗證，勿由裸設備名稱自動補上開／關。

Siri 精簡回覆：`/api/assistant` 呼叫 `process_message(..., voice=True)` 後經 `voice_reply.format_voice_reply`，請保持 `{reply}` 契約。純設備控制用實際 handler 結果；錯誤／未知／部分成功／追問不可為縮短而刪除。LINE 不啟用 voice。格式整理不可改掉溫度、負號、百分比與時間，不新增 LLM 呼叫；測試見 `tests/test_voice_reply.py`。

家電專用語音：`device_voice_api.py` 的 `/api/assistant/devices` 只接受獨立 `DEVICE_VOICE_API_KEY`。**不得把此金鑰加入 `verify_api_key` 或讓它進入完整 assistant pipeline**。模型僅見設備目錄投影，不讀家庭／待辦／食品／對話，不接受 `user_id`；先驗證整批白名單動作及參數才執行。冷氣原有防黴／自動關機副作用仍保留，除濕機不能繞過自動鎖。新增能力必須同步專用 schema、驗證、handler 白名單、README 及 `tests/test_device_voice.py`；完整 prompt 的動作擴充不會自動授權家電捷徑。

# 版本管理

v1.43.0 半度目標：`ac_temperature.py` 是 16–30°C、0.5°C 步進和 half-up 捨入的共用規則。回饋啟用保留半度 `最後溫度`，實際 IR 仍為整數；停用時目標歸整但不發 IR、保留 IR 狀態。補償上下界必須 ceil／floor 向內取整。`_ac_saved_state` 是該次保存結果，Dashboard 用它確認後端接受的目標，不能和原始半度請求硬比；語音／排程的 ARG_KEY_TYPES.temperature 使用 num，不能先截斷。Homebridge 1.2.0 需更新並保留配件 UUID／設定。

v1.42.1 回饋 API 的 `evaluate_now: true` 可在保存後立即評估；省略 config 時只讀現有設定，不寫回舊設定。明確評估略過背景週期／啟動等待，仍保留上次命令的間隔、持久化樣本與待確認檢查；不可把它實作成強制 IR 或清除 blocked。純 config POST 保持不送指令的相容契約。

空調室溫回饋（v1.42.0）見 [docs/ac-temperature-feedback.md](docs/ac-temperature-feedback.md)：`最後溫度` 永遠保留舒適目標，IR 補償另存 JSON。設定僅 Dashboard 成員／owner Key 可操作，預設關閉；背景工作不可開關機、重置計時或呼叫一般 handler。所有 AC 命令共用 `ac_feedback.CONTROL_LOCK`，發送前持久化待確認、未知不重送；手動成功保存才解除。修改時同步 controller／API／Dashboard demo 與文件，不把補償 IR 值投影成 HomeKit 目標。

Homebridge 見 [homebridge/README.md](homebridge/README.md)。`homebridge_api.py` 獨立 router 與
`HOMEBRIDGE_API_KEY`，只允許 JSON `HOMEBRIDGE_DEVICE_NAMES` 的唯一啟用 AC；不得把此 Key 加入
owner／device-voice verifier。GET 僅既有快取，POST 重新驗證 Sheet，ctx 限定精確 ID 後才呼叫原 handler。
局部設定保留其他欄位，沒有歷史狀態則拒絕，不暗中套用 defaults。`_ac_state_saved` 表示保存完成，
成功控制但保存失敗回 unknown、不重送。插件依 HomeKit 特徵更新狀態但不可呼叫 SET；防黴回應 fan/on
不可被 HomeKit optimistic off 蓋回。室溫需實際同房間感測器，缺值回 HAP 錯誤。新增類型／模式要同步
allowlist、投影、插件特徵、schema、文件與測試。`npm ci --ignore-scripts && npm test` 在 homebridge/，
測試用真實 HAP／PlatformAccessory 配 fake I/O，不 publish、不控制硬體；後端完整 unittest 仍必跑。
版本以 Dashboard 為準，插件 package 的版本用於打包安裝；不要新增 repo tag／Release。

插件 1.1.0 支援 cool／heat，dry／fan 為同一配件內穩定 subtype 的模式開關；保留原配件 UUID。
TargetHeaterCoolerState 沒有 dry／fan，運行時保留最近冷／暖選擇、Current=idle，以模式開關表達實際模式。
`off_if_mode` 僅接受 dry／fan 的非空清單，且只能與 power=off 搭配；後端以新讀的 Sheet 判斷，
已非該模式時成功 no-op、未知狀態拒絕，不把條件傳入 legacy handler。no-op 也必須去重。
同手勢明確選模式優先於關掉另一模式開關，主電源 OFF 仍優先；未確認結果不可重送或假設成功。
部署先後端再插件，舊後端會拒絕新增欄位。不要把模式關閉改成無條件關機。

v1.39.3 Sheets：`_get_spreadsheet` 持續重用連線，RLock 僅保護建立／失效，不再每分鐘重新認證；google-auth 管理 token 更新，GET 暫時性重試用盡才失效讓下次建立。`update_device_state_fields` 一次讀取最新欄位及 Device ID 所在列，再 RAW 批次写入，缺失／歧義拒寫，未知寫入不重試；不要恢復用 ctx 舊 row index 或長期快取欄位位置。詳見 README、voice-timing 及 `tests/test_sheets_reuse.py`；不提供 Sheets 外部並行編輯的交易保證。

v1.39.2 I/O 精簡：`get_lighting_area_info` 以 `load_area_settings(read_only=True)` 讀既有內容；不得在每次 prompt 組裝時重新建表／ensure_columns，原探索與設定路徑仍負責 schema。`maintain_ac_auto_schedule` 只在實際增刪分支取 worksheet，保留 timer anchor 與先封存後刪除。細分計時的 `parent_span_id` 表示包含關係，不能把外層與子階段相加；詳見 voice-timing 文件與 `tests/test_voice_io.py`。

Siri 獨立指令（v1.39.1）：`process_message(..., voice=True)` 使用 `ask_claude(..., include_history=False)`，一般與降級請求都只送當句；初始讀取五張表，略過對話暫存。背景存檔保留，LINE 預設歷史不變。不得因共用解析器把 LINE 歷史一併關閉；Siri 跨輪省略／確認不再依賴上一句，文件與測試見 README、`tests/test_voice_history.py`。未重写完整 SYSTEM_PROMPT。

語音請求計時見 [docs/voice-timing.md](docs/voice-timing.md)。`request_timing.py` 只在兩個 Siri 路由啟用，以 ContextVar 串起 `[TIMING]`；總時間從路由函式進入算起，不包含代理／threadpool 等待。新計時不可寫入輸入、身分、設備參數或金鑰；`completed` 不是硬體成功。原有模型設定、降級／重試與 `{reply}` 契約保持不變。純觀測不 bump Dashboard 版本。

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

背景工作由 `main.py` 註冊到 `job_runner.py`，每項使用獨立 thread：設備排程每 60 秒；感測器、照明、Notion、待辦提醒、每日推播檢查、agent 健康檢查各每 300 秒。不需要外部 cron。每項不重疊、按固定期限運行，錯過週期不密集補跑。

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

# 冷氣防黴送風（關機前吹乾蒸發器）

關冷氣時若「上次模式是冷氣/除濕 **且** 從最後一次開機算起運轉 ≥ 門檻分」，`handlers/device.py:handle_control_ac` 不直接關，改切送風（mode 4）+ 寫一筆「防黴收尾關」排程（送風分後），由每 60 秒的 `schedules` 工作收尾、真正關掉。**門檻（預設 30）與送風時長（預設 5）可在「智能居家」分頁逐台覆寫**：欄位 `防黴運轉門檻分鐘`、`防黴送風分鐘`（空白用預設；門檻 0 = 每次關都送風）。模式 `ANTIMOLD_MODES={冷氣,除濕}` 仍寫死在 device.py 頂。

幾個**非顯而易見、最容易改壞**的點：

- **防遞迴**：收尾關排程的 params 帶 `antimold_final=True`，那次關機跳過防黴判斷直接關。少了它會無限循環（關→送風→排程關→送風…）。
- **關機後還原模式**：切送風會把「最後模式」覆寫成送風。收尾關排程的 params 另外帶 `restore_mode/temp/fan`（防黴前的原始設定，在切送風「之前」從 prior_row 讀好），收尾關機時由 `_save_ac_last_state(..., restore_on_off=...)` 寫回，否則 UI 跟下次開機都會停在送風而不是原本的冷氣/除濕。
- **來源欄用「防黴」不是「自動」**：跟 AC 自動關機 timer（來源=自動）區隔開，否則 `maintain_ac_auto_schedule` 會把收尾關當成自動關機排程**誤刪**。
- **最後開機時間欄（錨定運轉起點）**：開機時記、**關機時清空**；下次開機若這欄是空的就重新錨定——不只靠「關→開」transition 偵測，避免快取電源狀態漂移（如上次用實體遙控器關、home-butler 以為還開著）時錨不到 → 防黴永不觸發。純調整 on→on（欄位非空）不重置。欄位由 `main.py:_warm_up` 背景呼叫 `ensure_columns` 自動補；真的算不出開機時間（如實體遙控器開的）就**保守不防黴**。
- **使用者中途重開**：任何 power=on 指令會 `_cancel_antimold_schedules` 取消待執行的收尾關，避免剛開又被關掉。
- **自動關機 timer 觸發的關機也會走防黴**（運轉夠久且冷氣/除濕模式）；送風期間刻意不呼叫 `maintain_ac_auto_schedule`，不讓它在送風中又生一筆自動關機。
- 收尾關 trigger=now+送風分鐘；正常情況另加不到一個 60 秒排程週期，仍受工作耗時、網路與服務休眠影響。
- **已知限制**：實體遙控器/Hub 機身鈕直接關（沒經 home-butler）攔不到——接受，不補。

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

# 自動夜燈：「這是不是夜燈」用場景指紋，不是記誰開的

`lighting_auto` 判斷可不可以自動關燈時，問的是**「這個房間現在亮著的是不是那個夜燈場景」**
（`_is_night_light`），不是「這盞燈是不是 auto 開的」。

**為什麼不能用 ownership**：使用者多半用 **Hue 遙控器 / Hue App** 開燈，那些操作
**完全不經過 home-butler**——我們連知道都不知道。所以「記住是不是自己開的」先天記不全：
手動點開的夜燈永遠被當成別人的燈，天亮了、時段結束了都沒人關（實際遇到的困擾）。
in-memory ownership 還會在 Render 重啟後歸零，同樣的洞再開一次。

**指紋來源是 bridge 自己記的 `scene.status`**（Hue API v2，實機確認有；agent 端由
`_hue_scene_status` 帶出來，掛在 `hue.list_areas` 每個 area 的 `scenes[]` 裡，
不需要額外 API 呼叫）。agent recall、遙控器、App 更新的是同一份欄位，天生一視同仁。

判斷分兩段，**第二段才是主力**：
1. `status.active` 非 `inactive` → 燈此刻就是這個場景。
2. 否則比 `status.last_recall`：這個房間裡最後被叫起來的場景就是夜燈 → 算數。

**為什麼需要第二段**（非顯而易見）：`_fire_scene_on` 會在 recall 之後蓋上規則的
`brightness`，bridge 判定「已偏離場景」→ `active` 立刻掉回 `inactive`。也就是說
**auto 自己開的夜燈，`active` 多半是 inactive**，只靠第一段會連自己開的燈都認不出來。
使用者事後用遙控器微調亮度也一樣。`last_recall` 不受這些影響。
（想只靠 `active` 的話，得把亮度直接編進場景、拿掉那步覆寫——但那會改變實際亮度，
沒做。）

刻意不要求 `last_recall` 夠新：使用者按遙控器電源鍵直接開（不 recall 任何場景）時燈會
回到上次的夜燈狀態，而夜燈仍是最後被 recall 的場景——那確實該算夜燈。

**`auto_on` ownership 留著當 fallback**，沒有刪：agent 還沒更新到會回傳 status 的版本、
或規則的 `scene_id` 不屬於該區域時，`_is_night_light` 回 `None`，行為退回改動前
（只關 auto 自己開的）。所以部署後在 agent 自動更新完成前不會有行為變化，也不會因為
拿不到新資料就亂關燈。

**取捨**：判斷的是「長相」不是「意圖」。把燈調成夜燈的樣子想讓它整天亮著 → 還是會被關；
recall 別的場景 → auto 完全不碰。另外這個機制**救不到**「拿過時亮值誤動作」——過時的
『已經變亮』讀值仍可能把還在暗處的夜燈關掉、下一輪又開，那是感應器讀值新鮮度的問題。

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

# Aqara Cloud API（FP2）：認證有狀態，權杖必須落地

`aqara_api.py` 是 Aqara Open API v3.0 的封裝，目前**只接到 API 為止**：授權、列裝置、
把 resource 讀回來，全部走 `main.py` 的 `/aqara/*` debug 端點。**沒有** polling thread、
沒有寫進「智能居家」分頁、Dashboard 與 LINE bot 都還看不到它。功能另外規劃。

⚠️ 這個模組**沒有對真的 FP2 跑過**（開發環境的 egress proxy 擋掉 aqara.com）。協定形狀
逐字對齊 Aqara 官方 Home Assistant 整合的 `aiot_cloud.py`（簽名字串、intent 名稱、header
大小寫），能離線確定的都確定了；連上真機後若有出入，以真機為準。

**跟 SwitchBot 最大的差別是認證有狀態**：SwitchBot 是 token + secret 每次現簽、無狀態，
Aqara 是 accessToken / refreshToken。以下幾點改壞了都會靜默失效或直接要人重跑授權：

- **簽名整串 `.lower()`，含 AppKey 與 AccessToken**。這是官方 SDK 的行為不是筆誤。
  少了那一步、或只 lower 一部分，伺服器一律回簽名錯誤，而錯誤訊息**不會**告訴你錯在
  大小寫。欄位順序（AccessToken → Appid → Keyid → Nonce → Time → AppKey）也是簽名的
  一部分，不能重排。
- **refreshToken 每次刷新都會換一把，舊的當場作廢**——所以權杖一定要寫進 Sheet
  （「系統狀態」KV，跟每日推播 marker 同一張表），純記憶體撐不過 Render 重啟。
- **寫 Sheet 的順序是 refreshToken 先寫**。中途掛掉時「新 refresh + 舊 access」還能靠
  code 108 自動補救，「舊 refresh + 新 access」是死局（Sheet 上那把 refresh 已作廢），
  只能請人重跑一次人工授權。寫失敗會設 `_persist_dirty`，下次呼叫補寫並印警告。
- **108 重試有防遞迴旗標**（`token_maintenance=False`）。refresh 自己那一發、以及 108
  之後的那次重試都不可以再觸發權杖維護，否則權杖真的死掉時會無限迴圈。
- **`_ensure_loaded_locked` 讀 Sheet 失敗時不標記已載入**，下次呼叫再試——一次 Sheets
  抖動不該讓整個服務退化成「未授權」直到重啟。
- **權杖不進 log 也不進 API 回應**。`token_status()` / `exchange_token()` 回的是
  `_mask()` 過的頭尾，別為了 debug 方便把原文印出來。
- **`write.resource.device` 的 data 是 list 不是 dict**（官方 SDK 的 `list_data=True`），
  包成 dict 送會被拒。`aqara_api.write_resource` 已經處理，自己組 intent 時要注意。
- **`probe_regions` 刻意不用 `config.auth.getAuthCode` 探測**——那個會真的寄六封授權信。
  改用不帶權杖的 `query.device.info`：回權杖相關錯誤＝這一區認得你的 App 憑證。

## 為什麼沒有寫死 resource id

Aqara 的每個欄位是一組 `x.y.z` 數字，官方文件按 model 分開列，網路上找得到的多半對不上
自己那台的韌體。**寫死一組猜來的 id，錯了是靜默失效**（永遠讀到空值，不會報錯）。

所以 `read_device()` 是「先打 `query.resource.info` 問這個 model 開放哪些 resource，
再照那份清單讀值」，清單快取 6 小時（只快取成功結果）。這也讓 FP2 以外的 Aqara 裝置
不用改 code 就讀得到。

「哪個欄位是有沒有人」目前用名稱關鍵字猜（`_PRESENCE_NAME_HINTS`），**猜不到就回
`None`，不硬挑一個看起來像的**——挑錯會讓上層拿著假值長出自動化，比承認不知道糟得多。
用 `/aqara/devices/{did}/values` 對照真機（人走進 / 走出各打一次，diff）確認之後，把 id
填進環境變數 `AQARA_FP2_PRESENCE_RESOURCE` 釘死，關鍵字猜測就完全不參與判斷。

## 之後要接 polling 時

- 這個模組**還沒有熔斷器**（`lg_api` / `panasonic_api` 都有）。單純被人手打 debug 端點
  時不需要，但一旦掛進每 5 分鐘的 polling thread，Aqara 雲端掛掉就會變成穩定的重打——
  照 `lg_api._circuit_open` 那套補上再接。
- 讀值有雲端延遲、也吃 API 配額。要做「人一進門就開燈」這種秒級反應，該走 Aqara 的
  訊息推送（webhook）而不是輪詢；`/aqara/raw` 就是留給那類還沒封裝的 intent
  （例如 `config.resource.subscribe`）先探路的逃生口。

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
