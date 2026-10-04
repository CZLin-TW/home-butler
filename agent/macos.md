# macOS 電腦指標 collector（未部署）

`macos_metrics.py` 是獨立的唯讀 collector，不載入 Windows `agent.py`、vision、theater、HA、Hue 或家電設定，沒有 WebSocket 控制、auto-update 或背景安裝功能。

## 資料與限制

| 指標 | 來源 | 可用性 |
| --- | --- | --- |
| CPU 使用率 | psutil 1 秒取樣 | 0–100%，跨核心整體使用率 |
| RAM 使用率 | psutil virtual_memory().percent | OS available-memory 語意，未必等同 Activity Monitor 的 Memory Used |
| GPU 使用率 | AGXAccelerator / PerformanceStatistics / Device Utilization % | 可選；只接受有效 0–100 數值，未公開穩定契約、OS 更新可能失效；不以 renderer/tiler 數值代替 |
| CPU/GPU 溫度 | 無已驗證的可靠無特權來源 | 固定 null；Dashboard 顯示 unavailable，沒有假設 0°C |
| load average 1/5/15 分鐘、RAM 總量 | os.getloadavg / psutil | 只在預設本機 JSON 的 local_only；既有後端契約不接收／儲存，不顯示在卡片 |

沒有執行 sudo、powermetrics、特權 SMC helper，也不以 thermal pressure 假裝攝氏。CPU/RAM 取樣失敗會略過 heartbeat，不捏造零值；GPU 讀取失敗保留其餘指標。

## 安全本機試跑

Python 3.11+；在獨立 checkout 建 venv，依 `requirements-macos.txt` 安裝 psutil：

```sh
python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install -r agent/requirements-macos.txt
.venv-macos/bin/python agent/macos_metrics.py --ip 192.0.2.20
```

192.0.2.20 是文件用假 IP，只作離線驗證。預設單次採樣、只 stdout、不發網路；不會探測區網 IP 或自動讀取機器 hostname。預設顯示名稱 Mac mini，可用 `--hostname` 指定。這不是溫度監控已完成驗收。

## 正式接入前需核對

目前既有 `/api/computers/heartbeat` 使用家庭級 `HOME_BUTLER_API_KEY`，沒有僅能寫 telemetry 的獨立金鑰。**本次沒有讀取、建立、持久化或配置金鑰**。若選擇新建受限 telemetry key，須另外授權後端驗證與憑證部署，不能把 HA／語音 key 加進通用驗證。

先核對目標 home-butler HTTPS origin、Mac 的穩定 LAN IP（既有卡片主鍵）與金鑰供應方式，再由使用者授權送出。repo Windows 範例 origin 是 `https://home-butler.onrender.com`，不代表已核實目前正式目的地；collector 沒有預設外送網址。

傳出的白名單資料：IP、指定 hostname、CPU/GPU 型號、CPU/RAM/GPU 百分比、null 溫度與 null fah。沒有 process list、攝影影像、檔案、帳號名稱、序號或 thermal pressure。目的地只使用明確指定的 origin + `/api/computers/heartbeat`，不跟隨 redirect、不讀環境 proxy、不做 fallback；遠端限 HTTPS，HTTP 只允許 literal 127.0.0.1/::1 測試。

後端目前 24h / 1440 點記憶體＋既有「PC 監控歷史」Sheet；不增加欄位。5 分鐘未回報會離線；現有 health alert 預設約 15 分鐘可能 LINE 告警，所以一次正式測試也會留下卡片與告警資格。DHCP 換 IP 會新增卡、舊卡可能告警；部署前須核對穩定 IP，不在這個 collector 自動改 DNS／DHCP／VPN。

授權後可用 `--send --url <approved-origin>` 做一次送出，再以 `--watch --interval 60` 前景運行；key 僅由 `HOME_BUTLER_API_KEY` 環境取得，不放 CLI、URL 或 log。不在文件提供真 key。loopback 測試若環境含 key 會拒絕，避免秘密送到假收件器。逾時不重送同一筆，下個週期重新採樣；HTTP 回覆只認 `200 {"ok":true}`。前景模式 Ctrl-C 停止，勿同時啟動多個實例。

常駐 launchd 安裝、開機啟動、金鑰持久化、正式 heartbeat、GitHub push／main 部署皆未執行。後续若要常駐，需另外決定專屬部署目錄、單实例與 log rotation、金鑰保管、失敗重啟、啟停與撤回方式；不放入相機服務 checkout。

## 測試

`tests/test_macos_metrics.py` 以 fake psutil、實際 heartbeat Pydantic schema 與 pc_state record/snapshot 驗證相容；Sheet writer 完全阻止。loopback HTTP 測試驗證 path、null、無 key 與禁止 redirect。後端 CI 不必安装 psutil；真實本機採樣才需要。

## TCMb／TCMz 整合（本機準備，未發布）

`macos_temperature.py` 只讀 AppleSMC 的 TCMb／TCMz，無 sudo、憑證或網路。
名稱依 OSHI 的 CPU die average / maximum 定義；M6 mapping 未經 Apple 官方確認。
直接讀取 TCMb=46.05°C、TCMz=null。數值高低不作語義證據。



Collector now emits optional smc_temperature={tcmb_c,tcmz_c}; CPU/GPU temperature fields stay null. Backend accepts only finite numeric values >0 and <=150 or null, rejecting booleans, strings and extra nested fields. Bounded memory history retains up to24h; new temperatures are NOT persisted in existing Sheets and are lost on backend restart. Dashboard renders each named sensor and its source/uncertainty independently. The installed trusted sender rejects this new schema, so do not replace only the collector: a reviewed new signed sender and explicit Keychain trust migration/re-entry are required. Existing installed files remain unchanged.
