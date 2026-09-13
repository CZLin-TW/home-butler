# Hub 2 光照等級

`switchbot_hub_light` 1.1.0 是 HA 的唯讀補充整合，支援 Core 2026.9.2。
官方 SwitchBot Cloud 原生 Hub 2 僅建立溫度、濕度實體；本整合將同一次狀態回應的
`lightLevel` 顯示為各 Hub 2 裝置下的「光照等級」。不修改 HA 內建程式，不讀憑證。獨立使用仍不增加查詢；搭配 Home Butler 1.3.0 可由推送通知提前更新。

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
設定選項可日後增減 Hub；既有來源改名仍維持光照實體 identity。

## 數值、更新與限制

- 官方 API 的範圍為 **1～20 級**，數字越高代表越亮；不是 lux 或百分比。
- 以數值狀態條件建立 HA 自動化，例如依實際觀察設定「光照等級低於 5」。門檻需自己校準。
- 沿用原生 Cloud coordinator，預設每 10 分鐘查詢作為備援。家庭 HA 已另外設定下述每分鐘更新自動化；推送仍可提前讀取，資料仍經雲端。光照單獨改變也會更新。
- 無此欄位、非整數或超出範圍顯示未知；原生連線失敗或來源移除則 unavailable，不以 0 代替。
- 此次只新增 HA 感測器。不冒充 illuminance／lux 匯入 HomeButler 或 Apple Home；
  目前 Home Butler 的選定亮度通道要求真正的 lux，FP2 原有通道不變。
- HA 核心更新若改變 SwitchBot runtime，需要跑框架測試再驗收；不偷偷退回獨立雲端查詢。

來源：[SwitchBot 官方 Hub 2 API](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/main/devices/hubs/hub-2.md)、
[HA SwitchBot Cloud](https://www.home-assistant.io/integrations/switchbot_cloud/)。
測試及家庭實際讀值見 [驗證紀錄](../docs/verification.md)。

## Push 通知更新（系統 v1.50.0）

路徑：Hub 2 → SwitchBot Cloud Webhook → 既有 Render `/switchbot/webhook`
→ 既有 HA 主動 WSS 連線 → HA 原生 SwitchBot Cloud 驗證讀取 → 原光照實體。
一般照明仍由使用者手動關閉；夜燈規則在 HA 另建，本次不建立或執行燈光動作。

先部署後端，再安裝通過 CI 的 `home_butler` 1.3.0 與 `switchbot_hub_light` 1.1.0 並重啟。
已有 Home Butler 連線且已選取 Hub 2 時會自動訂閱這些 Hub，不需新金鑰或開放 HA 外網連線。
增減來源後訂閱隨下一份快照更新，通常立即、最遲約 30 秒。HA 整合可獨立使用，無 Home Butler
時仍保留原生每 10 分鐘輪詢，或另設的 HA 定時更新；舊後端沒有 hub_updates capability 時也不傳新欄位。

SwitchBot Webhook 無簽章。Render 僅接受格式、Hub 2 類型、等級與樣本時間合理的通知，
並只向該 HA 連線明確選取的裝置轉送 device ID 與接收時間，**不傳入 webhook 的光照值**。
HA 再以原生整合既有憑證讀取正式 API；不修改 coordinator.data、不讀出 token、不冒充照度。
這是「Push 提醒 + 驗證讀取」，不是完全免查詢的直接值推送。可避免偽造通知注入感測值。

同一 Hub 的通知在 Render 合併，最多每 3 秒送一次；HA 合併連續通知並限制主動更新間隔至少
10 秒，保留突發通知最後一筆所需的讀取。原生 coordinator 自己的去抖仍可能延後讀取。
通知到達後不必等待原本的 10 分鐘週期，但仍有雲端、WSS、API 與去抖延遲，不能承諾秒數。
每次事件更新會增加一次原生狀態查詢（溫、濕、光共用），須連同既有輪詢計入 API 額度。

Render 斷線不補播通知；HA 來源移除／整合卸載會清理訂閱及待執行讀取，不控制家電。
Render 離線期間仍有 HA 原生轮詢，但快速夜燈觸發會受影響。
光照實體 attributes 的 `last_push_received` 是 HA 收到提示時間；
`last_push_refresh_requested` 是已交付原生更新請求的時間，**不是設備量測時間，也不保證 API 有新值**。
光照值始終來自原生 coordinator 的正式 API 回應。

既有 HB 夜燈原本已使用相同 Webhook，不能把全部 HomeButler 感測都說成輪詢。
本次保留原接收網址與既有規則，避免搶占 SwitchBot 唯一 Webhook；日後將同一區域夜燈搬入 HA
時，應停用 HB 的對應規則，避免兩套自動化同時控制同一盞燈。

## 可選：每分鐘備援更新（家庭 HA 已設定）

2026-09-14 在 HA UI 建立「Hub 2 每分鐘更新感測資料」（automation ID `1789319755253`）。
每分鐘呼叫 `homeassistant.update_entity`，每台 Hub 只選一個原生溫度實體；
同一台的溫度、濕度與光照共享該次原生 coordinator 回應，不分別對三種數值查詢。

```yaml
alias: Hub 2 每分鐘更新感測資料
description: 每分鐘更新三台 Hub 2 的原生資料，溫度、濕度與光照共用同一次查詢；保留推送更新。
mode: single
triggers:
  - trigger: time_pattern
    minutes: "/1"
conditions: []
actions:
  - action: homeassistant.update_entity
    data:
      entity_id:
        - sensor.hub_2_zhu_wo_temperature
        - sensor.hub_2_ci_wo_temperature
        - sensor.hub_2_ke_ting_temperature
```

其他家庭請改成自己的原生溫度實體，勿選本補充整合的光照實體作為強制讀取目標。
在 HA「設定 → 自動化與場景」可調整時間模式，例如 `/2` 為每兩分鐘。
停用這條自動化即回到 Push 加原生預設輪詢，無需修改程式或重啟 HA。
這是家庭設定，不修改整合的預設週期，也不新增 HomeButler 背景查詢。

Push 與定時更新共用原生 coordinator 的更新鎖與去抖機制，但不保證相近事件永遠只產生一次請求。
每分鐘查詢不代表設備每分鐘必有新量測；API 額度仍須計入定時與事件讀取。
