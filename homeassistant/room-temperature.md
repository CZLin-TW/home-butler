# 空調室溫配對

`ac_room_temperature` 1.0.0／系統 v1.47.0 是獨立 HA 本機整合，將一台原生 SwitchBot Cloud
空調與一個溫度感測器合成 climate，供 HomeKit Bridge 顯示及控制。
SwitchBot 紅外線空調不提供 current_temperature；HA 2026.9.2 HomeKit Bridge 缺值時保留 21°C 初始值。
本功能提供實際感測器讀值，不做自動調溫、回饋補償、開關機規則、重試或覆寫原生實體狀態。
沒有 Render／金鑰依賴；控制直接呼叫 HA 原生 climate，SwitchBot Cloud 本身仍需要網際網路。

## 安裝與初次設定

在 HA Terminal 使用通過 CI 的完整 SHA（只安裝本配對整合，不動既有 home_butler）：

```sh
curl -fL https://raw.githubusercontent.com/CZLin-TW/home-butler/COMMIT_SHA/homeassistant/install.sh -o /tmp/install-home-butler.sh
sh /tmp/install-home-butler.sh COMMIT_SHA ac_room_temperature
```

1. 重啟 HA，設定 → 裝置與服務 → 新增整合 →「空調室溫配對」。
2. 選原生 SwitchBot Cloud 空調，以及該房間的溫度感測器；每台各新增一次。
3. 新實體名稱預設為「原空調名稱（室溫）」。目前溫度來自 sensor；目標、模式、風速跟隨原生 AC。
4. HA HomeKit Bridge 匯出這些配對 climate，排除對應的原生 climate，避免重複。
   保留原生實體給 HA 自動化與 HB，**不要更改 Render 名稱清單或 Home Butler 的原生空調選取**。
5. 首次改用配對實體會建立新的 HomeKit 配件；Apple Home 房間、場景與自動化需重新確認。
   之後更換 sensor 不改 climate 的 unique ID／entity ID，不必重新配對。

感測器可來自任何 HA 整合，須為已登記的 sensor、temperature device class、°C／°F 單位。
預設搭配同房間的 SwitchBot Hub 2；也可手動選別款溫度感測器。

## 日後手動更換 Sensor

HA → 設定 → 裝置與服務 →「空調室溫配對」→ 對應空調的「設定」→「室溫感測器」→ 選新來源 → 傳送。

保存後自動重載，不需修改程式、重新啟動 HA 或重新配對 Apple Home；保存及感測事件不發送空調指令。
這只改 Apple Home／配對 climate 的室溫來源，不改 Dashboard 的環境感測卡片或 HB 空調控制目標。

2026-09-13 家庭設定已完成：客廳、主臥、次臥均搭配同房間的 Hub 2「溫度」。配對實體分別為
`climate.ke_ting_kong_diao_shi_wen`、`climate.zhu_wo_kong_diao_shi_wen`、`climate.ci_wo_kong_diao_shi_wen`。
HomeKit Bridge 已改匯出這三個實體；三個原生空調僅從橋接排除，仍供 HA 自動化與 Home Butler 使用。

## 可用性與維護

- 以 registry ID 固定來源；改 entity ID 仍追蹤同一來源，刪除後建立同名實體不會自動接管。
- sensor 刪除後可從設定重新選；原生 AC 若被重新建立，需重新建立配對。
- sensor unknown／unavailable、錯誤單位或非有限值時，配對 climate unavailable，避免 HomeKit 仍把預設／舊室溫當正常。
  此時可直接透過原生 HA 空調或 Dashboard 控制；原生空調不受配對可用性影響。
- 資料新鮮度依原感測整合的可用性，不以 last_changed 猜測斷線，因穩定溫度可長時間不變。
- 室溫不是冷氣內部量測；IR 電源與壓縮機仍無實體回讀，HomeKit 的冷暖運轉顯示只是推定。
- 原生來源只限 switchbot_cloud，不能指向配對實體本身或 HomeKit 匯入實體形成迴圈。

離線驗證見 `homeassistant/tests/test_room_temperature.py`，使用真實 HA 框架搭配假的 native service。
需覆蓋平台生命週期、換 sensor 保留 ID、感測事件不控制、失聯與恢復、攝華氏轉換、來源改名／刪除、
命令只發一次且不預先更新成功狀態。正式安裝結果見 [驗證紀錄](../docs/verification.md)。
