# Hub 2 光照等級

`switchbot_hub_light` 1.3.0 是 HA 的唯讀補充整合，支援 Core 2026.9.2。
官方 SwitchBot Cloud 原生 Hub 2 僅建立溫度、濕度實體；本整合將同一次狀態回應的
`lightLevel` 顯示為各 Hub 2 裝置下的「光照等級」。不修改 HA 內建程式，不讀憑證。內建備援查詢預設 60 秒；變化的即時更新來自 HA 內建整合的 SwitchBot 推送。

## 安裝與選取

先完成原生 SwitchBot Cloud 整合，確認 Hub 2 的溫度正常。
取得通過 CI 的完整 commit SHA，於 HA Terminal & SSH 執行：

```sh
curl -fL https://raw.githubusercontent.com/CZLin-TW/home-butler/COMMIT_SHA/homeassistant/install.sh -o /tmp/install-home-butler.sh
sh /tmp/install-home-butler.sh COMMIT_SHA switchbot_hub_light
sh /tmp/install-home-butler.sh COMMIT_SHA home_butler
ha core restart
```

HA「設定 → 裝置與服務 → 增加整合 → SwitchBot Hub 2 Light」選取要顯示光照的
Hub 2 原生溫度實體，例如客廳、主臥、次臥。選溫度只是識別同一個 Hub，不是用溫度推算光照。
設定選項可日後增減 Hub，或修改「備援更新間隔」為 60～3600 整數秒（預設 60）。保存後自動重載，不必重啟 HA；既有來源改名仍維持光照實體 identity。升級舊設定時自動使用 60 秒，請停用舊定時更新自動化。

## 數值、更新與限制

- 官方 API 的範圍為 **1～20 級**，數字越高代表越亮；不是 lux 或百分比。
- 以數值狀態條件建立 HA 自動化，例如依實際觀察設定「光照等級低於 5」。門檻需自己校準。
- 沿用原生 Cloud coordinator；本整合內建備援查詢預設 60 秒，取代家庭每分鐘更新自動化。變化的即時更新來自 HA 內建整合收到的 SwitchBot 推送（見下），資料仍經 SwitchBot 雲端。
- 無此欄位、非整數或超出範圍顯示未知；原生連線失敗或來源移除則 unavailable，不以 0 代替。
- 光照不冒充 illuminance／lux。Home Butler 1.4.0 可透過獨立 `light_level` 通道同步 1～20 級；FP2 原有 lux 通道不變。
- HA 核心更新若改變 SwitchBot runtime，需要跑框架測試再驗收；不偷偷退回獨立雲端查詢。

來源：[SwitchBot 官方 Hub 2 API](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/main/devices/hubs/hub-2.md)、
[HA SwitchBot Cloud](https://www.home-assistant.io/integrations/switchbot_cloud/)。
測試及家庭實際讀值見 [驗證紀錄](../docs/verification.md)。

## 推送通知（2026-10-25 起直接進 HA）

SwitchBot 的變化通知由 HA 內建的 SwitchBot Cloud 整合直接接收：HA 設了對外網址後，它自己向 SwitchBot 註冊
webhook，收到 Hub 2 的通知就更新溫度、濕度與光照（本整合讀同一份資料，所以光照同一秒更新）。
對外入口是家中主機的 Tailscale Funnel，只放行 HA 的 webhook 路徑；安裝、退路與注意事項在家中主機的
`~/Services/ha-webhook-relay/README.md`（不在 repo）。

v1.50.0～2026-10-25 曾經走 SwitchBot → HomeButler → HA 的「去重讀」提示；1.3.0 起本整合不再接收提示，
`home_butler` 1.10.0 也不再訂閱 Hub。本整合只剩下面的備援定時讀取，用來補漏接的通知，建議設 600 秒
（之後若有 8 台 Hub，60 秒會超過 SwitchBot 每天 10,000 次的額度）。

## 整合內備援更新（1.2.0／系統 v1.52.0）

HA「設定 → 裝置與服務 → SwitchBot Hub 2 Light → 設定」調整「備援更新間隔」。
預設 60 秒，可設定 60～3600 整數秒。初次載入先等待一個週期，不在啟動時立即查詢。
每台 Hub 只呼叫一次原生 coordinator，溫度、濕度、光照共享回應，不另外取得 Token／Secret。

每次請求結束後重新計算該 Hub 的備援期限；慢請求不重疊、不密集補跑。原生 coordinator 的排程、去抖與更新鎖保持原樣，
其他手動更新或外部自動化不在本整合的去重範圍，因此仍應停用舊更新自動化。

原生來源未載入時略過，下個週期重新解析來源；
整合卸載或設定重载會取消舊計時器及待執行工作。API 失敗仍按週期重試，不以零或假新值替代。
光照 attributes 新增 `poll_interval_seconds`、`last_poll_refresh_requested`。
最後請求時間不是設備量測時間，也不保證 API 已取得新值。實際完成仍受原生去抖與網路延遲影響。

家庭原自動化為「Hub 2 每分鐘更新感測資料」（ID `1789319755253`），
每分鐘更新三個原生溫度實體。安裝 1.2.0 後請停用（保留供還原），不要和整合計時長期並行。
實際家庭是否已停用，請看驗證紀錄，不能由程式版本推定。

若退回 1.1.0，還原整合備份並重啟後，再啟用原每分鐘自動化。
