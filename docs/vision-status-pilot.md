# SQLite status-only pilot（保留替代實作）

目前 main 已改用 [既有 HB 驗證＋Sheets 快照](vision-sheets-pilot.md)，不需要第二套 service token 或 SQLite volume。
本頁描述保留的 SQLite 替代實作與測試，不是目前 main 的啟用步驟。

這是可供後續審批的程式碼，不是已啟用的服務。未修改 render.yaml、部署設定、現役環境、
正式憑證或相機。現有 render.yaml **沒有 persistent disk**；若沿用目前 SQLite provider，正式啟用前必須另行批准並配置
持久 volume、備份／復原流程、單 instance／worker，以及受信任 TLS reverse proxy。
沒有 volume 就不能把此 SQLite 當成持久撤銷紀錄；不得用 ephemeral DB 自動初始化或 fallback。
付費磁碟不是所有設計的唯一最低要求；既有 Sheets 能力與成本／一致性比較見
[儲存選擇審查](vision-storage-review.md)，本輪沒有實作或選定替代 backend。

## 最小權限

保留的 `vision_pilot.install_status_pilot` 已不由 main 呼叫；所有開關預設關閉。僅註冊既有
`/api/vision/v1/command` 與 outbound device WebSocket。`StatusOnlyHub` 在 dispatch 前
只允許 `status.get`，即使 `config.get` 在通用協定也是 status scope，pilot 仍拒絕。
沒有 config/edit/media 路由擴張，沒有 Dashboard → 相機直連或 HA 操作。
原 `VISION_CONTROL_ENABLED` 不再是 main 的啟用開關；原可注入 registry 的 factory 只留給離線 fixture。

此版本的 status payload 仍是既有 synthetic adapter 健康與 revision/model metadata，
不能用 pilot 連線成功宣稱真相機、真模型或真實 occupancy 已驗收。

## 持久 registry 與單 authority

SQLite 只存 SHA-256 token digest、record ID、kind(service/device)、device ID、固定 status scope、
到期時間及 revoked。每一次 identify/authorize/check 重新開啟唯讀 DB、驗證 schema、完整性與
檔案身分後授權；不快取 allow。缺失、損毀、替換、權限錯誤均拒絕，不重建、不 fallback。
撤銷用原 DB transaction 更新，重啟後仍有效；SQLite DELETE journal 避免 WAL/SHM 長存檔。
短寫入 transaction 可能暫時阻塞讀取；超過 .1 秒即拒絕，不沿用舊 allow。

DB 使用 canonical absolute path，直接父目錄必須由服務使用者擁有且 0700；DB 與
`<db>.authority.lock` 必須同 owner、0600、一般檔案、單 hard link、非 symlink。
鎖檔由離線 init 一次建立；installer 不建立資料夾／DB／鎖檔，也不更改既有權限。
單 authority 對專用鎖檔持有非阻塞 exclusive flock 至 shutdown。第二 authority 使用同一檔案
會拒絕，shutdown 關閉既有 device session 與鎖。macOS SQLite 與直接 flock DB 會衝突，
故不能把鎖改回 DB 本體。DB／鎖檔不可在線替換或刪除，復原需先停止所有 authority。

**這個鎖只涵蓋同一 filesystem 的同一 lock inode，不代表跨 Render instance／獨立 volume 的分散式互斥。**
必須固定單 instance、單 worker、禁止 autoscaling 與並行 rollout。程式檢查環境宣告只是 guard，
不能證明部署平台沒有用 CLI 覆寫 worker 或建立第二 replica。

## 後續批准後才可設定的 gates

- `VISION_STATUS_PILOT_ENABLED=1`
- `VISION_STATUS_REGISTRY_DB`：既有私有 DB 的 canonical absolute path
- `VISION_STATUS_SINGLE_AUTHORITY_ACK=1`
- `VISION_STATUS_TLS_PROXY_ACK=1`
- `WEB_CONCURRENCY=1`，若設定 `UVICORN_WORKERS` 也只能為 `1`

缺 gate／DB 時啟動失敗，不默默降級。TLS ACK 只是部署確認，**不實作也不證明 TLS**；
外部必須是有效憑證的 HTTPS/WSS，proxy-to-app 的私有路徑與 header trust 邊界也需審核。
不以客戶端任意 `X-Forwarded-Proto` 作 TLS 驗證。端點僅接受 Authorization Bearer，拒絕 query token。

## 離線 operator CLI

`scripts/vision_status_registry.py` 不會在服務啟動時自動執行。

```sh
python scripts/vision_status_registry.py init --db /approved/private/registry.sqlite
python scripts/vision_status_registry.py enroll --db /approved/private/registry.sqlite \
  --record-id service-rotation-id --kind service --device-id approved-mini \
  --expires-at UNIX_EXPIRY --secret-file /approved/private/service-token
python scripts/vision_status_registry.py revoke --db /approved/private/registry.sqlite \
  --record-id service-rotation-id
```

這是離線 registry 工具介面，不是正式 mini device secret 的預設保存方式。正式預設為
簽章固定的 Keychain broker 持有 token 並負責 WSS 認證，Python 不取得秘密；本輪已有 native 原始碼與 mock，由 native 自行產生受限 synthetic metadata，
沒有 Python producer IPC；尚未簽章、安裝，activation 維持停用。
不得將檔案 provider 當成 Keychain 失敗時的 fallback。

也可 `--stdin` 由 pipe／檔案重導輸入；拒絕互動 terminal，避免 echo。token 不接受 argv，
不輸出、不存進 DB；secret-file 必須 private、同 owner、非 symlink、大小有界。CLI 不生成或尋找
正式秘密。到期／撤銷後用新 record ID 與新 token 重新 enrollment；不提供解除撤銷或覆寫舊紀錄。
尚需批准正式 enrollment/operator 責任、secret 保存／rotation／backup 與 TLS endpoint。

## 驗證

`python -m unittest discover -s tests -p test_vision_pilot.py -v` 使用 temporary private DB 和
明顯 synthetic token，覆蓋持久撤銷、重新開啟、digest-only、雙 authority、缺失／損毀／替換、
權限／symlink／WAL拒絕、expiry、預設關閉／deployment guards、config/edit/media 拒絕、status 回覆
以及 device session 撤銷。測試不 import main，不執行家庭 API，也不接觸正式 secret。
