# 家庭中樞架構與遷移順序

## 責任分工

| 元件 | 長期責任 | 第一階段現況 |
| --- | --- | --- |
| Home Assistant（HA） | 本地設備狀態、基本控制、即時自動化、Apple Home 發布 | FP2 已接入；Home Butler 整合只上傳選定的存在與亮度 |
| HomeButler（Render） | AI／LINE、家庭權限、待辦庫存、跨服務提醒、Dashboard API | 接收 HA 快照；原家電與排程控制路徑保留 |
| Dashboard | 家庭成員操作與資訊顯示 | 新增空間感測；瀏覽器只連 Dashboard，不取得 HA 金鑰 |
| Apple Home／Siri | Apple 生態的操作入口 | FP2 經 HA HomeKit Bridge；既有空調仍經 Homebridge |
| Theater Agent | AVR、KEF、電視等具狀態的協調控制 | 維持独立程序與既有 HB／PC relay；本階段不移轉 |
| PC Agent | 每台 PC 的系統監測 | 原 Hue／Theater relay 暫時保留；日後與家庭中樞連接職責分離 |

長期一般設備的控制方向為「Dashboard／LINE → HB → HA → 設備」與
「Apple Home／HA 自動化 → HA → 設備」。SwitchBot 最底層走本地或雲端，不改此分工。
v1.46.0 增加逐台空調移轉；以 Render HOME_ASSISTANT_AC_NAMES 與 HA 實際選取為準。SwitchBot Cloud 仍走雲端，不能稱為全本地。
v1.48.0 增加逐台 IR 按鈕移轉；HOME_ASSISTANT_IR_NAMES 決定控制權，HA 本機按鈕與下行白名單
獨立於雲端連線。未知指令不重送、不猜風量或電源狀態，見 [IR 按鈕](../homeassistant/ir-buttons.md)。

## 第一階段：FP2 觀測

HA 自訂整合主動以 WSS 連到 Render，使用獨立 HOME_ASSISTANT_API_KEY。
只傳明確勾選的 occupancy／presence binary sensor 與 lx illuminance sensor：
穩定 registry ID、entity ID、名稱、區域、型別、值、可用性與 HA 狀態更新時間。
不傳任意 attributes、HA token、完整設備目錄、家庭資料或服務呼叫權限。

狀態改變後合併短時間事件並送完整快照；無改變時每 30 秒確認一次。
Render 只在記憶體保存最新一份，不每次寫 Sheets，不新增資料庫。
斷線立即將讀值呈現為未知；無有效快照超过 90 秒也過期。
重連先未知、收到新快照才恢復；不補播離線期間事件。
HA last_updated 是狀態更新時間，並非感測器每次物理量測時間；長時間不變不等於感測器故障。
連線心跳也不能證明 FP2 實體健康，實體可用性仍依 HA 原整合。

一套 HB 部署只接一套 HA；新連線取代舊連線。此狀態與現有排程仍限定單 process／worker。
Dashboard 成員查看，kid 不開放此新資料；瀏覽器只存記憶體，5 秒更新一次，
連接失敗或資料過期顯示未知。Apple Home 即時自動化不等此雲端同步。

安裝、欄位、金鑰與復原方式見 [HA 整合](../homeassistant/README.md)。

## 後续遷移與完成條件

1. 完成 FP2 的 HA → HB → Dashboard 實機驗收。
2. 評估 Hue 原生 HA 整合與現有場景、效果、通知的功能對等，再移轉控制權。
3. Theater Agent 保留本地複雜控制器，之後獨立驗證 HA 中繼與狀態投影。
4. 逐台搬移空調；使用者取消半度與回饋補償，先驗證四模式、整數溫度、風速與狀態同步。
   舊防黴／自動關機／HB 空調排程退出，之後按需在 HA 重建。除濕機本次不移轉。
5. Apple Home 全部配件與功能驗收完成後，才停用重複的 Homebridge 配件或服務。

每一台設備、每條規則同時間只有一個控制主體。禁止建立 HB → HA → HB 的循環命令路徑，
禁止同一自動化在兩邊同時執行。命令被接受、最後送出的 IR 與實體觀察值必須分開；
未知結果不自動重送。空調下行命令的權限、去重、結果與超時契約見 homeassistant/README.md。

Mac mini 第一階段定位為本地 Agent／Bridge 中心，HB 保留 Render。
相機分析未實作；未來由本地分析輸出區域存在等狀態，再進 HA 與 HB，不將連續影像傳回 HB。

## 參考與維護

- [HA HomeKit Bridge](https://www.home-assistant.io/integrations/homekit/)
- [HA HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/)
- [HA Hue](https://www.home-assistant.io/integrations/hue/)
- [HA SwitchBot](https://www.home-assistant.io/integrations/switchbot/)
- [HA Matter](https://www.home-assistant.io/integrations/matter/)

文件中的「長期責任」不代表對應設備已遷移。各階段以驗證紀錄與實際部署狀態為準。
