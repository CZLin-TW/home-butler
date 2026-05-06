# home-butler PC monitoring agent

跑在每台要監控的 Windows PC 上，每 60 秒讀本機指標（CPU/RAM/GPU/CPU 溫/F@H 狀態）push 到 home-butler `/api/computers/heartbeat`。Dashboard 那邊會顯示成「電腦」區塊的卡片。

---

## 前置

- Windows 10+
- Python 3.10+（**勿用 Microsoft Store Python**——Task Scheduler 在 SYSTEM 帳號跑時讀不到 user-scoped 安裝。從 [python.org](https://python.org) 下載標準版）
- NVIDIA GPU + driver 已裝
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)（讀 CPU 溫度用）
- [F@H v8](https://foldingathome.org) + `pip install lufah`（如果這台有跑 F@H 才需要）

## Setup

```powershell
git clone https://github.com/CZLin-TW/home-butler.git
cd home-butler\agent
copy agent_config.example.py agent_config.py
notepad agent_config.py     # 填 API key + CPU/GPU model 簡化名
pip install -r requirements.txt
python agent.py
```

第一輪輸出長這樣：
```
agent start: Xeon-1230V2 (192.168.68.55) → https://home-butler.onrender.com
[push] ok cpu=1.8% gpu=0.0% cpu_t=45.0C gpu_t=39.0C fah_paused=True
```

IP / hostname 會自動偵測；CPU/GPU model 填顯示用簡化字串就好。

## LibreHardwareMonitor 設置

CPU 溫度在 Windows 上純 Python 拿不到，必須靠 LHM 當 sensor bridge：

1. 下載 LHM portable zip 解壓
2. 跑 LibreHardwareMonitor.exe（給 admin 權限，否則某些 sensor 讀不到）
3. Options → Remote Web Server → 確認 Port = `8085` → 勾 Run
4. Options → Run On Windows Startup
5. 驗證：瀏覽器開 `http://localhost:8085/data.json` 應該回 JSON

LHM 沒跑 / 端點掛了 agent 不會 crash，只是 `cpu_temp_c` 回 None。

## 更新

```powershell
cd home-butler
git pull
```

`agent_config.py` 不會被動到（在 .gitignore 內）。改了 agent.py 重新跑就好。

## 開機自啟（待補）

第一版先手動跑驗證。之後會用 Task Scheduler at-startup + 本機使用者帳號（不要 SYSTEM——SYSTEM 讀不到 user-scoped 的 lufah / pip 套件）。

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

接收端是 `home-butler/web_api.py:api_pc_heartbeat`，丟進 `pc_state` 模組的 in-memory ring buffer（24h × 60s = 1440 點）。
