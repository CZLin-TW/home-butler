# Vision 正式啟用提案（未執行，2026-10-05）

這是供使用者批准的具體候選方案，不是已啟用設定；本輪只有隔離 loopback/synthetic 驗證。
現有程式只允許 fixture connector，批准後仍需實作正式 TLS connector、憑證 provider、媒體 sender，
重新審查及測試，不能將測試開關視為 production rollout。

## A. 控制面：先 status-only，再開修改

- 接收者：既有 Render `home-butler` 服務。候選完整端點為
  `wss://home-butler.onrender.com/api/vision/v1/device` 與
  `https://home-butler.onrender.com/api/vision/v1/command`。
  這個 host 來自 Dashboard 程式的預設值；本輪未讀 production env，也未呼叫此端點。
  批准前由擁有者確認它就是目前要使用的 production host。
- 裝置代號候選 `floor-mini-01`；只准此裝置的 status scope，驗收後才另外批准 edit。
  傳送健康狀態、模型選項、revision、normalized ROI 與區域名稱；不傳相機 URL、帳密、影像或人臉資料。
  區域名稱由使用者自行命名，不能保證自由文字不含私人資訊，批准時須納入資料範圍。
- 建立與保存責任：使用者批准後，由部署操作者在 mini 本機以密碼學亂數建立 device token，
  直接寫入專用 Keychain，不經聊天、repo、log或export。另建獨立 service token，直接交付 Dashboard
  hosting secret；HB只接收其digest與授權metadata。這些步驟本輪均未執行。
- Mini credential：新增獨立 macOS login Keychain item，service 候選
  `com.floorpresence.vision-control`、account `floor-mini-01`；只供簽章固定的 native broker 讀取，由 broker 負責固定 HB host 的 WSS 認證，
  本輪 native 原始碼／mock 由 native 自行產生受限 synthetic metadata，未實作 Python producer IPC；
  尚未簽章、安裝，activation 維持停用。明文 device token 檔不是預設或 fallback。
  不沿用相機 broker、家庭 API key 或其他 Keychain item。新增 item／ACL／簽章需另行批准；本輪未操作。
- HB：只保存 token digest、device allowlist、scope、expiry/revoked metadata；單 worker registry。
  第一輪設定 24 小時到期、可獨立撤銷的 device token。SQLite enrollment／持久 revocation 已有隔離程式與測試，尚未部署；儲存選擇仍待核准，
  不預設購買 Render disk。既有 Sheets 替代方案的限制見 [儲存審查](vision-storage-review.md)。
- Dashboard：另發 service token，只准該 device 的 status，存在 hosting server secret；瀏覽器不取得。
  BFF 仍必須驗證 session 與個別使用者 vision grant。edit 開放時才增 grant／scope；不借用家庭 key。
- 不改防火牆、不開 mini inbound port、不建 tunnel。Mini 主動連 HB；目前斷線後由操作者明確重連，
  修改結果 unknown 時先讀 revision，不自動補送。HB 重啟不保證 exactly-once；不得增加 workers。
- 回滾：關閉 `VISION_STATUS_PILOT_ENABLED`、撤銷兩種獨立 credential、停止新 connector；不動既有相機／HA服務。
- 涉及三項 rollout：先 HB repo 部署正式 registry／TLS signaling與預設關閉的路由，再 Dashboard repo
  部署 server transport／個人grant，最後 mini floor-presence connector安裝與手動啟動；
  若需launchd另批。每步均需明列待部署SHA與回滾SHA，先status-only驗收再另批edit。
  本提案尚未批准 push、部署或 service 安裝；正式 rollout 另列 SHA 與變更清單後批准。

## B. 媒體面：Cloudflare Realtime TURN 候選

只為需要外網預覽時批准，不作為控制面啟用的前提。候選為 TURN 中繼，不使用 SFU 或錄影服務。
Mini → 使用者瀏覽器的 WebRTC DTLS-SRTP media 經 TURN 轉送；TURN 可見連線 IP、時序與流量，
瀏覽器為影像接收者。HB 只轉送獨立 signaling／session metadata，不將影像塞入 JSON 控制協定。

- 接收端：`turn.cloudflare.com`（優先 UDP 3478，必要時 TLS TCP 443）；server mint API
  `https://rtc.live.cloudflare.com/v1/turn/keys/{TURN_KEY_ID}/credentials/generate-ice-servers`。
- 長效 TURN API key 只存 HB hosting secret；每次觀看產生短效 credential，候選 TTL 10 分鐘。
  每次觀看最多5分鐘、閒置30秒關閉、同時1位觀看者；授權撤銷時本地 sender 也立即關閉。
  provider TTL 不等同應用程式精確停止計時，必須另實作 session lease。
  官方支援短效 credential 與撤銷：[credential 文件](https://developers.cloudflare.com/realtime/turn/generate-credentials/)。
- 建議先 synthetic TURN 測試，之後才獨立批准 C110 私人影像經上述接收端傳給有 preview grant 的本人。
  無音訊、無錄影、無雲端分析；720p、15 fps、上限1 Mbps 為初始候選，須以實際 encoder 驗證。
- 官方2026-10-05查核：TURN／SFU 共用每月首1000 GB免費量，超額 egress USD0.05/GB。
  [官方定價](https://developers.cloudflare.com/realtime/sfu/platform/pricing/)。
  以1 Mbps每天30分鐘估算單向媒體約6.75 GB/月，另加封包、重傳及雙向流量；不能當保證帳單。
  若免費量已用盡，純該方向估算約USD0.34；建議帳單預算USD5/月，應用程式最多10 GB/月便拒開新session。
  provider 帳單告警不是硬停用保證，預算／應用計量亦尚未實作。既有 hosting 費用不在此數字內。

## 需要使用者確認的批准範圍

先確認 A 的 host、device ID、status-only、24小時兩種獨立 credential 與各自保存位置；
不把此批准擴成 edit、相機、媒體或部署。B 必須另確認 Cloudflare 帳號／付費權限、接收端、
synthetic 外送範圍及USD5/月預算；真影像要再次明確選擇其資料與接收者。
本輪沒有建立任何帳號、key、Keychain item、正式 token、外部 session 或付費資源。
