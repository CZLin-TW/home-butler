# 版本管理

系統版本不在 home-butler 管。Source of truth 是 **Dashboard 的 `package.json:version`**，本 repo 透過 `config.py:get_app_version()` 在 runtime 撈 Dashboard `/api/version`（1 小時 cache，失敗 fallback「未知」），由 `prompt.py` 注入 `SYSTEM_PROMPT`，讓 LINE bot 能回答「目前版本是？」之類的問題。

需要設定環境變數 `DASHBOARD_URL`（Dashboard 部署網址）才撈得到；沒設或撈不到就會回「未知」，但 bot 仍能正常運作。

**bump 時機**：使用者**體感得到**的變化才 bump（新功能、UI/行為改動、會被察覺的 bug fix）。純 refactor、註解、文件、type 整理**不 bump**。

**bump 流程**：只動 Dashboard `package.json:version` + push 一次。**home-butler 完全不用 push 也不用改任何檔案**。最壞情況是 cache 還沒過期、LINE 回答的版本舊一陣子，1 小時內自動同步。

本專案不使用 git tag / GitHub Releases；版本以 Dashboard `package.json` 為準，git history 自己就是版本軌跡。

# 排程 / 推播架構（in-process scheduler；GAS 已退場）

時間驅動的工作**全部跑在 `main.py` 的 polling thread**（每 5 分一 tick），不再用 Google Apps Script cron。

- **realtime tick**：`notify.run_realtime_tick(ctx)`——行事曆同步 / 週期待辦生成 / 待辦提醒 / 設備排程執行 / 封存。每 5 分一次（原 GAS 15 分，現在更即時）。每步驟各自 try/except 隔離，一步壞不擋其餘。
- **每日綜合推播**：`notify.run_daily_push_if_due(ctx)`——每天過了 `DAILY_PUSH_HOUR`（env，預設 21 點）後第一個 tick 觸發一次。去重 marker 存在 Sheet「系統狀態」分頁的 `最後每日推播日期`（跨 Render 重啟存活，不重發不漏發；睡整晚跨午夜才醒則當天不補）。
- `/notify`、`/notify_realtime` 端點**保留**但只當手動觸發（debug / 補發）；不再有外部 cron 打它們。手動 `/notify` 不檢查也不更新每日 marker。

**為什麼能拿掉 GAS**：這些工作全是 Sheet-anchored / 冪等（觸發時間、狀態、marker 都在 Sheet），重啟後 thread 讀同一份 Sheet 就能補上，不依賴外部時鐘的精準或存活（code 本就容忍漂移：`is_near_hour` ±5 分、排程 2h 過期窗）。GAS 當年的唯一價值是「喚醒睡著的 Render ＋幹活綁同一個 HTTP beat」，但 thread 要能跑的前提（實例醒著）本來就由 UptimeRobot 扛——GAS 的保溫只是跟它**重複**。

**UptimeRobot 是 load-bearing 保溫，不是普通監控**：每 5 分 ping `/` 防止 Render idle-sleep（Readme 標「防冷啟動」）。拿掉 GAS 後，「保持實例醒著、讓 polling thread 不被凍住」這件事**完全靠它**。所以**別把 UptimeRobot 當可有可無的監控隨手關掉**——關了它，排程與推播會跟著 Render 一起睡死。

唯二的記憶體計時（除濕機去抖 `above_since`/`below_since`、照明 `window_active` 邊緣）本來就在這條 thread 上、且自我修正，重啟最多晚一個去抖窗，無資料損失。

**切換注意**：部署後要去 Google Apps Script 把舊的兩條觸發（`/notify` 日計時器、`/notify_realtime` 15 分計時器）**刪除或停用**，否則跟 thread 雙跑。重疊期短且工作冪等，無害，但別長期掛著。

# 冷氣防黴送風（關機前吹乾蒸發器）

關冷氣時若「上次模式是冷氣/除濕 **且** 從最後一次開機算起運轉 ≥30 分」，`handlers/device.py:handle_control_ac` 不直接關，改切送風（mode 4）+ 寫一筆「防黴收尾關」排程（5 分後），由 polling thread 的 realtime tick 來收、真正關掉。參數在 device.py 頂：`ANTIMOLD_FAN_MINUTES=5`、`ANTIMOLD_MIN_RUNTIME_MINUTES=30`、`ANTIMOLD_MODES={冷氣,除濕}`。

幾個**非顯而易見、最容易改壞**的點：

- **防遞迴**：收尾關排程的 params 帶 `antimold_final=True`，那次關機跳過防黴判斷直接關。少了它會無限循環（關→送風→排程關→送風…）。
- **來源欄用「防黴」不是「自動」**：跟 AC 自動關機 timer（來源=自動）區隔開，否則 `maintain_ac_auto_schedule` 會把收尾關當成自動關機排程**誤刪**。
- **最後開機時間欄**：只在 關→開 transition 記錄（純調整 on→on **不**重置），運轉時長才算得準。欄位由 `main.py` startup `ensure_columns` 自動補；空白（例如實體遙控器開的）就**保守不防黴**。
- **使用者中途重開**：任何 power=on 指令會 `_cancel_antimold_schedules` 取消待執行的收尾關，避免剛開又被關掉。
- **自動關機 timer 觸發的關機也會走防黴**（運轉夠久且冷氣/除濕模式）；送風期間刻意不呼叫 `maintain_ac_auto_schedule`，不讓它在送風中又生一筆自動關機。
- 實際送風 5~10 分（收尾關 trigger=now+5 分，受 thread 5 分粒度影響）。
- **已知限制**：實體遙控器/Hub 機身鈕直接關（沒經 home-butler）攔不到——接受，不補。

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

## 標準診斷三連發（agent 失聯時）

在那台 PC PowerShell 跑：
```powershell
# A. disk 上 repo HEAD 對不對
cd C:\butler-agent\repo
git rev-parse --short HEAD

# B. Task 狀態
schtasks /query /tn "ButlerAgent" /v /fo list | findstr /i "Status Last"

# C. log 最後幾行
Get-Content "$env:USERPROFILE\butler-agent.log" -Tail 10
```

### 症狀 → 處理對照

| 症狀 | 原因 | 處理 |
|---|---|---|
| log 停在 `[update] X → Y, restarting`、無後續 `agent start:` 行 | 舊 broken `os._exit(1)` 路徑死亡，Task Scheduler 沒接住 | 該台 `schtasks /end + /run` 手動 kick；disk 上 code 已是新版的話一次就活 |
| Task `Status: Ready` + `Last Result: 3` 或 `9009` | bat 找不到 python.exe，啟動瞬間死 | 對照「各台差異」表修 bat 的 python 路徑 |
| 前景手動 `python agent.py` OK、Task Scheduler 死 | bat 路徑問題（最常見）或 Task Scheduler 環境變數差異 | 同上，看 bat 內容 |
| log 持續 `[push] ok` 但 dashboard 顯示失聯 | server 端／網路問題，非 agent | 看 home-butler render log、確認 `/api/computers/status` 回什麼 |
| 跑著但 `cpu_temp_c` 永遠是 None | LibreHardwareMonitor 沒啟／沒 admin | 看 `agent/README.md` 雷點表 |
| log 反覆 `[ws] connected` 後立刻 `disconnected … 1012`、Hue 指令時好時壞（502 `Unsupported command type` / 504 timeout 交替） | 同台多隻 agent（self-restart 孤兒＋手動 `schtasks /run`）搶同一 agent_id 連線互踢 | Admin PowerShell 按 PID 殺掉所有 butler 的 `agent.py`（**別誤殺其他 agent 如 `theater_agent.py`**）再 `schtasks /run`；單一實例鎖上線後不會再發生 |
| 改 `agent_config.py` 後 `schtasks /end + /run`，log 只多 `[lock] another agent instance is already running`、新 config 沒生效 | 跑著的 agent 是 self-restart 孤兒，`/end` 殺不到；`/run` 的新實例被單一實例鎖正確擋退，但本尊還抱著舊 config（2026-06-10 加 theater capability 時實測） | 按 PID 殺 butler 的 `agent.py`（同上行，別誤殺 `theater_agent.py`）再 `schtasks /run`，看 `[ws] connected` 行確認新 capability |
