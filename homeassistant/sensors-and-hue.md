# HA 感測器與 Hue 統一（home_butler 1.4.0／系統 1.51.0）

此版本提供切換機制，家庭實際啟用及驗收結果另記於 `docs/verification.md`。
未切換的設備保持原路徑；不要只憑已 push 或 HA 已更新就判定遷移完成。

## 感測器

HA → 既有 outbound WSS → HB 即時快照 → Dashboard、查詢及除濕控制。
HB 每五分鐘從同一份快照取一筆歷史，保留原 Sheet 名稱、補償、24 小時曲線與歷史回填。
不新增 HA 感測輪詢：沿用原生整合、Hub Push 刷新及家庭現有每分鐘更新自動化。

在 HA「設定 → 裝置與服務 → Home Butler → 設定」填寫「感測器配對」。例如：

```json
{
  "SwitchBot Hub 2 客廳": {
    "temperature": "sensor.hub_2_ke_ting_temperature",
    "humidity": "sensor.hub_2_ke_ting_humidity",
    "light_level": "sensor.guang_zhao_deng_ji"
  }
}
```

以上是格式示例，實體 ID 請用 HA 當前實體確認；主臥、次臥、CO₂ 也逐台配對。
可省略沒有提供的種類。`temperature` 要 °C、`humidity` 要 %、`co2` 要 ppm；
`light_level` 僅限 `switchbot_hub_light` 的整數 1–20 級。FP2 lx 仍走原存在／照度通道。
不要把等級換算成 lux。HA registry ID 固定來源，改顯示名或實體 ID 不會換感測器；
刪除後建立的同名實體不會自動冒充原來源，需要重新配對。

HB 使用原 Sheet「溫度補償／濕度補償」，每次投影只套用一次；HA 不再加一次相同補償。
如果 HA 已做了同樣校正，先調整設定避免雙重校正。HA last_updated 是狀態更新時間，
WSS received_at 是同步時間，均不宣稱是新的物理量測時間。
失聯／超過 90 秒無快照／實體 unavailable／單位錯誤，都回 null；不回填舊數值或零。

確認 HA 配對快照後，在 Render 設定：

```text
HOME_ASSISTANT_SENSOR_NAMES=["SwitchBot Hub 2 客廳","SwitchBot Hub 2 主臥","SwitchBot Hub 2 次臥","SwitchBot CO2"]
```

只填實際完成配對的名稱。此清單明確停止那些感測器的 HB 雲端輪詢、手動查詢及夜燈光照讀取。
HA 斷線不會自動切回 SwitchBot API；格式錯誤也不會重新開啟直接讀取。
既有 HB 夜燈若仍啟用，改以已驗證的 HA 光照快照變更觸發；unsigned webhook 只觸發 HA 刷新。

## Hue

HB → WSS → HA `home_butler` adapter → HA 原生 Hue V2 已配對的連線 → Hue Bridge。
HA 本機與 Apple Home 控制仍直接走原生 Hue，不需經 Render。
adapter 使用 aiohue 4.9.0 的公開 `request`（讀取）及 `create_request`（單次寫入）介面；
不讀取／複製 Hue 金鑰、不新增 Bridge IP 或 HTTP session、不修改 HA core。

同一設定頁勾選允許 HB 控制的 Hue 房間／區域。清單只從已載入的原生 Hue V2 取得。
保留原 grouped_light UUID，Sheet 的區域別名及燈光提醒目標不需重建。
設定好後在 Render 設定 `HOME_ASSISTANT_HUE_ENABLED=true`。

功能範圍：開關、亮度、普通／動態場景、smart_scene 啟用／停用、燈具所宣告的效果
（包含有支援時的 timed_effects 日出）、alert／signaling 通知。
Hue App 的「喚醒自動化」不等於可呼叫場景，本 adapter 不讀取或編輯 Hue App 自動化。
保留硬體支援清單與部分燈具不支援的提示，不虛構所有燈都可用日出。

通知交給 Bridge 原有 alert／signaling 行為，不另建「幾秒後恢復舊亮度」計時器，避免蓋掉人工操作。
沒有任意 HA service、URL、單燈或未選區域控制入口。場景必須屬於選定區域。
每筆命令具期限、request ID 去重，整批先驗證；逾時／斷線／部分執行回 unknown、不重送。
未知結果不改成另一種 Hue payload 再試一次。連線重建不保留待執行佇列。

待辦仍由 HB 判定是否到期；新增每分鐘燈光提醒工作，只把固定 breathe 與區域 UUID 送 HA，
不傳待辦文字／成員資料。開啟 HA Hue 後，舊 `/api/todos/light-reminders` 固定回空，
讓既有 PC Agent 不再另發一份提醒。PC／Theater 工作保留。

## 部署、驗收與還原

1. 先部署後端且確認 CI（包含 Linux 真 HA framework）通過；切換環境變數先保留未啟用。
2. 以 `install.sh <完整 commit SHA> home_butler` 更新 HA，重啟後設定感測配對及 Hue 區域。
3. 從 owner `/api/home-assistant/observations` 確認 environment 的配對／值／可用性，以及 hue_available。
4. 再啟用 Render 的兩個切換設定。Dashboard 照明連線來源會顯示 Home Assistant。
5. 對照 HA、Dashboard 的溫濕度／CO₂／光照，確認歷史續接；測試 Hue 開關、亮度、場景、效果、通知及人工調光。
6. 核對 HA／HB／Hue App 相同目標的自動化，只保留一個執行者。此版本不會擅自刪除任何規則。

要還原路徑，先將 Hue 設為 false、感測名稱清單還原舊值，再視需要還原程式／HA 備份。
HA 安裝腳本會保存原整合副本；不要在切換旗標仍啟用時先卸載整合，否則對應裝置會如預期顯示未知。
還原 Hue 前確認 PC Agent 原 Hue 配對仍可用；不要同時啟用兩套提醒工作。
