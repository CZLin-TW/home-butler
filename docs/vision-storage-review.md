# Status-only 儲存選擇審查（2026-10-06，未啟用）

**不應把新增付費 Render disk 說成唯一最低要求，也不應立即購買。** HB 已有 Google Sheets
持久化能力；SQLite 是目前已實作、已測試的 pilot provider，並非唯一可行資料格式。
若維持「每次授權讀最新狀態、撤銷不依賴舊 allow 快取」的現有要求，SQLite 加持久儲存是
工程變更最少、延遲較可預測的候選。若首要目標是避免新增 hosting 資源，建議先另外批准
一個僅用 fake Sheets 的 registry 可行性驗證，再決定儲存；不能直接將既有家庭 Sheets wrapper
插入目前每秒授權路徑。本輪沒有實作替代 provider、建表、購買資源或啟用任何設定。

## 唯讀證據

| 來源 | 能證明的現況 |
| --- | --- |
| [Readme.md](../Readme.md#常駐資料與費用) | HB 以 Google Sheets 保存家庭資料、規則與歷史，搭配程序內快取；單 process／worker |
| [requirements.lock](../requirements.lock) | 已鎖定 gspread 6.2.1，不必為 Sheets 再引入新 SDK |
| [backend-guide.md](backend-guide.md) 的每日推播／效能／部署段 | 系統狀態 Sheet marker 跨 Render 重啟保存；Sheets 讀寫無跨請求交易，程序鎖不涵蓋其他程序或手動編輯 |
| [sheets.py](../sheets.py) | 既有 wrapper 有 GET 重試、request-context 資料快取與批次寫入；不是 vision credential registry 或分散式 lease provider |
| [render.yaml](../render.yaml) | repo 宣告一個 Python web service，未列 disk 或 database；不能由此推論 live 方案、帳單或 console 設定 |
| [vision_api.py](../vision_api.py)、[vision_hub.py](../vision_hub.py) | device WS idle 每秒重查 session，dispatch／receive 等也查授權 |
| [vision_pilot.py](../vision_pilot.py) | 已完成 SQLite digest provider、持久 revoke、每次授權新讀取、同 filesystem 專用鎖 |

未讀取環境變數值、真實 Google Sheet、hosting 帳號或 Keychain；上述結論僅源自程式碼與公開文件。

## 兩種候選的差異

| 面向 | 沿用 Sheets 技術的新 registry provider（未實作） | 已實作 SQLite＋持久 volume |
| --- | --- | --- |
| 新增基礎設施 | 可避免 HB 本機持久 disk；仍需批准專用 registry 資料位置、最小分享權限與 schema | 需批准持久 mount、私有權限、備份與 operator 流程 |
| 一致性 | 單次 batch 可原子套用；讀取後再寫入不是跨請求 transaction／CAS。人工編輯、重複 ID、列移動、舊快照還原需額外防護 | 單 DB transaction、unique constraints 與固定 inode 可直接使用；仍要阻止舊備份恢復撤銷前狀態 |
| 撤銷 | 欲沿用目前語意就必須讀新資料並在讀失敗時拒絕；新增 allow TTL cache 必須明列最長撤銷延遲並重新批准，不能宣稱立即撤銷 | 每次查 DB；device idle 下次檢查約 1 秒，加上 request／response 邊界檢查，並非零延遲保證 |
| 故障 | quota、網路、timeout、缺表、schema 錯誤都應 deny；不得用最後一次 allow 當 fallback | missing／corrupt／權限／鎖失敗均 deny；部署磁碟不可用時服務也會受影響 |
| 多 instance | 共用表不等於共用 authority，既有 RLock 仍只在本程序；不可藉此解除單 instance 限制 | flock 只涵蓋同 filesystem 的同一鎖檔；不能跨獨立 volume 選主 |
| 工程成本 | SDK 現成，但 auth freshness、quota budget、撤銷寫入回讀、禁用 stale cache、重啟及並行測試仍需新增 | provider 已有測試；主要缺正式儲存、TLS、憑證及部署核准 |

Google 官方列出一般讀取 quota 為每 project 每分鐘 300 次、每 user／project 每分鐘 60 次，
service account 按單一帳號計。依目前每秒 idle check 推算，一條 device WS 就可能約佔 60 次／分鐘，
尚未加入握手、狀態請求和原家庭功能。因此「registry 只有幾列」不能推論呼叫量足夠；實際帳號 quota
及流量本輪未查。官方也說單次更新原子性不代表多次讀寫交易。
[Google Sheets API limits](https://developers.google.com/workspace/sheets/api/limits)。

## 費用與部署限制

Google 文件目前說標準 Sheets API 使用沒有額外費用，並提到 2026 年稍後的超 quota 計费規劃。
不能把它寫成永久免費，也未核對使用者是否適用其他配額／帳單條件。
[官方用量與定價說明](https://developers.google.com/workspace/sheets/api/limits#pricing)。

Render 文件說 persistent disk 只能附加到 paid service；預設 filesystem 為 ephemeral。
附 disk 後不支援多 instance 擴展，部署時會先停舊 instance，因此失去 zero-downtime deploy。
這會影響整個既有 HB 的部署可用性，而不只是 vision registry。
[Render persistent disks](https://render.com/docs/disks)。

本輪未核對現役 Render tier、既有費用、可用額度、容量或最終報價；不提供猜測月費。
需確認方案、磁碟容量與實際增量費用後才可購買。[Render 公開定價](https://render.com/pricing)。

## Mini 憑證預設：簽章固定的 Keychain broker

正式 device credential 的預設設計是獨立 Keychain item＋簽章固定、最小權限的 native broker，
由 broker 讀取秘密並負責固定 HB host 的 WSS 認證。Python/status producer 只透過受限 IPC 提交
allowlisted status 資料，不取得 token。明文 device token 檔不是預設部署方法；目前檔案讀取介面
只是測試／離線 enrollment 工具能力，不代表已批准以檔案保存正式 device secret。

repo 的 [macOS telemetry sender](../agent/macos_daemon/README.md) 提供固定簽章／精確 ACL 的既有
設計證據，但它是另一個功能，不能沿用其 binary、Keychain item、ACL 或秘密。Vision broker
尚未實作或驗收；新 item、簽章及 ACL 均需另批，不允許通用 Python/shell key reader、廣泛 ACL
或失敗時改讀明文檔。Keychain 解鎖／登入前可用性與版本升級信任也必須另外測試。
