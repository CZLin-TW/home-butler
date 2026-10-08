# macOS 電腦指標 collector 與可選 daemon

`macos_metrics.py` 是獨立的唯讀 collector，不載入 Windows `agent.py`、vision、theater、HA、Hue 或家電設定，沒有 WebSocket 控制或 auto-update。可選的原生 sender／LaunchDaemon 原始碼與安全操作見 [macOS daemon](macos_daemon/README.md)。

## 資料與限制

| 指標 | 來源 | 可用性 |
| --- | --- | --- |
| CPU 使用率 | psutil 1 秒取樣 | 0–100%，跨核心整體使用率 |
| 記憶體壓力 | 唯讀 kern.memorystatus_vm_pressure_level | normal／warning／critical；不可當百分比，失敗／未知值為 null |
| RAM 使用率（相容欄位） | psutil virtual_memory().percent | 保留原契約與既有歷史；新版 Mac 卡改顯示記憶體壓力 |
| GPU 使用率 | AGXAccelerator / PerformanceStatistics / Device Utilization % | 可選；只接受有效 0–100 數值，未公開穩定契約、OS 更新可能失效；不以 renderer/tiler 數值代替 |
| CPU/GPU 溫度 | 無已驗證的可靠無特權來源 | 固定 null；Dashboard 顯示 unavailable，沒有假設 0°C |
| TCMb／TCMz | 唯讀 AppleSMC | 獨立感測器欄位；缺值為 null，M6 語義未官方確認 |
| load average 1/5/15 分鐘、RAM 總量 | os.getloadavg / psutil | 只在預設本機 JSON 的 local_only；既有後端契約不接收／儲存，不顯示在卡片 |

collector 沒有執行 sudo、powermetrics、特權 SMC helper，也不以 thermal pressure 假裝攝氏。CPU/RAM 取樣失敗會略過 heartbeat，不捏造零值；GPU 讀取失敗保留其餘指標。

## 安全本機試跑

Python 3.11+；在獨立 checkout 建 venv，依 `requirements-macos.txt` 安裝 psutil：

```sh
python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install -r agent/requirements-macos.txt
.venv-macos/bin/python agent/macos_metrics.py --ip 192.0.2.20
```

192.0.2.20 是文件用假 IP，只作離線驗證。預設單次採樣、只 stdout、不發網路；不會探測區網 IP 或自動讀取機器 hostname。預設顯示名稱 Mac mini，可用 `--hostname` 指定。這不是溫度監控已完成驗收。

## 正式接入前需核對

目前既有 `/api/computers/heartbeat` 使用家庭級 `HOME_BUTLER_API_KEY`，沒有僅能寫 telemetry 的獨立金鑰。collector 本身不持久化金鑰；可選 daemon 的 System Keychain 設定必須由使用者親自授權及輸入。若選擇新建受限 telemetry key，須另外授權後端驗證與憑證部署，不能把 HA／語音 key 加進通用驗證。

先核對目標 home-butler HTTPS origin、Mac 的穩定 LAN IP（既有卡片主鍵）與金鑰供應方式，再由使用者授權送出。repo Windows 範例 origin 是 `https://home-butler.onrender.com`，不代表已核實目前正式目的地；collector 沒有預設外送網址。

傳出的白名單資料：IP、指定 hostname、CPU/GPU 型號、CPU/RAM/GPU 百分比、null 溫度與 null fah。沒有 process list、攝影影像、檔案、帳號名稱、序號或 thermal pressure。目的地只使用明確指定的 origin + `/api/computers/heartbeat`，不跟隨 redirect、不讀環境 proxy、不做 fallback；遠端限 HTTPS，HTTP 只允許 literal 127.0.0.1/::1 測試。

後端目前 24h / 1440 點記憶體＋既有「PC 監控歷史」Sheet；不增加欄位。5 分鐘未回報會離線；現有 health alert 預設約 15 分鐘可能 LINE 告警，所以一次正式測試也會留下卡片與告警資格。DHCP 換 IP 會新增卡、舊卡可能告警；部署前須核對穩定 IP，不在這個 collector 自動改 DNS／DHCP／VPN。

授權後可用 `--send --url <approved-origin>` 做一次送出，再以 `--watch --interval 60` 前景運行；key 僅由 `HOME_BUTLER_API_KEY` 環境取得，不放 CLI、URL 或 log。不在文件提供真 key。loopback 測試若環境含 key 會拒絕，避免秘密送到假收件器。逾時不重送同一筆，下個週期重新採樣；HTTP 回覆只認 `200 {"ok":true}`。前景模式 Ctrl-C 停止，勿同時啟動多個實例。

獨立 daemon 已有使用者授權的本機部署與自然回報驗證；重開機未登入仍未實測。repo 提供的是後續整理版原始碼，尚未替換現役 sender；重新建置會改變 cdhash，需獨立部署審查。見 [安裝、續接與回滾](macos_daemon/README.md)。不放入相機服務 checkout。

## 測試

`tests/test_macos_metrics.py` 以 fake psutil、實際 heartbeat Pydantic schema 與 pc_state record/snapshot 驗證相容；Sheet writer 完全阻止。loopback HTTP 測試驗證 path、null、無 key 與禁止 redirect。後端 CI 不必安装 psutil；真實本機採樣才需要。

## TCMb／TCMz 整合

`macos_temperature.py` 只讀 AppleSMC 的 TCMb／TCMz，無 sudo、憑證或網路。
名稱依 OSHI 的 CPU die average / maximum 定義；M6 mapping 未經 Apple 官方確認。
缺失／無效值保留 null，不能把 TCMb 代替 TCMz，亦不能稱作已驗證的 CPU/GPU 攝氏溫度。

Collector 外送獨立 `smc_temperature={tcmb_c,tcmz_c}`；CPU/GPU 溫度欄位維持 null。
後端只接受有限、>0 且 <=150 的數值或 null，拒絕 bool/string/額外欄位。
新溫度保留最多 24 小時記憶體歷史，不寫既有 Sheet 欄位，後端重啟即失去歷史。
Dashboard 分別顯示來源、限制及 unavailable。舊 sender 不一定接受新 schema，
不得只替換 collector；新的 binary 必須經明確的簽章／Keychain 信任審查。


## 記憶體壓力

`memory_pressure={level, pct?}` 為可選獨立欄位；level 僅 normal／warning／critical／null。
接收端先開放 pct：0–100 整數或 null；目前舊 collector 仍只送 level，待接收端部署後更新。
collector 唯讀 sysctl 的 **dispatch flags 1／2／4**，不是 XNU 內部 enum 的 0／1／2／3。
失敗／未知一律 null；不以 free RAM 百分比推估，不製造壓力測試、不執行 memory_pressure 工具。
原生 sender 嚴格白名單，後端只保留 bounded 24h 記憶體歷史，不新增 Sheet 欄位。
舊 Windows／Mac payload 缺欄位仍可接收；RAM 使用率欄位語義不變。

來源：[Apple XNU sysctl 轉換](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus_notify.c)、
[Activity Monitor 記憶體壓力](https://support.apple.com/guide/activity-monitor/actmntr34865/mac)。
壓力狀態與 Activity Monitor 的壓力意義一致，但不聲稱重現其圖形高度／百分比。
