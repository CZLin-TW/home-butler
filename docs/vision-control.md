# Vision control phase 2：離線協定

本階段已提供實際 loopback HTTP／WebSocket 控制鏈與 fixture 磁碟 writer。正式 app 僅有
VISION_CONTROL_ENABLED=1 的註冊入口，預設停用；即使啟用也使用空 registry、拒絕全部憑證。
沒有設定此開關、連線現役 mini 或產生正式憑證。既有 HA／PC agent 通道與家電路由不變。

預定資料路徑：瀏覽器的 Dashboard session → Dashboard BFF 的 status/edit 授權 →
獨立 HB vision service credential → 指定 mini 的 outbound vision session。
裝置與服務憑證各自限制 device ID、scope、到期時間，registry 預設空白，支援撤銷；
不得沿用家庭 API key 或將憑證交給瀏覽器。registry、session、去重紀錄目前只在記憶體，
僅支援單 process；重啟後需重新授權，不能宣稱持久 exactly-once。

## vision.v1

JSON 上限 32768 bytes；只接受固定欄位，拒絕 duplicate keys、非有限數字、未知動作。
`status.get`、`config.get` 需要 status；`config.replace`、`detector.configure` 需要 edit。
status 回應目前只有 synthetic adapter 健康／revision／模型選項／區域數，不代表真相機偵測結果。
不承載 preview、影像、任意 URL、相機憑證、檔案路徑、shell 或 HA 指令。

服務 command 包含 protocol、type=command、request_id、device_id、action、deadline、payload。
hub 注入每次連線的新 session_nonce 並轉成 type=request；result 必須匹配 request/device/session，
且通過該 action 的回應 schema。deadline 為 Unix 秒，最長 30 秒，部署端時鐘需同步。
所有可修改請求需要 expected_revision；ROI 設定與模型選項有各自 revision。
floor 端仍執行完整幾何驗證及 schema v1 → v2 migration；多 polygons 為聯集。

相同 request ID 與內容共用結果，不同內容拒絕。快取／queue 有界，滿載拒絕，不以提前丟棄
仍有效 ID 換取重送。session 斷線、權限到期／撤銷或執行期限跨過後，已送出的修改可能完成，
因此結果為 unknown，不自動重試。重新取得授權後先讀 revision／設定，再由使用者决定是否修改。
重連使用新 nonce，舊 session 回應不可完成新請求；跨重啟或新 session 不提供 exactly-once 保證。

## 未來啟用需要的明確授權

1. 指定 HB TLS host 作為 mini outbound 的接收端，以及唯一 device ID；只傳狀態與設定 metadata。
2. 決定 device credential 與 Dashboard service credential 的建立、保存、輪替、撤銷方式及管理者；
   不使用既有家庭共用 key，也不建立可公開呼叫的無驗證 API。
3. 授權註冊獨立 router、production 設定與指定 mini connector，再接真正的 config/model adapter。
   本階段的模型選項修改只改記憶體，不載入模型、不重啟任何服務。
4. 影像是另一條未實作媒體路徑。對外無 VPN 預覽需另核准 relay/TURN 接收端、影像範圍、
   短效授權／TTL／流量限制；不可用本 JSON 控制通道傳圖，也不錄影。
5. 真 C110、HA MQTT publish、常駐服務或公開部署均需另行授權，不由離線測試推論已成功。

核心協定檔 `vision_protocol.py` 與 floor-presence 的 `vision/control_protocol.py` 必須 byte-identical。
跨 repo 測試用明確指定的本機 checkout 路徑與假 transport，不能解析真憑證或使用 live service。

## 重跑離線驗證

```sh
python3 -m unittest discover -s tests -v
python3 scripts/test-vision-pair.py --floor-checkout /path/to/floor-presence
```

第二條需要同階段 floor checkout；不會連線網路或 production app。五項跨 repo 測試包含
shared schema 一致性、synthetic 狀態、修改去重／revision 衝突、幾何拒絕、撤銷與重連、
斷線中斷回 unknown。兩 repo 的各自單元測試另覆蓋 payload／權限／容量／期限邊界。

## Loopback milestone

`vision_integration.install_vision_routes` 已接到 main，預設 disabled。
`scripts/vision_loopback_fixture.py` 建立 ephemeral 127.0.0.1 listener，獨立假credential；
floor `connect_fixture` 只允許固定 loopback device 路徑，拒proxy/redirect、不自動重連或重送。
`FixtureFileAdapter` 只能使用新建的私有 temporary fixture 目錄，無 production path 參數；
atomic JSON replace、每次reload、CAS、fixture-only rollback，不載入模型。

完整三repo測試（需 uvicorn/websockets、Dashboard已安裝依賴）:

```sh
python3 scripts/test-vision-full-chain.py --floor-checkout /path/to/floor-presence \
  --dashboard-checkout /path/to/Dashboard --node /path/to/node
```

此測試啟動真 HTTP BFF → HTTP HB → outbound WebSocket device，測試JWT拒絕、
狀態讀取、兩client的revision競爭（200/409）、舊revision拒絕及adapter磁碟reload。
不啟動Next dev；不包含productionmedia。正式啟用候選詳見 [批准提案](vision-activation-proposal.md)。
