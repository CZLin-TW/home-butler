# 驗證方式與範圍

2026-09-14 v1.56.0（部署前驗證）：232 項 HB 離線測試、34 項 Dashboard 測試、lint、正式 build 與 Python 編譯通過。
自動關機測試涵蓋首次 on、off→on、調溫不重置、時數修改／停用、相同設定不重置、手動優先含未知結果、離線／不確定不下令，以及結果持久化跨請求不重送。
實際 HTTP 路由使用假 Sheets，確認只改既有時數欄位且保存不控制家電；嚴格整數驗證與完整 key／橋接／語音 key 邊界通過。
真實 HB WebSocket 測試收到自動排程的 power=off，回覆前 Sheet 已先記待確認，legacy handler 未被呼叫。
一般計時 tick 重用既有 batch 資料，測試確認狀態不變時不再取得 worksheet、不寫 Sheet；必要異動才即時定位列。
Chrome demo 驗證 HA 測試空調保存 3 小時顯示預計關機時間、收合重開保留值、改 0 停用；390×844 的欄位高 38px，無水平溢出。
本次沒有寫入家庭時數或操作實體家電；不需更新 HA。上線後原 Sheet 有效時數會啟用，首次接手已開機從觀察起算，實體到期反應待使用者驗收。

2026-09-14 v1.55.0（部署前驗證）：221 項 HB 離線測試、31 項 Dashboard 測試、lint、正式 build 通過。
新增 HA 手動排程來源標記、明確編輯、舊自動列拒絕、provider 切換取消與未知結果不重送測試。
實際 scheduler → control_ac → HB WebSocket 路徑以假 HA 接收命令、回覆狀態；確認發送前先持久化待確認、重複 tick 不重送且 legacy handler 未呼叫。
Chrome demo 新增 2026-12-01 03:00 HA 測試空調排程，編輯為關機並刪除成功；卡片即時狀態不受影響，收合草稿保留。
390×844 檢查各表單欄位高 38px、無水平溢出；不是 iPhone Safari 實機驗收。
1280×900 照明頁展開色盤，document 高度 900→1106，經典捲軸出現；卡片 left 均 56.4px、width 均 376px，沒有水平位移。
本次未建立正式家庭排程、未控制實體家電；實際到期後的家電反應仍待使用者驗收。無需更新 HA 整合。

部署確認：Render 已將 `41c5aab6e4aa14ee797baf558014d29e7208c203` 列為最後成功部署；
HB [CI 34847276001](https://github.com/CZLin-TW/home-butler/actions/runs/34847276001) 與 Dashboard
[CI 34847313146](https://github.com/CZLin-TW/Dashboard/actions/runs/34847313146) 均成功。
Dashboard `ba3a330332ad6c7a6d423dd3ccc45bbe3f093788` 已上線，正式 `/api/version` 與頁面均為 1.55.0。
已登入正式頁面，三台 HA 空調均有排程入口；客廳空調表單展開正常後取消，沒有建立正式排程。

2026-09-14 v1.54.0（部署前驗證）：216 項 HB 離線測試、30 項 Dashboard 測試、lint、正式 build 通過。
本機 demo 驗證彩色／白光切換、二維色盤點選、飽和度鍵盤操作、色溫回讀；390px 無橫向溢出。
HA 新增色彩能力、互斥與全批預驗證、混合燈組／部分支援／去重測試。
模擬離線情境正確顯示錯誤並隱藏不可操作卡片；未驗證 iPhone Safari 實機。

同日約 20:34（Asia/Taipei）正式部署完成：
- 後端／HA `75f80739953ff81a81b1148fff07a2660cf8bfa9` 的 [CI](https://github.com/CZLin-TW/home-butler/actions/runs/34843465827)
  全部成功（後端、HA Core 2026.9.2 真實框架、Homebridge）。
- Dashboard `6ba4572f588b076002d6ed65a68bb431ec6a5d60` 的 [CI](https://github.com/CZLin-TW/Dashboard/actions/runs/34843644252)
  成功，公開 `/api/version` 與正式頁面均顯示 1.54.0。
- Render 公開 OpenAPI 的 HueAreaStateRequest 已確認包含 hs_color 與 color_temp_kelvin，非僅前端上線。
- 透過家庭 HA Terminal 安裝上述固定 SHA 的 home_butler 1.5.0；備份為
  `/config/home_butler-backup-20260914-202943-379`，`ha core check` 與 `ha core restart` 均回成功。
- 重啟後正式 Dashboard 已重新讀到主臥／客廳兩組燈，各一盞，均有白光／彩色選擇；
  主臥為關閉、客廳開啟，Bridge 回讀顯示彩色。僅讀取家庭狀態，沒有發出燈光變更命令。

實際色盤／色溫呈色效果留待使用者驗收。HA 區域統一與 ToDo 通知效果編輯未在此版實作。


2026-09-14 v1.53.0：使用者決定除濕機完整保留 HB，移除 HB 自動夜燈與前端入口。
刪除 lighting_auto／ha_sensor_events；startup 不再載入夜燈 Sheet 或註冊 lighting 工作，
Webhook 只保留 HA Hub 提示，環境快照只更新資料。舊 rules GET 回空與 retired、寫入／刪除 410。
原 Sheet 資料保留；待辦燈光提醒的同步橋接移至 lighting_transport，不影響 HA／legacy 路由。
本機 214 項後端離線測試通過，含舊客戶端無法重啟規則、Webhook 仍轉送提示、提醒傳送與未知不重送。
Dashboard 28 項測試、lint／build 通過；模擬家庭完成桌機 1280 與手機 390 寬度的卡片／收合檢查。
已測模擬照明電源、亮度、場景、效果、通知、改名及離線提示；除濕機設定保存後收合重開仍保留，
自動模式下手動控制及感測器仍鎖定，監控時間可調。沒有操作家庭設備或更動 HA 自動化。
純 UI 測試不代表 Hue 燈具效果實機驗收或 iPhone Safari 已驗收；這版不需重裝 HA 整合。

同日正式部署確認：後端 `ac6361d8753e1e5b3efcbb7c736d8b7e9692f6b8` 的
[CI](https://github.com/CZLin-TW/home-butler/actions/runs/34837653311) 三項 job 全部成功，
Render UI 顯示該提交為 Last successfully deployed commit、Live，部署 `dep-dajtglgu01pc739ehsig`，耗時 1m14s。
Dashboard `d25b57f` 的 [CI](https://github.com/CZLin-TW/Dashboard/actions/runs/34837693572) 成功，
正式 `/api/version` 已確認 1.53.0。部署核對為唯讀，沒有操作家電；HA 套件及規則未修改。

2026-09-14 README 重整（僅文件）：首頁改為現行架構與建置入口，詳細設定／API 移至 backend-guide，
新增 version-selection，區分 main 不啟用 HA 與 HA 前 v1.44.0 固定快照。
已用 Git 歷史核對首次 HA commit 的父版本：後端 `5c0b681f149741236d16d100bf96b734d1b1f767`，
Dashboard `2c31431d0588e4cfbe8323384234f64d092dfd9d`；確認當時 Dashboard 1.44.0、Homebridge 1.3.0、
PC Agent 的 AUTO_UPDATE 開關與 origin/main 更新行為。未重新部署或實測舊版，不據此保證外部服務相容。
檢查七份入口與參考文件的本地／跨 repo 連結、標題錨點及程式碼區塊，git diff --check 通過。
無執行程式變更、無系統版本調整、無家庭設備操作或 HA 設定改動；此次不重跑硬體／模型測試。

2026-09-14 v1.52.0／switchbot_hub_light 1.2.0：內建可設定 60～3600 秒的備援更新，預設 60 秒。
定時／Push 共用工作，新增週期、合併、失敗恢復、卸載與選項重載測試。
`c6f00ce9d941ae4377c316a4156dc67a3569576f` 的 [CI](https://github.com/CZLin-TW/home-butler/actions/runs/34813483894)
全部通過：211 項後端測試、HA Core 2026.9.2／Python 3.14 的 53 項框架測試及 Homebridge 測試。
Dashboard `ea5cb72106369fa65fa0ae248d1500c2da18bee7` 的 [CI](https://github.com/CZLin-TW/Dashboard/actions/runs/34813495573)
測試／lint／build 與 Vercel 部署成功，公開 `/api/version` 已實際確認 1.52.0。
使用者同意與感測器／Hue 統一一起上線，實體操作留待使用者稍後測試。
同日 15:35（Asia/Taipei）已透過桌面啟動 Chrome 並使用既有登入完成家庭部署，不需遠端桌面。
HA 從上述完整 SHA 安裝 `home_butler` 1.4.0 與 `switchbot_hub_light` 1.2.0，重啟成功，UI 版本已確認。
安裝備份為 `/config/home_butler-backup-20260914-153500-346` 及
`/config/switchbot_hub_light-backup-20260914-153501-357`。
三台 Hub 保留選取，備援間隔儲存為 60 秒；舊自動化 `1789319755253` 已停用、未刪除。
主臥光照 attributes 顯示最後定時請求從 15:44:44 進到 15:48:47，最後 Push 仍為 15:40:40，
證明停用舊自動化後本機計時仍工作；此為請求時間，不能視為新的設備量測時間。

Home Butler 已配對客廳／主臥／次臥 Hub 2 的溫度、濕度、光照，以及 SwitchBot CO2 的溫度、濕度、CO₂，共 12 項。
原生來源為 `sensor.hub_2_ke_ting_*`、`sensor.hub_2_zhu_wo_*`、`sensor.hub_2_ci_wo_*` 的 temperature/humidity，
三房光照依序為 `sensor.guang_zhao_deng_ji`、`sensor.guang_zhao_deng_ji_2`、`sensor.guang_zhao_deng_ji_3`；
CO₂ 裝置使用 `sensor.co2_temperature`、`sensor.co2_humidity`、`sensor.co2_carbon_dioxide`。
Hue 選取主臥、客廳及全家來源，保留原兩個 FP2 實體、三台原生空調及九個 IR 按鈕。
Render 已儲存 `HOME_ASSISTANT_SENSOR_NAMES` 為這四個 HB 設備名称，`HOME_ASSISTANT_HUE_ENABLED=true`；
設定部署 `dep-dajqbhu7bikc73d409pg` 在 Render UI 確認 **Live**，執行 commit 為 `c6f00ce`（部署 40.6 秒）。
Dashboard 1.52.0 正式照明頁已顯示 Home Assistant、主臥／客廳兩區及既有場景／效果清單；
主臥「偵測亮度」回 11 級、HA 同步於剛剛，與 HA 當下值相同。裝置頁溫濕度／CO₂、歷史圖與 HA 連線可讀。
部署期間的資料過期提示於重新載入後消失。兩區 HB 自動夜燈均顯示 OFF，未變更其規則。
未發出燈光、空調、風扇控制或測試通知；Hue 開關／調光／場景／效果／通知及人工介入仍待使用者晚上實機驗收。
設定及還原見 [感測器與 Hue 統一](../homeassistant/sensors-and-hue.md)。

2026-09-14 v1.51.0／home_butler 1.4.0：新增感測環境快照與 Hue 選定區域控制通道。
本機 211 項後端離線測試通過，新增 HA 來源失聯／補償一次／歷史保留、光照型別、Hue 分流與提醒去重。
HA framework 測試新增 registry 改名／替換／單位、原生 Hue 場景／動態／smart_scene／timed effect、
未授權目標拒絕、命令過期／取消／部分失敗不重送及雙向傳輸。
`22bccffd12d97162cc6e61932d3b50ccf430992c` 的 [CI](https://github.com/CZLin-TW/home-butler/actions/runs/34810186441)
三個 job 全通過，其中 HA Core 2026.9.2／Python 3.14 的 47 項 framework 測試通過。
此為程式與 CI 階段紀錄；同日下午家庭安裝與切換已完成，詳見上方 v1.52.0 部署紀錄。
Dashboard `dee34f26cd34c7fecf87bac4c51a00670df0ea0c` 的 [CI](https://github.com/CZLin-TW/Dashboard/actions/runs/34810581459)
及 Vercel 部署成功（28 tests／lint／build）；隔離預覽已確認 HA 照明來源與「4 級」光照顯示。
HA 配對、Hue 選取與 Render 來源切換已完成；燈光實機驗收仍未完成，不以部署成功替代。

2026-09-14 家庭 HA 設定：建立「Hub 2 每分鐘更新感測資料」（`1789319755253`），
時間模式 `/1`，更新三台 Hub 2 各一個原生溫度實體，保留 Push。
HA 追蹤確認 01:16:00 由時間模式觸發，`homeassistant.update_entity` 在 0.49 秒內完成。
這是服務執行驗證，不代表三台設備當時都有新量測；未發送燈光或空調命令。
設定及還原方式見 [Hub 2 光照](../homeassistant/hub-light.md)。此次僅 HA 設定與文件，未修改整合程式或系統版本。

2026-09-14 v1.50.0：Hub 2 Webhook 提示經既有 HA WSS，喚醒原生 API 驗證讀取。
本機 205 項後端測試通過，涵蓋真實 WSS 選取／斷線／重連、不轉送外來光照值、格式／重播拒絕與突發通知合併。
HA 框架測試新增正式 coordinator 讀取更新、外來值不可覆蓋、連續通知最後一筆不遺失與卸載清理。
[72bf293 CI](https://github.com/CZLin-TW/home-butler/actions/runs/34769253232) 全部通過：28 項 HA 框架測試、205 項後端測試及 Homebridge 測試。
家庭 HA 於 00:42 重啟完成，安裝同 SHA 的 home_butler 1.3.0 與 switchbot_hub_light 1.1.0。
Render UI 已確認該 SHA Live，既有 Webhook 註冊成功，HA WSS 已重連。
主臥光照實體在 00:43:49 收到真實推送，00:43:50 更新請求完成，API 光照從 11 降到 10；
未用模擬 webhook 或自行開關燈驗收。使用者隨後實際關閉主臥一般照明，確認 HA 光照「有反應」；未量測關燈到更新的精確秒數。
Dashboard 1.50.0 的 [CI](https://github.com/CZLin-TW/Dashboard/actions/runs/34769397248) 通過。未建立夜燈自動化。

2026-09-13 v1.49.0：新增 switchbot_hub_light 1.0.0，從原生 Cloud coordinator 讀 Hub 2 lightLevel。
新增 HA 框架測試涵蓋光照單獨更新、缺失／非法值未知、連線失敗、native reload／來源改名／移除、
設定篩選與卸載 listener 清理。
[3238386 CI](https://github.com/CZLin-TW/home-butler/actions/runs/34767328475) 全部通過：
25 項 HA Core 2026.9.2 框架測試、201 項後端測試及 Homebridge 測試。
2026-09-14 00:04 起，家庭 HA 已安裝 3238386 的 switchbot_hub_light 1.0.0 並重啟成功，
選取客廳／主臥／次臥的原生 Hub 2 溫度實體，三個光照感測器皆建立在原裝置與原房間。
家庭實際頁面確認主臥 10 級、次臥 1 級、客廳 1 級；不是模擬值或 lux。
未改動燈光／空調自動化、未發送設備命令、未把等級匯入 HomeButler 的 lux 通道。
Dashboard 1.49.0 的 [CI](https://github.com/CZLin-TW/Dashboard/actions/runs/34767180292) 也通過；本次 UI／API 契約未變。

2026-09-13 v1.48.0：新增 HA IR Buttons 1.0.0 與 Home Butler 1.2.0 的選取式 IR 控制。
201 項後端離線測試通過，含真實 IR handler／WebSocket、名稱解析、結果分類、斷線不補送、
白名單與歧義拒絕。HA 真實框架測試新增按鈕平台至 SDK、來源身分／選取／去重／未知案例。
[a64456f CI](https://github.com/CZLin-TW/home-butler/actions/runs/34765088624) 全部通過，
包含 HA Core 2026.9.2 的 21 項框架測試、201 項後端測試及 Homebridge 測試。
家庭 HA 已從 a64456f 安裝 SwitchBot IR Buttons 1.0.0、Home Butler 1.2.0，重啟成功；
建立客廳／主臥／次臥電扇各三個按鈕，歸入原生裝置與原房間。Home Butler 已選取九個按鈕，
保留兩個 FP2 感測器與三台原生空調。Render 已加入三台電扇的 HOME_ASSISTANT_IR_NAMES，
設定部署 dep-dajc2lfqj5pc73d1gul0 已確認 Live；Dashboard 1.48.0 已上線，既有空調與 FP2 同步恢復。
家庭驗證只送兩次客廳風速命令：HA 直接按風速+，活動時間 23:30:13；Dashboard 按風速-，
HA 對應 button.ke_ting_dian_shan_feng_su_2 紀錄 23:32:13，確認 Dashboard 指令確實經 HA。
兩次介面均結束等待且未見錯誤；未測電源、未操作另外兩台電扇，也未觀察實體轉速。
按鈕時間只能證明呼叫發生，不能當作紅外線設備的真實狀態或實體收訊證明。

2026-09-13 v1.47.1：修正 HA 分流只接受英文模式／風速、拒絕 Dashboard 中文選項的問題。
197 項後端離線測試通過；新增 Dashboard 中文模式／風速經真實 WebSocket 邊界的指令與回傳標籤驗證、未知值不送出的回歸案例。
先前對相同設定的 Dashboard 畫面驗收不足以證明指令有送達，不能據此宣稱完整控制驗收。
[209bc89 CI](https://github.com/CZLin-TW/home-butler/actions/runs/34762589894) 全部通過，Render 已確認部署 209bc89 並恢復 HA 連線。
正式 Dashboard 依使用者既有草稿，對原本關閉的客廳送出一次開機／冷氣 29°C／低風速；
Dashboard 回報 22:27:43 的 29°C／冷氣／低，按鈕回到「未變更」。另開 HA 原生客廳空調面板，
獨立確認模式冷氣、目標 29°C、風速低一致。此為指令與平台狀態驗收，並非 IR 設備的實體狀態回讀。
測試後保留該設定；此次後端修正無需重裝 HA 整合。

2026-09-13 v1.47.0 室溫配對：新增 HA 平台、設定流程、sensor 替換、原生 registry 改名／刪除、
缺值／NaN／單位轉換與控制轉送測試。Python 編譯與安裝腳本語法檢查通過。
[6025591 CI](https://github.com/CZLin-TW/home-butler/actions/runs/34759469355) 全部通過，包含 HA Core 2026.9.2 的 17 項框架測試（室溫配對新增 6 項）、後端及 Homebridge 測試。
家庭 HA 已安裝 ac_room_temperature 1.0.0（6025591）並重啟成功。三台配對均選同房間 Hub 2 溫度；
HA 實體面板確認客廳室溫 27.8°C／冷氣目標 28°C、主臥 27.8°C／冷氣目標 27°C、次臥 28.8°C／關閉（目標 21°C）。
「更換室溫感測器」選項頁已在家庭 HA 開啟確認；換來源保留 identity／不發命令由框架測試驗證。
原 HASS Bridge:21064 保留 bridge／exclude、Binary Sensor／Sensor／Climate 類群，排除三個原生 climate，
改匯出三個 `*_shi_wen` 配對 climate，全部選加熱冷卻器，介面確認選項已儲存。
未修改 Home Butler 的原生空調 allowlist，未為本次設定發送實體冷氣命令。Apple Home 手機顯示、房間／場景設定仍待使用者確認。
本次未恢復回饋補償，未修改後端空調控制來源。Apple Home 全部改用 HA 原生配件已由使用者確認可用。

2026-09-13 HA 空調遷移（v1.46.0／HA 整合 1.1.0）：本機 195 項後端離線測試通過，
涵蓋 HA 狀態優先、斷線未知、指令配對／去重／不補送、整數正規化及 legacy 回饋分流。
新增真實 HA 框架的原生平台選取、參數預驗證、service 結果與雙向傳輸測試。
[55a2659 CI](https://github.com/CZLin-TW/home-butler/actions/runs/34749437363) 的 HA、後端及 Homebridge 工作全部通過。
Render 已部署 55a2659；家庭 HA 已安裝整合 1.1.0、重啟並保存三台原生空調，保留兩個 FP2 來源。
既有 HA HomeKit Bridge 已加入 Climate 類群、三台均選加熱冷卻器，保留原 Sensor／Binary Sensor 類群及配對。
使用者已確認 HA 控制實際正常。2026-09-13 晚間已設定 HOME_ASSISTANT_AC_NAMES 為三台空調，
Render 494d01e 的設定部署成功，後端控制權已切換。正式 Dashboard 三台均顯示「由 HA 管理」，
客廳冷氣 28°C／自動風速與 HA 一致（舊 Sheet 為低風速）；回饋與舊排程編輯已退出。
從正式 Dashboard 對客廳送出一次相同的冷氣 28°C／自動設定，完成 HA 確認並解除送出狀態，未自動重送。
次臥仍回報初次匯入的送風 21°C，不能推論為實體狀態；由使用者在 HA 設定所需狀態。
新 Apple Home 配件實機驗收與舊 Homebridge 清理仍待完成，尚未刪除原配件。
FP2 前階段已實機確認觀測同步、停用顯示未知及重新啟用恢復。

2026-09-13 HA 第一階段（v1.45.0，已推送 main）：本機 186 項後端測試通過，
包含真實 FastAPI／WebSocket 的獨立金鑰、格式驗證、false／0／未知、斷線、
重連與舊連線隔離。Python 編譯與安裝腳本語法檢查通過。
另以假傳輸驗證斷線重連後重新讀取最新快照、不補播舊值，測試通過。
Linux／Python 3.14／HA Core 2026.9.2 的 6 項 registry、設定流程、生命週期與
傳輸測試通過，包含改名保留 ID、刪除／未知、lx 白名單、取消分享、認證錯誤與重連。
同一版後端與 Homebridge CI 亦通過，見 [16c534d CI](https://github.com/CZLin-TW/home-butler/actions/runs/34745832730)。
真實 HA 框架使用假的後端傳輸，不能寫成家庭 FP2 已回傳成功；Render 專用 key、
HA 安裝、FP2 進出／離線／重連均待實機驗收。未呼叫 Sheets、家電或付費 AI。

2026-09-09 即時感測與歷史分離（v1.44.0）：179 項後端離線測試通過。新增真實 HTTP 設定接受／保存 1 分鐘、預設不變、59 秒不調整／60 秒可調整、新讀值與相同樣本去重；0、非整數及超過上限仍拒絕。另驗證快速感測器選取／共享／關機取消、8 個並行查詢僅一次 API、補償只套用一次、失敗不更新資料年齡且可恢復、先取值再評估、24 小時每分鐘更新仍只記 288 筆歷史，以及重啟回填不覆蓋新讀值或重複寫入。同時使用假時鐘／外部 SDK，未操作實體家電。

2026-09-09 半度診斷（系統 v1.43.1／插件 1.3.0）：23 項插件測試與 169 項後端離線測試通過。新增 opt-in、首次註冊時序列化 minStep=0.5、半度冷暖／電源指令僅本機模擬、收到值的 Log、無背景輪詢也能讀取、快取還原不重複、停用只移除診斷配件與真實後端離線隔離。未發布實際橋接廣播，新增測試配件的 iPhone 步幅待使用者回報。既有 1.2.0 配件使用者已確認：Siri 半度控制及 Apple Home 半度顯示正常，但 Apple Home 調整手勢仍跳 1°C；不能宣稱重新配對可修復。

2026-09-08 半度目標（系統 v1.43.0／插件 1.2.0）：169 項後端離線測試、變更模組編譯及 19 項 Homebridge 測試通過。涵蓋啟用／停用回饋的半度保存與 half-up 正規化、冷暖整數 IR、補償邊界、防黴半度保留、設定停用不發指令、Dashboard 接受值回應、HomeKit SET 後正規化同步，以及模型字串參數直接控制／排程不截斷。npm pack --dry-run 確認插件只含 5 個預期檔案。全部使用假外部服務；未呼叫付費 AI、未控制實體家電，iPhone 上半度操作與實際冷氣回應仍待驗收。

以下是維護入口及已留存的驗證範圍，不是每次部署自動續期的保證。

2026-09-08 立即評估（v1.42.1）：162 項離線測試及變更模組編譯通過。新增保存後立即評估、略過背景週期／啟動等待、僅評估指定設備且不覆寫設定、同樣本跨重啟不重送、剛手動控制後等待、關機／非冷暖模式／過期／達標不發送、保存失敗不評估及未知結果不重試的測試。外部服務皆為 fake，未操作真實家電。

2026-09-08 空調室溫補償（系統 v1.42.0）：Python 3.12 的 157 項離線測試及變更模組編譯檢查通過，Homebridge 18 項測試通過。新測試涵蓋冷暖補償方向、保持舒適目標、死區與上限、同樣本去重、過期／關機／送風除濕不調整、重啟等待、未知結果持久化暫停、與手動關機共用鎖、設定不送 IR、獨立 API Key 邊界，以及缺欄拒絕部分保存。未發送真實家電指令，實際室溫穩定性仍待家庭環境觀察。詳見[功能說明](ac-temperature-feedback.md)。

2026-09-07 Homebridge 第一版（系統 v1.40.0）：本機 Python 3.12 完整 137 項離線測試、
Python 編譯檢查通過；Node 24 / Homebridge 2.4.0 的 11 項插件測試通過。
`npm pack --dry-run` 確認安裝包只含五個程式／設定／說明檔案，無憑證或測試依賴。
使用者已完成家中 VM 安裝、主橋接器配對與插件安裝，正在填寫連線設定；Render 橋接 API、
Siri／冷氣控制仍待實機驗證。

2026-09-07 設定表單調整（插件 1.0.1／系統 v1.40.1）：使用者回報室溫來源只見標題且無法捲動。
改用明確的 tabarray 與兩個子欄位、可收合連線區塊及數字輸入，並補上選填／更新說明。
schema JSON 解析、11 項插件測試、137 項後端離線測試及 npm 打包內容檢查通過。
使用者已回報新版表單可操作、Apple Home 已出現空調；以上離線測試本身不涵蓋瀏覽器操作。

2026-09-07 四模式（插件 1.1.0／系統 v1.41.0）：18 項真實 HAP 程式庫測試與 142 項後端
離線測試通過。新增冷暖 SET／調溫、四模式同步、除濕送風互切、同手勢模式 OFF/ON 兩種順序、
主電源 OFF 優先、條件式關機新讀 Sheet、no-op 去重、未知狀態拒絕、防黴回應及配件／服務身分保留。
沒有控制實體家電；新版在 iPhone 的分組、暖房操作、Siri 名稱與實際設備回應仍待使用者驗收。

## 離線檢查

Homebridge 插件另在 `homebridge/` 執行 `npm ci --ignore-scripts`、`npm test`，
以官方 Homebridge 2.4.0 的 PlatformAccessory／HAP 特徵驗證狀態更新、SET 完成後的防黴狀態、
同手勢合併、舊輪詢隔離、無回應、真實室溫來源及未知結果不重送。HTTP 為 fake，沒有啟動橋接廣播。
後端 `tests/test_homebridge.py` 使用真實 FastAPI 與 fake Sheets／設備，驗證獨立權限、
同名／同 ID 拒絕、局部設定、狀態投影、去重及保存失敗。實機驗收流程在 [插件文件](../homebridge/README.md)。

```sh
pip install -c requirements.lock fastapi httpx
python -m compileall -q .
python -m unittest discover -s tests -v
```

測試用假 Sheets／設備 SDK／模型，覆蓋排程執行結果、未知結果不重送、列位移、控制結果、LINE callback 並行、首頁輕量查詢、工作隔離、天氣預算、待辦權限與 Notion 協調。家電語音測試另使用真實 FastAPI／TestClient，驗證獨立金鑰、停用／重複設定、拒絕身分覆寫及回覆契約；只需從既有 lock 安裝 HTTP 測試依賴，不需正式憑證。CI 設定見 [ci.yml](../.github/workflows/ci.yml)，目前使用 Ubuntu／Python 3.11。

測試不啟動正式服務、不發 LINE 或家電指令；不能證明 Google Sheets、認證憑證、外部雲端與家中設備當下可用。

付費的真實模型效能／辨識評估另見 [Sonnet effort benchmark](../evals/README.md)。它使用固定合成資料，只跑解析，不執行業務 handler；必須先取得 API 呼叫額度授權，且不加入 CI 自動執行。既有的 fake 模型測試不能當作降低 effort 後辨識率的證據。

## 已有紀錄與未涵蓋範圍

- 2026-09-07 Sheets 連線重用與 AC 狀態寫入（系統 v1.39.3）：本機 Python 3.12 完整 123 項離線測試及編譯檢查通過。新增 8 項測試覆蓋並行只建立一次、時間經過不重建、初始化失敗及重試用盡後重建、即時欄列／空白列定位、缺表與歧義不寫、未知寫入不重試、開機錨點／防黴還原及寫入失敗不更新快取。SDK 按需更新憑證的行為已核對官方來源；未用正式憑證跨過期時間測試，未新增真實設備／模型／Sheets 寫入呼叫。正常 AC 存檔減為兩次 Sheets 呼叫的離線驗證不等同正式省時秒數。

- 2026-09-06 I/O 精簡與細分計時（系統 v1.39.2）：本機 Python 3.12 完整 115 項離線測試及編譯檢查通過。新增 7 項測試核對照明唯讀／管理路徑、排程不變時零 worksheet 取得、新增不讀封存、重置／取消保持先封存後刪除、巢狀 span 與 HTTP 重試邊界。這些是假外部 I/O 的呼叫次數／行為驗證，未新增真實 Claude、家電或 Sheets 寫入測試；正式節省秒數尚未量測。

- 2026-09-06 Siri 不帶歷史（系統 v1.39.1）：本機 Python 3.12 完整 108 項離線測試及編譯檢查通過。新增 2 項測試核對真實解析函式的一般／降級 SDK payload，Siri 只含當句且不讀取歷史，LINE 預設保留歷史；既有管線測試補上來源選擇、初始五表讀取與原有存檔契約。沒有新增真實模型呼叫或家電測試，尚未證明改善正式速度或辨識率；先前 120 次評估本來就未帶歷史，不能當成本次變更的前後比較。

- 2026-09-06 Siri 分段計時：本機 Python 3.12 完整 106 項離線測試通過（新增 7 項），含假時鐘耗時、並行請求隔離、例外及記錄失敗、完整入口二次 AI／原有降級、家電專用入口未知結果不重送；編譯檢查通過。只增加 `[TIMING]` 觀测，未新增真實 AI 呼叫、家電控制或 Sheets 寫入；未取得 Render 正式分段耗時，不能宣稱已找出瓶頸。操作見 [語音分段計時](voice-timing.md)。

- 2026-09-06 Sonnet 5 effort 真實 API 評估：完成預先授權的 120 次呼叫，三組各 40/40 意圖及參數通過，無錯誤／截斷；high／medium／disabled 中位時間約 2.774／2.704／2.679 秒，估計總費用 USD 1.211402。使用合成資料，只做解析，未控制家電。結論為暫不調整正式 high，完整範圍及限制見 [實測紀錄](../evals/results/2026-09-06.md)。評估工具含臨時金鑰輸入的本機離線測試合計 99 項通過，`d22462f` CI 通過；這與 120 次真實模型請求分開計算。
- 2026-09-06 家電專用語音（系統 v1.39.0）：Windows／Python 3.12 本機 85 項測試及編譯檢查通過，新增 16 項測試。使用真實 FastAPI／TestClient 驗證獨立金鑰、原完整入口的 verifier、停用／重複／輪替、身分覆寫與輸入邊界；假模型／Sheets／handler 驗證設備目錄投影、混合越權動作整批拒絕、名稱／參數範圍、無效模型輸出、簡潔實際結果、部分執行例外不重送。既有完整語音回歸一併通過。未以正式 Claude、Render 環境金鑰、iPhone 或實體設備驗證；上線需另設 `DEVICE_VOICE_API_KEY`。
- 2026-09-06 Siri 精簡回覆（系統 v1.38.2）：Windows／Python 3.12 本機完整離線測試 69 項及編譯檢查通過。新增 11 項語音測試覆蓋實際結果短句、LINE 原回覆、emoji／格式清理、數值日期、失敗及追問、混合指令、既有查詢路徑、API 契約與對話存檔；AI、Sheets 與設備為 fake。沒有增加真實設備控制或 iPhone 朗讀測試，手機效果仍需使用者確認。
- 2026-09-06 IR 名稱修正（系統 v1.38.1）：Windows／Python 3.12 本機完整離線測試 58 項及編譯檢查通過。新增 8 項測試覆蓋電扇別名、精確名稱優先、房間隔離、單台省略名稱、歧義、無效設備及缺少動作不送出；實際 handler 的裝置傳輸均為 fake。這不代表 Siri 漏字、AI 自然語言解析或家中風扇已實機驗證修復。
- 2026-09-06 的架構整理已包含上述離線回歸；具體執行結果以對應 [GitHub Actions](https://github.com/CZLin-TW/home-butler/actions) 的 commit 為準。
- 同日曾經從正式 Dashboard 讀取 PC 心跳與劇院兩程序版本，表示當時 Dashboard → home-butler → PC agent → theater-agent 的摘要路徑可讀。這不代表所有雲端控制與 LINE 推播已實機測試。
- 本次文件整理不新增真實設備、LINE、Notion 或 Sheets 寫入測試，不據此擴張先前測試結論。
- 多 worker／多實例、外部直接修改 Sheets 的競爭不在現有單程序鎖的保障範圍。
- `agent/` 的系統排程、硬體指標、Hue 控制與更新應在各台 PC 分別核對；後端 CI 不代表 Windows agent 已部署。

部署觀察請記下時間、目標程序、程式版本／SHA、最後成功時間與查到的結果。無法讀到程序版本時寫「未確認」，不能用 push 或 CI 代替。跨專案流程見 [系統導覽](system-overview.md)。
