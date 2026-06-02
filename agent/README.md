# home-butler PC monitoring agent

跑在每台要監控的 Windows PC 上，每 60 秒讀本機指標（CPU/RAM/GPU/CPU 溫/F@H 狀態）push 到 home-butler `/api/computers/heartbeat`。Dashboard 那邊會顯示成「電腦」區塊的卡片，含當下值 + 24h 折線圖。

```
PC ──60s heartbeat──→ home-butler /api/computers/heartbeat
                          │
                          ↓ in-memory ring buffer (24h × 60s)
                          │
Dashboard ←─pull──── /api/computers/status
```

---

## 前置需求

- **Windows 10+**
- **Python 3.10+，必須是 [python.org](https://python.org) 標準版**——不要用 Microsoft Store 版（user-scoped 安裝路徑會讓 Task Scheduler 在某些情境下找不到 python.exe）
- **Git for Windows**（[git-scm.com](https://git-scm.com/download/win) 或 `winget install --id Git.Git`）
- **NVIDIA GPU + driver**（agent 用 `pynvml` 讀 GPU 指標，AMD/Intel GPU 不支援）
- **[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)**——讀 CPU 溫度的 sensor bridge（Windows 上 `psutil.sensors_temperatures` 沒實作，必須靠 LHM web server）
- **[F@H v8](https://foldingathome.org)** + `pip install lufah`——若這台不跑 F@H 可省略，agent 會自動偵測缺失並把 `fah` 欄位送 None

---

## Setup

### 1. clone repo

```powershell
mkdir C:\butler-agent
cd C:\butler-agent
git clone https://github.com/CZLin-TW/home-butler.git repo
```

### 2. 建自己的 config

```powershell
cd C:\butler-agent\repo\agent
copy agent_config.example.py agent_config.py
notepad agent_config.py
```

填三個必填值：

```python
HOME_BUTLER_API_KEY = "..."   # 從 home-butler Render env var 抄
CPU_MODEL = "Xeon-1230v2"     # 顯示用簡化型號
GPU_MODEL = "GTX-1650S"
```

`agent_config.py` 在 `.gitignore`，**絕對不能 commit**（含 secret）。

### 3. 裝套件（用 python.org 那個 python）

```powershell
& "C:\Program Files\Python314\python.exe" -m pip install -r requirements.txt
```

> 路徑要對應你裝的版本：python.org 下載的 installer 在「Customize installation → Install for all users」會裝到 `C:\Program Files\Python3XX\`。

---

## Philips Hue Bridge 測試工具

`hue_probe.py` 是 Hue 燈光通知整合的第一步，只負責確認這台 PC 能在區網內控制 Hue Bridge。先跑通這個，再把 Hue 通知接進正式 agent / home-butler。

### 1. 建立 Hue application key

先按一下 Hue Bridge 上的實體按鈕，30 秒內執行：

```powershell
cd C:\butler-agent\repo\agent
& "C:\Program Files\Python314\python.exe" hue_probe.py --bridge-ip 192.168.x.x auth
```

成功後會印出要加進 `agent_config.py` 的設定：

```python
HUE_BRIDGE_IP = "192.168.x.x"
HUE_APPLICATION_KEY = "..."
HUE_CLIENT_KEY = "..."
HUE_NOTIFY_GROUPED_LIGHT_ID = "..."
```

其中 `HUE_APPLICATION_KEY` 是 Hue API v2 後續呼叫用的 key；`HUE_CLIENT_KEY` 先留著，通知場景/呼吸燈不一定會用到。
`HUE_NOTIFY_GROUPED_LIGHT_ID` 可先留空，等下一步 `list` 找到要用來提醒的房間/區域 grouped_light id 再填。

### 2. 列出 Hue 資源

```powershell
& "C:\Program Files\Python314\python.exe" hue_probe.py list
```

會列出 `light`、`grouped_light`、`room`、`zone`、`scene` 的名稱與 ID。之後通知通常會用：

- `scene id`：呼叫 Hue App 裡設定好的場景
- `grouped_light id`：對整個房間或區域觸發 breathe

### 3. 測試呼吸燈 / 場景

對某個房間或區域觸發 Hue 內建 breathe alert：

```powershell
& "C:\Program Files\Python314\python.exe" hue_probe.py breathe <grouped_light_id>
```

呼叫 Hue App 內已建立的場景：

```powershell
& "C:\Program Files\Python314\python.exe" hue_probe.py scene <scene_id>
```

如果要啟動動態場景，可試：

```powershell
& "C:\Program Files\Python314\python.exe" hue_probe.py scene <scene_id> --action dynamic_palette
```

注意：Hue Bridge API 是區網 API，這些指令必須在跟 Bridge 同網段的 PC 上跑。Render 上的 home-butler 不能直接連到家裡的 Bridge。

### 4. 待辦燈光提醒

把上一步確認可用的 grouped_light id 寫進 `agent_config.py`：

```python
HUE_NOTIFY_GROUPED_LIGHT_ID = "66b5968b-a4a0-4492-93cf-7f81673412e2"
HUE_LIGHT_REMINDERS_ENABLED = True
```

之後 agent 每 60 秒會查 home-butler `/api/todos/light-reminders`。如果有「有時間、已到期、未完成、燈光提醒=TRUE」的待辦，agent 會依每筆待辦的 `light_area_id` 對對應 Hue grouped_light 觸發 breathe；同一區域同一輪多筆待辦只呼吸一次。待辦被標記完成後，下一輪 API 不再回傳，燈光提醒自然停止。

### 5. WebSocket 即時通道

agent 會主動連到 home-butler 的 `/api/agent/ws`，建立一條從 Render 回到家中 PC 的即時通道。通道會做 hello、heartbeat、在線狀態查詢，也會承接 Hue 指令；Dashboard 照明頁面的列區域與呼吸燈測試按鈕都走這條通道。

這個通道預設開啟，使用同一個 `HOME_BUTLER_API_KEY` 驗證。如果要暫時停用，可以在 `agent_config.py` 加：

```python
AGENT_WEBSOCKET_ENABLED = False
```

如果 log 出現 `[ws] disabled: missing dependency 'websockets'`，代表程式碼已更新但套件還沒裝，跑一次：

```powershell
cd C:\butler-agent\repo\agent
& "C:\Program Files\Python314\python.exe" -m pip install -r requirements.txt
```

### 6. 設置 LibreHardwareMonitor

CPU 溫度在 Windows 上純 Python 讀不到，要靠 LHM 跑著 + 開 web server 當 sensor bridge：

1. 下載 LHM portable zip 解壓到 `C:\Tools\LibreHardwareMonitor\` 之類
2. 啟動 `LibreHardwareMonitor.exe`（**給 admin 權限**，否則某些 sensor 讀不到）
3. Options → Remote Web Server → 確認 Port = `8085` → 勾 Run
4. 驗證：瀏覽器開 `http://localhost:8085/data.json` 應該回 JSON sensor tree
5. **開機自啟**選一個方案：
   - **A. 一般情境**：Options → Run On Windows Startup（會放 startup folder shortcut，user logon 後才跑——表示登入前 agent 會回報 `cpu_temp_c=None`）
   - **B. 24/7 無人值守**：用 Task Scheduler at-startup + Run with highest privileges（不需要 logon）

LHM 沒跑 / 端點掛了 agent 不會 crash，只是 `cpu_temp_c` 回 None。

### 7. 第一次手動跑驗證

```powershell
cd C:\butler-agent\repo\agent
& "C:\Program Files\Python314\python.exe" agent.py
```

預期看到：
```
agent start: Xeon-1230V2 (192.168.68.55) → https://home-butler.onrender.com  log=...
[ws] background channel starting url=wss://home-butler.onrender.com/api/agent/ws
[ws] connected agent_id=Xeon-1230V2 capabilities=pc_monitor,hue
[push] ok cpu=1.8% gpu=0.0% cpu_t=45.0C gpu_t=39.0C fah_paused=True
```

跑通後 Ctrl+C 停掉，繼續做開機自啟。

---

## 開機自啟（Task Scheduler）

仿 F@H 自動化的同款 pattern。

### 1. 寫個 bat

`C:\butler-agent\start-agent.bat`：

```bat
@echo off
cd /d C:\butler-agent\repo\agent
"C:\Program Files\Python314\python.exe" -u agent.py
```

> `-u` 是 unbuffered output 保險用（agent 自己已用 line-buffered logging，但 -u 加上去無害）。

### 2. 註冊 Task

GUI（推薦——直接點 Task Scheduler）：

- **Create Task**（不是 Create Basic Task）
- General：
  - Name: `ButlerAgent`
  - ✅ Run whether user is logged on or not（會問密碼）
  - ❌ Run with highest privileges（agent 不需要 admin）
- Triggers → New → **At startup**
- Actions → New → Start a program → `C:\butler-agent\start-agent.bat`
- Settings → ✅ If the task fails, restart every 1 minute, attempt up to 3 times

或 PowerShell：

```powershell
schtasks /create /tn "ButlerAgent" `
  /tr "C:\butler-agent\start-agent.bat" `
  /sc ONSTART `
  /ru "$env:USERNAME" /rp "你的密碼"
```

### 3. 手動觸發測試

```powershell
schtasks /run /tn "ButlerAgent"
Start-Sleep -Seconds 70
Get-Content "$env:USERPROFILE\butler-agent.log" -Tail 5
schtasks /query /tn "ButlerAgent" /v /fo list | Select-String "Last Result|Status"
```

`Last Result: 0` + log 有 `[push] ok ...` 就成了。重開機驗證 task 自動觸發即可。

---

## Log 與 rotation

agent.py 內建 `RotatingFileHandler`：

- 預設位置：`%USERPROFILE%\butler-agent.log`（即 `C:\Users\<你>\butler-agent.log`）
- 5 MB 切檔、保留 3 份輪替（最多佔用 ~15 MB）
- log 有時間戳，每行 `YYYY-MM-DD HH:MM:SS [push] ok ...`
- 同時印到 stdout——前台 PowerShell 跑 agent 也看得到輸出

要改 log 位置在 `agent_config.py` 加：
```python
LOG_PATH = r"C:\butler-agent\agent.log"
```

---

## 更新 agent

**預設自動更新**：agent 每 5 ticks（TICK_SECONDS=60 時約 5 分鐘）跑一次 `git fetch origin main`，跟本機 HEAD 比對，有新 commit 就 `git pull` → `py_compile` 驗新 code syntax 過得了 → 自己用 `subprocess.Popen` spawn detached 新 process 接班 + `os._exit(0)`（不靠 Task Scheduler restart-on-fail，歷史上那條路太脆——使用者沒勾／3 次 attempt 用完都會讓 agent 永久死到下次重開機）。`main` push 完之後約 5 分鐘內所有 PC 自動跟上，**不用手動**。

要調整頻率可在 `agent_config.py` 設定：

```python
AUTO_UPDATE_CHECK_TICKS = 5
```

auto-update 只會拉新程式碼，不會自動安裝新 Python 套件。遇到這類更新（例如 WebSocket 通道新增 `websockets`）時，要在 PC 上手動跑一次 `python -m pip install -r requirements.txt`，之後同一個套件就不用再裝。

第一次套用 auto-update 功能本身那次升級要手動（拉新版 agent.py 進來才會有自更新邏輯）：

```powershell
cd C:\butler-agent\repo
git pull
schtasks /end /tn "ButlerAgent"
schtasks /run /tn "ButlerAgent"
```

之後就純手動釋出 = 直接 push 到 `main`，所有 PC 自動拿。

### 暫停 auto-update

push 了壞 code 想暫停推送、或某台 PC 想鎖版本 debug：在 `agent_config.py` 加：

```python
AUTO_UPDATE = False
```

然後重啟 agent（`schtasks /end + /run`）。該台 PC 從此不再 check 更新，要拿新版要手動 `git pull` + 重啟 task。

### 看當前版本

agent startup log 第一行會印 `sha=xxxxxxx`：

```powershell
Get-Content "$env:USERPROFILE\butler-agent.log" -Head 1
```

或所有 PC 一起看（home-butler Dashboard 上目前還沒 expose，要的話 agent payload 加一個 sha 欄位即可）。

### 單一實例鎖（防重複 agent）

agent 啟動時先對 `<butler-agent>/agent.lock`（在 repo 外，例如 `C:\butler-agent\agent.lock`）取一個 OS 層級獨佔鎖、整個 process 期間持有（process 結束或 crash 由 OS 自動釋放，不留 stale lock）。**已經有一隻 agent 在跑時，新啟動的會直接乾淨退出（exit 0）**——所以不論 self-restart 留下殘餘、還是手動 `schtasks /run` 重複觸發，每台機器都只會有一隻 agent 活著。self-restart 時鎖會短暫重試，讓後繼 process 在前一個退出、釋放鎖後順利接手，不會把自己擋掉。

> 沒這個鎖時的雷：多隻 agent 用同一個 hostname 連回 `/api/agent/ws` 會互相把對方踢下線（server 對同 agent_id 的舊連線送 close 1012），Hue 指令時好時壞——送到舊記憶體 code 的那隻回「Unsupported command type」、送到互踢中途的會 timeout。

---

## 踩過的雷（快速排查指南）

| 症狀 | 原因 | 解法 |
|---|---|---|
| Task 跑了 Last Result `0x41303` `(SCHED_S_TASK_HAS_NOT_RUN)`、log 沒長 | bat 走 PATH 解 `python.exe` 解到 MS Store 版 stub launcher，background session 跑不起來 | bat 改絕對路徑 `"C:\Program Files\Python3XX\python.exe"` |
| Task `Status: Ready` + `Last Result: 3`、log 完全沒新增（連 `agent start:` 都沒寫） | bat 裡 python 絕對路徑跟實際安裝位置對不上（例如裝在 `C:\Python313\` 但 bat 寫 `C:\Program Files\Python313\`），bat 啟動瞬間就死 | 前景跑一次 `& "<正確 python>" -u agent.py` 確認新 code 沒問題，然後改 bat 的 python 路徑（用 `where.exe python` 或 `Get-ChildItem "C:\Python*","C:\Program Files\Python*"` 找實際位置） |
| 前台跑 agent OK，但 task scheduler 跑 log 50 分鐘才出現 | python stdout 對檔案是 block buffer (4KB)，每行 ~80 bytes 要攢很久 | bat 加 `-u` flag，或升 agent.py 到自帶 line-buffered 版本 |
| `cpu_temp_c` 一直是 None / `[lhm] timed out` | LHM 沒在跑 / port 8085 沒開 / 沒有 admin 權限 | 確認 LHM process 在、Web Server option 勾了、首次啟動給 admin |
| 重開機後 `cpu_temp_c` 短時間是 None，登入後恢復 | LHM 用 startup folder 模式只在 logon 後啟，agent 是 OnStart 早於 logon | 改 LHM 也用 Task Scheduler at-startup with highest privileges，或設 auto-login |
| `[fah] lufah not installed` | lufah 套件沒裝、或裝在不同的 python 而 agent 找不到 | `pip install lufah` 到跑 F@H 排程的那個 python；agent 透過 PATH 找 `lufah.exe` 可以跨 python install |
| `[ws] disabled: missing dependency 'websockets'` | agent 已拉到 WebSocket 版程式碼，但本機 python 還沒安裝新套件 | 在 `C:\butler-agent\repo\agent` 跑 `python -m pip install -r requirements.txt`，再重啟 ButlerAgent |
| Task Scheduler `/ru SYSTEM` 跑失敗 | SYSTEM 帳號讀不到 user-scoped 套件（pynvml、psutil 等） | 一律用本機使用者帳號 `/ru "$env:USERNAME"`，**不要用 SYSTEM** |

---

## Payload schema

每次 POST 的 JSON：

```json
{
  "ip": "192.168.68.55",
  "hostname": "Xeon-1230V2",
  "cpu_model": "Xeon-1230v2",
  "gpu_model": "GTX-1650S",
  "cpu_pct": 1.8,
  "ram_pct": 73.9,
  "gpu_pct": 0.0,
  "gpu_temp_c": 39.0,
  "cpu_temp_c": 45.0,
  "fah": {
    "paused": true,
    "finish": false,
    "units_count": 0,
    "progress_pct": null
  }
}
```

接收端是 `home-butler/web_api.py:api_pc_heartbeat`，丟進 `pc_state.py` 的 in-memory ring buffer（24h × 60s = 1440 點/PC）。home-butler 重啟資料會丟（第一版簡化版設計），下次 heartbeat 開始重新累積。
