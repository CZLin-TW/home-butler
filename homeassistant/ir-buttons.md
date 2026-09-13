# 電扇紅外線按鈕（v1.48.0）

`switchbot_ir_buttons` 1.0.0 為 HA 原生 SwitchBot Cloud 遙控設備補上可供 UI／自動化使用的
獨立 button 實體；`home_butler` 1.2.0 將明確選定的按鈕提供給 Dashboard／LINE。
沒有風量百分比、段數或開關狀態回讀，不建立推測 fan 狀態。

## 安裝與設定

1. 先部署本版後端。以通過 CI 的完整 commit SHA，依 [安裝器](README.md#安裝)
   更新 `home_butler`，並再執行 `sh /tmp/install-home-butler.sh COMMIT_SHA switchbot_ir_buttons`。
2. 重啟 HA。新增 **SwitchBot IR Buttons** 整合，每台電扇設定一次：
   原生 SwitchBot Cloud 的 switch、與 Sheet 相同的管家設備名稱、半形逗號分隔的按鈕名。
   例如 `電源,風速+,風速-`，自訂名稱必須與 SwitchBot App 完全一致。
   不需新增 Token／Secret；沿用選定原生整合的記憶體連線。
3. 新按鈕歸到原生設備，沿用其房間。HA 裝置詳細頁可操作，亦可加入儀表板或用 `button.press` 自動化。
   每個按鈕只按一次，不自動重試；本地 HA 操作不依賴 Render。
4. HA → Home Butler → 設定，保留原 sensors／climates，再勾選要提供給管家的 IR 按鈕。
5. Render 設 `HOME_ASSISTANT_IR_NAMES` 為 JSON 設備名稱陣列，例如 `["客廳電扇"]`。
   這是逐台控制權切換，Dashboard／LINE／既有 IR 排程的共用 handler 都改走 HA。
   HA 未選取的按鈕拒絕，HA 離線不退回直接 SwitchBot，不會離線排隊後補送。

按鈕識別依來源 registry ID 與按鈕名稱固定；HA entity ID 改名不會改控另一台。
要更換來源／按鈕定義，移除該台 IR Buttons 整合再重新建立，並重新核對 Home Butler 勾選。
新增非電扇的自訂 IR 按鈕也可使用相同方式，但需先明確確認目標與命令。

## 命令語意與限制

- 風速 `+`／`-` 原樣以 SwitchBot `customize` 命令送出。沒有段數讀值，不能當成絕對設定。
- `電源`／`開` 等名稱沿用 HB 現有 `turnOn`；`關` 等名稱用 `turnOff`。
  「電源」對只有一顆電源鍵的設備可能是切換，不能宣稱必定開機或關機。
- 成功僅代表 SwitchBot API 接受；超時／通道斷線／送出後錯誤是 unknown，不自動重送。
- 傳輸沿用 HA 主動連線，新增協商能力 `ir_control`；快照只含選定按鈕的 ID、名稱、標籤與可用性。
  HB 白名單與 HA 本機按鈕白名單雙重檢查，禁止任意 service、entity ID 或直接裝置 ID 下行。
  有效期 15 秒；HA 每台按鈕群組拒絕重疊操作，通道每次只處理一個命令；最近 256 ID 去重。
- 底層仍是 SwitchBot Cloud，並非離線 IR 控制。原生整合需要已載入，未知電源狀態不等於連線離線。
- 本地整合的相依介面集中於 `driver.py`，核對 Core 2026.9.2 的 `runtime_data.api/devices.switches`；
  HA 更新後若介面不同會拒絕控制，不讀憑證、不 monkey patch 原生整合。

回復先移除 HA 的相關自動化，再明確撤回 Render 的 IR 名稱清單，恢復 HB 原控制權；
之後才能移除新增按鈕。不要只回退程式而留下兩套同時執行的規則。

測試涵蓋 HA 真實 button 平台 → SDK 呼叫、原生來源改名／刪除／重載、選取與期限／去重，
以及 HB 真實 handler → WebSocket 的成功／失敗／未知與離線不 fallback。實機驗收另外記錄。
