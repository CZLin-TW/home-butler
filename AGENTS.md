# 版本管理

系統版本不在 home-butler 管。Source of truth 是 **Dashboard 的 `package.json:version`**，本 repo 透過 `config.py:get_app_version()` 在 runtime 撈 Dashboard `/api/version`（1 小時 cache，失敗 fallback「未知」），由 `prompt.py` 注入 `SYSTEM_PROMPT`，讓 LINE bot 能回答「目前版本是？」之類的問題。

需要設定環境變數 `DASHBOARD_URL`（Dashboard 部署網址）才撈得到；沒設或撈不到就會回「未知」，但 bot 仍能正常運作。

**bump 時機**：使用者**體感得到**的變化才 bump（新功能、UI/行為改動、會被察覺的 bug fix）。純 refactor、註解、文件、type 整理**不 bump**。

**bump 流程**：只動 Dashboard `package.json:version` + push 一次。**home-butler 完全不用 push 也不用改任何檔案**。最壞情況是 cache 還沒過期、LINE 回答的版本舊一陣子，1 小時內自動同步。

本專案不使用 git tag / GitHub Releases；版本以 Dashboard `package.json` 為準，git history 自己就是版本軌跡。

# Git push 環境差異

這個 repo 會被多種 harness 操作（本機 VS Code、claude.ai/code web UI 等）。
如果 `git push` 失敗、錯誤是認證相關（no credentials / permission denied / could not read Username），**立刻停下來，不要繞路**：

- 不要設 git credential helper、token、或改寫 remote URL
- 不要用 curl 打 GitHub API 繞過
- 不要改 SSH

如果當下環境有 GitHub MCP 工具（`mcp__github__*`），直接切過去用；沒有就回報「這個環境沒有 push 權限」由 User 處理。

注意 `main` branch 有 protection，CLI push 會 403；走 `mcp__github__push_files` 直接打 API 才寫得進去（會合成單一 commit，可接受）。Feature branch 可以直接 CLI push。

# PC monitoring agent 部署現況

家裡兩台 Windows PC 跑 `agent/agent.py` 監控本機指標，每 60s push 到 home-butler `/api/computers/heartbeat`。詳細 setup 看 `agent/README.md`，這裡只記**本家**部署現況跟踩過的雷。

## 共用 layout

- repo clone: `C:\butler-agent\repo`
- Task Scheduler bat: `C:\butler-agent\start-agent.bat`
- log: `C:\Users\<user>\butler-agent.log`（RotatingFileHandler 5MB × 3 = ~15MB）
- Task name: `ButlerAgent`

## 各台差異

| Hostname | IP | Python | Windows user |
|---|---|---|---|
| `A7600X_N4070Ti` | 192.168.68.53 | `C:\Python313\python.exe` | `chuan` |
| `XEON-1230V2` | 192.168.68.55 | `C:\Program Files\Python314\python.exe` | `User1` |

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
