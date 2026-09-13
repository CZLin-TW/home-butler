# Hub 2 光照等級

`switchbot_hub_light` 1.0.0 是 HA 的唯讀補充整合，支援 Core 2026.9.2。
官方 SwitchBot Cloud 原生 Hub 2 僅建立溫度、濕度實體；本整合將同一次狀態回應的
`lightLevel` 顯示為各 Hub 2 裝置下的「光照等級」。不修改 HA 內建程式，不讀憑證、不增加 API 查詢。

## 安裝與選取

先完成原生 SwitchBot Cloud 整合，確認 Hub 2 的溫度正常。
取得通過 CI 的完整 commit SHA，於 HA Terminal & SSH 執行：

```sh
curl -fL https://raw.githubusercontent.com/CZLin-TW/home-butler/COMMIT_SHA/homeassistant/install.sh -o /tmp/install-home-butler.sh
sh /tmp/install-home-butler.sh COMMIT_SHA switchbot_hub_light
ha core restart
```

HA「設定 → 裝置與服務 → 增加整合 → SwitchBot Hub 2 Light」選取要顯示光照的
Hub 2 原生溫度實體，例如客廳、主臥、次臥。選溫度只是識別同一個 Hub，不是用溫度推算光照。
設定選項可日後增減 Hub；既有來源改名仍維持光照實體 identity。

## 數值、更新與限制

- 官方 API 的範圍為 **1～20 級**，數字越高代表越亮；不是 lux 或百分比。
- 以數值狀態條件建立 HA 自動化，例如依實際觀察設定「光照等級低於 5」。門檻需自己校準。
- 沿用原生 Cloud coordinator，通常每 10 分鐘查詢；並非即時本地感測。光照單獨改變也會更新。
- 無此欄位、非整數或超出範圍顯示未知；原生連線失敗或來源移除則 unavailable，不以 0 代替。
- 此次只新增 HA 感測器。不冒充 illuminance／lux 匯入 HomeButler 或 Apple Home；
  目前 Home Butler 的選定亮度通道要求真正的 lux，FP2 原有通道不變。
- HA 核心更新若改變 SwitchBot runtime，需要跑框架測試再驗收；不偷偷退回獨立雲端查詢。

來源：[SwitchBot 官方 Hub 2 API](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/main/devices/hubs/hub-2.md)、
[HA SwitchBot Cloud](https://www.home-assistant.io/integrations/switchbot_cloud/)。
測試及家庭實際讀值見 [驗證紀錄](../docs/verification.md)。
