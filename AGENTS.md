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

時間驅動的工作**全部跑在 `main.py` 的 polling thread**（每 5 分一 tick），不再用 Google Apps Script cron。

- **realtime tick**：`notify.run_realtime_tick(ctx)`——行事曆同步 / 週期待辦生成 / 待辦提醒 / 設備排程執行 / 封存。每 5 分一次（原 GAS 15 分，現在更即時）。每步驟各自 try/except 隔離，一步壞不擋其餘。
- **每日綜合推播**：`notify.run_daily_push_if_due(ctx)`——每天過了 `DAILY_PUSH_HOUR`（env，預設 21 點）後第一個 tick 觸發一次。去重 marker 存在 Sheet「系統狀態」分頁的 `最後每日推播日期`（跨 Render 重啟存活，不重發不漏發；睡整晚跨午夜才醒則當天不補）。
- `/notify`、`/notify_realtime` 端點**保留**但只當手動觸發（debug / 補發）；不再有外部 cron 打它們。手動 `/notify` 不檢查也不更新每日 marker。

**為什麼能拿掉 GAS**：這些工作全是 Sheet-anchored / 冪等（觸發時間、狀態、marker 都在 Sheet），重啟後 thread 讀同一份 Sheet 就能補上，不依賴外部時鐘的精準或存活（code 本就容忍漂移：`is_near_hour` ±5 分、排程 2h 過期窗）。GAS 當年的唯一價值是「喚醒睡著的 Render ＋幹活綁同一個 HTTP beat」，但 thread 要能跑的前提（實例醒著）本來就由 UptimeRobot 扛——GAS 的保溫只是跟它**重複**。

**UptimeRobot 是 load-bearing 保溫，不是普通監控**：每 5 分 ping `/` 防止 Render idle-sleep（Readme 標「防冷啟動」）。拿掉 GAS 後，「保持實例醒著、讓 polling thread 不被凍住」這件事**完全靠它**。所以**別把 UptimeRobot 當可有可無的監控隨手關掉**——關了它，排程與推播會跟著 Render 一起睡死。

唯二的記憶體計時（除濕機去抖 `above_since`/`below_since`、照明 `window_active` 邊緣）本來就在這條 thread 上、且自我修正，重啟最多晚一個去抖窗，無資料損失。

**切換注意**：部署後要去 Google Apps Script 把舊的兩條觸發（`/notify` 日計時器、`/notify_realtime` 15 分計時器）**刪除或停用**，否則跟 thread 雙跑。重疊期短且工作冪等，無害，但別長期掛著。

# Agent 失聯告警（`health_alert.py`）

`pc_state` 一直算得出每台 PC 的 `online`，但那個值**只餵給 Dashboard 畫灰點**——
沒人盯著 Dashboard 的時候等於沒有監控。2026-07-19 兩台 agent 的 self-restart 孤兒
hard-crash 就是這樣**躺了三天**沒人知道（Task Scheduler 早已 exit(0) 回 `Ready`
不再觸發）。`health_alert` 把那條線接到 LINE，掛在 `notify.run_realtime_tick` 最後一步。

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
- **收件人**：「家庭成員」分頁的 `系統告警` 欄勾 TRUE 的啟用成員（欄位由 `main.py` startup
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

關冷氣時若「上次模式是冷氣/除濕 **且** 從最後一次開機算起運轉 ≥ 門檻分」，`handlers/device.py:handle_control_ac` 不直接關，改切送風（mode 4）+ 寫一筆「防黴收尾關」排程（送風分後），由 polling thread 的 realtime tick 來收、真正關掉。**門檻（預設 30）與送風時長（預設 5）可在「智能居家」分頁逐台覆寫**：欄位 `防黴運轉門檻分鐘`、`防黴送風分鐘`（空白用預設；門檻 0 = 每次關都送風）。模式 `ANTIMOLD_MODES={冷氣,除濕}` 仍寫死在 device.py 頂。

幾個**非顯而易見、最容易改壞**的點：

- **防遞迴**：收尾關排程的 params 帶 `antimold_final=True`，那次關機跳過防黴判斷直接關。少了它會無限循環（關→送風→排程關→送風…）。
- **關機後還原模式**：切送風會把「最後模式」覆寫成送風。收尾關排程的 params 另外帶 `restore_mode/temp/fan`（防黴前的原始設定，在切送風「之前」從 prior_row 讀好），收尾關機時由 `_save_ac_last_state(..., restore_on_off=...)` 寫回，否則 UI 跟下次開機都會停在送風而不是原本的冷氣/除濕。
- **來源欄用「防黴」不是「自動」**：跟 AC 自動關機 timer（來源=自動）區隔開，否則 `maintain_ac_auto_schedule` 會把收尾關當成自動關機排程**誤刪**。
- **最後開機時間欄（錨定運轉起點）**：開機時記、**關機時清空**；下次開機若這欄是空的就重新錨定——不只靠「關→開」transition 偵測，避免快取電源狀態漂移（如上次用實體遙控器關、home-butler 以為還開著）時錨不到 → 防黴永不觸發。純調整 on→on（欄位非空）不重置。欄位由 `main.py` startup `ensure_columns` 自動補；真的算不出開機時間（如實體遙控器開的）就**保守不防黴**。
- **使用者中途重開**：任何 power=on 指令會 `_cancel_antimold_schedules` 取消待執行的收尾關，避免剛開又被關掉。
- **自動關機 timer 觸發的關機也會走防黴**（運轉夠久且冷氣/除濕模式）；送風期間刻意不呼叫 `maintain_ac_auto_schedule`，不讓它在送風中又生一筆自動關機。
- 實際送風 5~10 分（收尾關 trigger=now+5 分，受 thread 5 分粒度影響）。
- **已知限制**：實體遙控器/Hub 機身鈕直接關（沒經 home-butler）攔不到——接受，不補。

# Notion 待辦：完成的記號蓋在 Sheet，不會回寫 Notion

公司的 Notion 只能讀（`notion_api.py` 沒有任何寫入函式），所以「完成」是在本地蓋章：
`handle_delete_todo` 對 `屬性=唯讀` 的列**只改狀態成「已完成」、把列留著**。那列是給
`sync_external_events` 看的記號——下一輪 sync 會把它收進 `completed_external`
（key = `事項+日期+時間`），從 Notion 拉到同一筆時就跳過不寫回，任務因此不會復活。

**sync 是砍掉重建，不是差異更新**：每 5 分鐘把「來源≠本地 且 狀態=待辦」的列**全部刪除**，
再把當下符合「Notion 篩選」的事件寫回表尾。兩個後果：
- Notion 那邊狀態一改（例如不再是 Incoming），那列就**直接消失**，不會留下任何完成紀錄。
- 每 5 分鐘所有外部列的列號都會變 → 寫入前一定要用即時 `get_all_values()` 重新定位
  （信任請求開頭的快取列號會寫到別列，實測踩過）。

**「完成的記號」跟「Notion 狀態改變」在搶時間**：使用者若在 Notion 先結案，列就沒了，
本地根本沒機會蓋章。這不是 bug，但會讓使用者事後回「那件事做完了」時撲空——所以
`_explain_missing_todo` 把定位不到的情況拆成三種：已完成的列還在 → ✅；列沒了但對話暫存
裡今天發過該任務的提醒（`⏰`/`⚠️` 開頭）→ ✅ 說明它已不在清單上；兩者皆非才 ❌ 找不到。

**已知未修的雷：`Notion 權限` 若改成「讀寫」，完成會失效**。那時 `屬性` 會變成讀寫 →
`handle_delete_todo` 走的是本地那條「封存＋刪列」→ 活表上不再有「已完成」那列 →
下一輪 sync 的 `completed_external` 收集不到它 → 從 Notion 原封不動拉回來變待辦，
**5 分鐘內復活、逾時提醒重新開始響**。要開放讀寫之前，得先讓完成回寫 Notion，
或讓唯讀那條分支的邏輯也涵蓋讀寫的外部項目。

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
  Google 真的掛掉時再試也是白試。但**六個分頁全讀不到時會拋錯**，
  不再靜靜回一堆空 list——那會讓 bot 回「沒有待辦事項」、Dashboard 顯示 0 台設備，
  比報錯更誤導人。
- 撐過重試仍失敗 → `main.py` 的 `GSpreadException` handler 回 **503**（不是 500 +
  traceback）；LINE 端回「資料庫暫時連不上」而不是籠統的「未知錯誤」。分類靠
  `sheets.is_transient_error`。
- log 關鍵字：`[SHEETS RETRY]`（吸收掉的抖動）、`[SHEETS ERROR]`（重試用盡）、
  `[SHEETS UNAVAILABLE]`（回 503 給 client）。前者偶爾出現是正常的。

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
