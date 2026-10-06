# Vision status pilot：既有 HB 驗證＋Sheets 授權快照

本輪只交付預設停用的程式碼及 fake-reader 測試，未建立或讀寫真 Sheet、未取得秘密、未部署。
main 改用 `vision_sheets_api.install_sheets_status_pilot`；不再需要第二套 vision service-token
registry 或 SQLite volume。舊 `vision_pilot.py`／SQLite CLI 保留為已測試的替代方案與 fixture，
不是目前 main 的必要依賴。不得以此次變更擴張正式登入修復、edit、media 或 HA 控制。

## Dashboard 與 mini 授權分離

Dashboard server 使用既有 `auth.verify_api_key` 所驗證的 `X-API-Key`，並從已驗證 JWT 產生：

- `X-Dashboard-User`：精確 LINE user ID。
- `X-Dashboard-Role`：必須是 `member`；缺失、未知或 `kid` 都拒絕，避免 kid JWT 的家長 ID 變成成人權限。
- `X-Dashboard-Session-Expires`：整數 Unix 秒，必須仍有效；命令 deadline 不得超過此值，回應前再驗證。

BFF 不可複製瀏覽器提供的同名 header。HB 只信任通過既有 API key 的 server 轉送身分，
並在自己的有效快照精確核對啟用家庭成員與明確 vision grant；不存在、重複、停用或無 grant 均拒絕。
既有家庭 key 本身不代表此 actor 有 vision 權限。HB 不收 raw Dashboard JWT。

`GET /api/vision/v1/access` 回 `{capabilities:{status,preview,edit}}`，三項明確布林值。
`POST /api/vision/v1/command` 仍使用 vision.v1 envelope，但只 dispatch `status.get`；
`config.get` 即使屬通用 status scope 也拒絕。preview/edit grant metadata 不會新增相應路由或能力。
key 錯誤回 401；actor／grant／role／expiry 拒絕回 403；快照不可用回 503 registry_unavailable。
所有路由錯誤與成功回應均 no-store。request 完成時重查 actor，撤權後晚到結果不可交付。

Mini 繼續以獨立 device Bearer 主動連 `/api/vision/v1/device`；hello/welcome/nonce/request/result
協定未改。只允許 device digest、status scope、device ID、expiry/revoked；不接受 household key，
即使誤把家庭 key 的 digest 登記成 device 也拒絕。每個 session 綁定完整 credential record，
同 record ID 換 digest／scope／expiry 也必須斷開重驗。device 撤銷／移除會斷開 session，
actor 撤銷會終止其 pending result；unknown 不重送。

## Shared snapshot 與撤銷時效

一個 authority 共用一份快照；啟動須成功讀取／驗證才能 ready。每 30 秒啟動一次 refresh，
以 monotonic 的**開始讀取時刻**計算 freshness，60 秒為硬上限；系統校時不延長期限。
正常狀況仍存在 refresh 間隔造成的撤銷延遲，不宣稱即時撤銷。讀取錯誤、schema 錯誤或最多
5 秒 refresh timeout 一經確認即清除 allow，不續用前一快照，也不延長原到期時間。

reader singleflight；timeout 後仍在執行的底層 IO 未結束前不開始第二次 read，晚到資料丟棄。
shutdown 封閉 snapshot 並取消 publish 流程，晚到成功不得復活授權。heartbeat／session 檢查
只讀記憶體快照，不每秒請求 Sheets。production reader 使用獨立 readonly Google session，
一次 values:batchGet，連線 2 秒／讀取 3 秒、auth refresh 3 秒、無 redirect 或 household wrapper 重試。
30 秒穩定 cadence 約兩次 batch read／分鐘，不包含其他家庭功能的既有用量。

所有 members/grants/devices 先完整驗證才一次發布到本程序。這是**本機發布一致性**，
不是宣稱 Sheets 跨分頁編輯有交易隔離；正式 operator schema／變更流程仍需驗證。

## 尚未建立的 Sheet schema 與 source 邊界

production source 只讀既有 `SPREADSHEET_ID`／`GOOGLE_CREDENTIALS` 設定的 spreadsheet，
read-only scope；不呼叫 get_or_create、不新增表或欄位、不寫入。正式授權／分享權限仍需批准。

| 分頁 | 欄位 |
| --- | --- |
| 家庭成員（既有） | 只投影 `Line User ID`、`狀態`；狀態為啟用／停用 |
| Vision Grants（提案，未建立） | `user_id,status,preview,edit`；布林文字只能 TRUE／FALSE |
| Vision Devices（提案，未建立） | `record_id,digest,device_id,scopes,expires_at,revoked`；scopes 只接受 status，expiry 為 Unix 秒 |

兩張 vision registry 表不得含 raw token；未知 header、重複 ID／digest、錯誤型別與超量（每類最多
256 列）會令整份快照失效。family 身分資料與 grants／device rows 分開驗證，不把 grant 推導自姓名。

## 單 authority 與啟用 gates

仍預設關閉。候選 gates 為 `VISION_STATUS_PILOT_ENABLED=1`、
`VISION_STATUS_SINGLE_AUTHORITY_ACK=1`、`VISION_STATUS_TLS_PROXY_ACK=1`、`WEB_CONCURRENCY=1`，
若設定 `UVICORN_WORKERS` 也只能 1。`VISION_STATUS_AUTHORITY_LOCK` 指向既有 private ephemeral lock：
canonical absolute path、owner 0700 父目錄、owner 0600 regular single-link 檔、無 symlink。
installer 只開既有 lock，不建立檔案或修改權限；不再需要 DB path 或 paid disk。

flock、PID／inode 檢查只防同 filesystem 的重複 authority 或鎖檔替換，不是跨 Render replica 的選主。
平台仍須單 instance、單 worker、禁止重疊 rollout；ACK 不是實際部署驗證。TLS ACK 也不代替
有效 HTTPS/WSS、受信任反向代理與 server-only key 配置。此次沒有改 Render 設定。

## Fake-only 驗證入口

`create_sheets_app(reader,api_key_verifier,clock=...,monotonic=...)` 建立隔離 app；reader 為 async，
回 `{members:[...],grants:[...],devices:[...]}`，欄位如上但布林是 Python bool、scopes 是 `['status']`。
Production verifier 預設既有 verify_api_key；fixture verifier 只能使用明顯假 key，錯誤拋 HTTPException。
`app.state.vision_snapshot.refresh(force=True)` 僅供測試明確模擬新資料；HTTP 不提供 refresh／寫表入口。

`python -m unittest discover -s tests -p test_vision_sheets.py -v` 覆蓋錯 key／kid／expiry、無 grant、
startup failure、30/60 秒邊界、refresh error、singleflight timeout、晚到與 shutdown、撤權、digest 輪替、
錯 device key、無每 heartbeat IO、private ephemeral lock 與 source schema。所有 reader 都是假資料。
