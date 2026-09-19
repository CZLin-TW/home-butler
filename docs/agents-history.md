# home-butler 版本變更紀錄

這裡是**歷史**：每個版本當時的決定與限制，按時間新到舊排列。
目前行為一律以 [AGENTS.md](../AGENTS.md) 的現況章節與程式碼為準；
**不要把這裡的舊版描述改寫成最新版**，那會讓歷史失去對照價值。
若某條舊限制到現在仍然成立，它應該同時出現在 AGENTS.md 的「不可違反的約束」。

# 2026-09-19 HA 本地劇院開關部署驗證

家庭 HA 安裝 Theater Agent 1.0.0、Home Butler 1.7.0 並選用共用控制器。
HA 與 Dashboard 唯讀核對一致，後者摘要來源為 `home_assistant`；三個旗標沿用既有值。
核對後將開關名稱修正為「電視畫面自動關閉」「AVR 自動隨電視開啟」，避免誤示恢復或雙向電源同步。
未操作實體家電或測試斷網控制，詳細範圍見驗證紀錄。

# 2026-09-19 HA 本地劇院開關（部署前）

新增 Theater Agent 1.0.0 三個 switch 與本地控制器；Home Butler 1.7.0 可選取它，
維持原 summary／set_flags 契約與獨立劇院指令車道。Render 不參與本地開關的載入與輪詢。
Agent 的旗標檔案是持久化來源，HA 啟動不覆寫；POST 後 GET 確認，未知不重送。
原劇院連動、Windows 程序與 Dashboard API 不變；實際安裝與路徑切換另行驗收。

# v1.57.0 自動關機改用一般排程編輯

使用者要求時數只在 Sheet「自動關機小時數」管理；Dashboard 移除設定面板與 BFF，舊後端 POST 回 410。
HB 每 60 秒觀察 HA，首次 on 依 Sheet 產生來源「自動（HA）」的一筆排程；正整數時數變更用於下輪，0 取消本輪。
Dashboard 原排程區可編輯時間／同一空調參數或刪除，不能編輯未知／失敗結果；刪除後不補回。
以來源與既有 JSON metadata 追蹤本輪；_auto_edited 保留修改，_auto_deleted 的已取消列是隱藏去重紀錄，確認 off 前不能封存。
確認 off 只取消本輪尚未執行的 off；另建的手動排程完全保留。若明確編成 on／調溫則結束 cycle、保留為一般未來排程。
已關閉的成功／取消 cycle 可獨立封存，即使該設備還有未來手動排程。下次 on 才依 Sheet 重新產生。
v1.56 _auto_paused 舊列會恢復為可編輯排程，保留期限；不再因另有手動 off 隱藏或重建此列。
排程增改刪與背景 reconcile／dispatch／archive 共用 cycle lock；寫入前查即時列，歧義拒絕。未知結果不重送。
不新增 Sheet 欄位或 HA 自動化，HA 組件不用更新；不可用家庭家電操作測試。詳見後端 docs/ac-auto-off.md。

# v1.55.0 HA 空調手動排程與控制樣式

Dashboard 空調可新增／修改／取消指定日期時間的一次性排程，由 HB 每 60 秒檢查、經 HA 控制。
新建或明確編輯的 HA 手動排程使用來源「使用者（HA）」；舊列不自動升級，自動／防黴列不能轉換。
執行時重驗來源與目前 provider；切換不一致則取消，不能 fallback 直接 IR。未知結果維持待確認、不重送。
HB 舊自動關機、防黴與回饋仍不對 HA 空調執行；這不是 HA 自動化的編輯器，也不是每日重複排程。
排程共用 Dropdown／38px 控制項與收合卡片；keepMounted 保留草稿，其他延遲載入面板仍按原設定卸載。
html 預留 scrollbar-gutter: stable，舊瀏覽器以 overflow-y: scroll 備援，避免色盤展開引起左右位移。
部署先 HB 再 Dashboard；不需更新 HA 整合或 Sheets 欄位。

# v1.54.0 照明光色控制

照明卡保留電源／亮度，加入白光色溫與可展開的 HSV 二維色盤；特效／場景直接顯示。
通知操作已從日常照明卡移除，既有 ToDo breathe 提醒保留，通知效果編輯器尚未實作。
需要 HA home_butler 1.5.0 才宣告 color_control；舊 HA／PC 不顯示新控制，不可默默忽略色彩請求。
只向允許燈組內支援的燈具送色彩／色溫；HS 經 HA 公開色彩工具套每燈 gamut，色溫使用共同範圍。
調色不附帶開機；白光和彩色不能同時下發，整批預驗證後寫入，未知／部分完成不重送。
色盤放開、滑桿放開／鍵盤完成才送；模式切換只選擇編輯工具，調整後才套用。
目前讀值來自 Bridge；混合光色不平均，mirek 取整後 K 值可能與輸入有小幅差異。
這版聚焦光色卡片；HA 區域統一、HB 除濕機區域對應及 ToDo 效果編輯仍是下一階段，不能宣稱已完成。

# v1.53.0 照明精簡與除濕機保留

使用者決定除濕機完整留在 HB（新家預計中央除濕），不再列 HA 遷移待辦。
HB 夜燈引擎／背景工作／Webhook 及 HA 快照評估已移除；舊 Sheet 保留不執行，規則寫入回 410。
Hub 更新提示與備援轮詢保持；待辦燈光提醒改用 lighting_transport.send_command_sync，不可依賴已刪夜燈模組。
照明卡常駐電源／亮度／場景，效果通知與區域設定收合；除濕機自動／手動設定收合，HB 控制語意不變。
這是既有卡片整理，尚未改成新的全站控制 Layout。部署先後端再 Dashboard，無須重裝 HA 整合。

v1.52.0／switchbot_hub_light 1.2.0：Hub 備援查詢由整合管理，預設 60 秒、可設 60–3600 整數秒。
Push 與定時刷新共用單台工作／原生 coordinator；請求結束後延後該台的下一次輪詢，保留最後一筆提示。
不 monkey patch core、不取出憑證、不新增 HB 查詢；Render 斷線仍能輪詢。卸載／options reload 清理計時與 pending。
家庭已於 2026-09-14 安裝並停用舊自動化 1789319755253（保留供還原）；60 秒備援已讀到實際請求紀錄。
感測器與 Hue 切換旗標已啟用，Dashboard 讀取已驗證；燈光實體操作仍待使用者驗收。詳見 verification。

v1.51.0／home_butler 1.4.0：[感測器與 Hue 統一](homeassistant/sensors-and-hue.md)。
`HOME_ASSISTANT_SENSOR_NAMES` 按名稱指定唯一 HA 即時來源；`ha_sensors` 套原 Sheet 補償一次，
五分鐘歷史保持，不可在 HA 失聯時 fallback 雲端或把 1–20 光照等級當 lux。
`HOME_ASSISTANT_HUE_ENABLED` 將所有照明入口切到 `lighting_transport`，HA 本機選定 Hue 區域。
Hue 沿用原生 aiohue 4.9.0 公開連線；不得抄金鑰、任意 URL／service、未知結果換 payload 重試。
`hue_model` 是 legacy agent 純呈現投影的相容副本，修改需同步契約／測試，不可 import PC agent。
HA Hue 啟用時 legacy 待辦燈光 queue 必須為空；`lighting_reminders` 每分鐘執行，不傳私人待辦到 HA。
生產切換狀態以 verification 紀錄為準，不能由合併／CI 推定已安裝。Theater／除濕機控制不在本次遷移。

v1.50.0：Hub 2 Push 見 [homeassistant/hub-light.md](homeassistant/hub-light.md)。
Home Butler 1.3.0 + 光照 1.1.0 透過現有 Render Webhook／WSS 轉送選定 Hub 的刷新提示。
無簽章 payload 不能寫進 HA 狀態；值只能來自 native authenticated refresh，新增事件 I/O 是明確需求。
保留訂閱白名單、事件時間驗證、合併最後一筆、舊版 capability 相容、卸載與斷線清理測試。
既有 HB 夜燈本來就走 Webhook；不搶占 URL、不新增 HA 夜燈規則。
家庭 HA 另有「Hub 2 每分鐘更新感測資料」自動化（1789319755253），每台只刷新一個原生溫度實體。
這是 1.1.0 的家庭備援設定；1.2.0 以整合內計時取代，切換時停用這條自動化。設定與還原見同一份光照文件。

v1.49.0：Hub 2 光照見 [homeassistant/hub-light.md](homeassistant/hub-light.md)。
原 switchbot_hub_light 1.0.0 僅共享原生 SwitchBot Cloud coordinator 的 lightLevel，不新增 I/O、
不讀金鑰、不 monkey patch 核心。1–20 級不得標成 lux 或匯入現有 illuminance 通道。
保留 native reload／registry identity／缺值未知／單獨光照更新及卸載 listener 清理測試。

v1.48.0：IR 電扇按鈕見 [HA IR 按鈕](homeassistant/ir-buttons.md)。獨立 switchbot_ir_buttons 本地整合
只沿用明確選取的原生 SwitchBot Remote 連線，建立 momentary button，不推測 fan／power 狀態。
home_butler 1.2.0 的 ir_control 能力只接受本機勾選的該平台 button；HB HOME_ASSISTANT_IR_NAMES
逐台分流共用 IR handler，離線／錯誤不得 fallback 直接 IR。相對按鍵未知結果不重送；registry ID、
命令期限、去重與真假來源測試必須保留。Theater／除濕機不隨電扇遷移。

v1.47.1：Dashboard AC mode／fan_speed 送中文顯示值，HA 分流必須在嚴格驗證前正規化為 HA 值；
`ha_climate.MODE_INPUTS/FAN_INPUTS` 同時支援既有中英文別名，不可對未知值套預設。回傳 lastMode／lastFanSpeed 仍為中文以供 UI 確認。
回歸測試需用實際 Dashboard 中文 payload 經 handler → WebSocket → 確認狀態，不能只測英文或對已相同狀態送出。

v1.47.0 室溫配對：`homeassistant/custom_components/ac_room_temperature` 為獨立本機整合，
不依賴 Render／home_butler setup；每台原生 SwitchBot climate 配一個溫度 sensor。
registry ID 固定來源與唯一實體；options 只換 sensor、不換空調 ID、不發指令。
Apple Home 匯出配對 climate、排除原生 climate；HB 仍只選原生 SwitchBot 平台。
感測事件只更新室溫，禁止控制迴圈、改 native state 或 monkey patch；失聯配對實體 unavailable。
安裝、更換與限制見 [室溫配對](homeassistant/room-temperature.md)，驗證使用 HA CI。

HA 架構以 [docs/local-hub-architecture.md](docs/local-hub-architecture.md) 為準。
v1.46.0：`HOME_ASSISTANT_AC_NAMES` 明確決定逐台控制權；設定錯誤或 HA 失聯不可 fallback 到直接 IR。
`ha_climate.py` 在原 handler／回饋 wrapper 前分流；HA 管理空調不寫 Sheet last-state，
不跑補償、防黴、自動关機與 HB 排程。使用者已取消半度與回饋，溫度為整數。
原生 SwitchBot Cloud climate 是唯一控制實體，禁止將 Homebridge 匯入實體再導回 HB。
HA `climates.py` 僅接受本機明確選取的 registry ID + 穩定名稱、固定 climate 動作與參數。
命令有效期 15 秒、無離線佇列；預驗證失敗與送出後未知分開，未知不自動重送。
狀態由 HA 快照投影到 Dashboard／prompt／Homebridge；IR 仍無實體回讀。
遷移後 Apple Home 應使用 HA HomeKit Bridge 的原生 climate；舊 Homebridge 不列入相容驗收（HA 關機時無模式，舊插件可能顯示無回應）。先加入新配件並驗收，再移除舊配件。
FP2 觀測仍為斷線／90 秒過期未知；HA key 不可當 owner key，Framework 測試使用 Linux CI。


# 舊版本管理與已退場功能

v1.44.0：空調回饋 interval_min 可設整數 1–30、min_adjust_min 可設整數 1–60；預設仍 5／10 分鐘。後端 valid_config、Dashboard 進階欄位與 simulator 必須一致。回饋啟用且冷暖房開機時，其感測器約每分鐘取值；其他背景讀取及歷史仍每 300 秒。sensor_polling 共用每 ID 的鎖與每個 60 秒時段內的讀取結果，sensor_state.update_current 不寫歷史。1 分鐘查詢不等於設備有新測量，保留樣本去重、冷卻等待、關機／未知結果限制。

插件 1.3.0／系統 v1.43.1：`halfDegreeTest` 必須嚴格為 true 才建立 `diagnostic.js` 的純本機配件。與正式空調共用 HAP 介面，但模擬 adapter 不可取得真實 ButlerClient、不可進入 devices map；cached 診斷 UUID 在一般還原前分流，停用僅 unregister 診斷配件。冷暖 minStep 必須在首次 register 前設為 0.5，測試狀態重啟歸 26°C。使用者已確認 Siri 可半度、Apple Home 可顯示，但按鈕仍整度，勿再將協定支援當成 iPhone UI 已通過；驗證見 homebridge/tests/diagnostic.test.js。

v1.43.0 半度目標：`ac_temperature.py` 是 16–30°C、0.5°C 步進和 half-up 捨入的共用規則。回饋啟用保留半度 `最後溫度`，實際 IR 仍為整數；停用時目標歸整但不發 IR、保留 IR 狀態。補償上下界必須 ceil／floor 向內取整。`_ac_saved_state` 是該次保存結果，Dashboard 用它確認後端接受的目標，不能和原始半度請求硬比；語音／排程的 ARG_KEY_TYPES.temperature 使用 num，不能先截斷。Homebridge 1.2.0 需更新並保留配件 UUID／設定。

v1.42.1 回饋 API 的 `evaluate_now: true` 可在保存後立即評估；省略 config 時只讀現有設定，不寫回舊設定。明確評估略過背景週期／啟動等待，仍保留上次命令的間隔、持久化樣本與待確認檢查；不可把它實作成強制 IR 或清除 blocked。純 config POST 保持不送指令的相容契約。

空調室溫回饋（v1.42.0）見 [docs/ac-temperature-feedback.md](docs/ac-temperature-feedback.md)：`最後溫度` 永遠保留舒適目標，IR 補償另存 JSON。設定僅 Dashboard 成員／owner Key 可操作，預設關閉；背景工作不可開關機、重置計時或呼叫一般 handler。所有 AC 命令共用 `ac_feedback.CONTROL_LOCK`，發送前持久化待確認、未知不重送；手動成功保存才解除。修改時同步 controller／API／Dashboard demo 與文件，不把補償 IR 值投影成 HomeKit 目標。

Homebridge 見 [homebridge/README.md](homebridge/README.md)。`homebridge_api.py` 獨立 router 與
`HOMEBRIDGE_API_KEY`，只允許 JSON `HOMEBRIDGE_DEVICE_NAMES` 的唯一啟用 AC；不得把此 Key 加入
owner／device-voice verifier。GET 僅既有快取，POST 重新驗證 Sheet，ctx 限定精確 ID 後才呼叫原 handler。
局部設定保留其他欄位，沒有歷史狀態則拒絕，不暗中套用 defaults。`_ac_state_saved` 表示保存完成，
成功控制但保存失敗回 unknown、不重送。插件依 HomeKit 特徵更新狀態但不可呼叫 SET；防黴回應 fan/on
不可被 HomeKit optimistic off 蓋回。室溫需實際同房間感測器，缺值回 HAP 錯誤。新增類型／模式要同步
allowlist、投影、插件特徵、schema、文件與測試。`npm ci --ignore-scripts && npm test` 在 homebridge/，
測試用真實 HAP／PlatformAccessory 配 fake I/O，不 publish、不控制硬體；後端完整 unittest 仍必跑。
版本以 Dashboard 為準，插件 package 的版本用於打包安裝；不要新增 repo tag／Release。

插件 1.1.0 支援 cool／heat，dry／fan 為同一配件內穩定 subtype 的模式開關；保留原配件 UUID。
TargetHeaterCoolerState 沒有 dry／fan，運行時保留最近冷／暖選擇、Current=idle，以模式開關表達實際模式。
`off_if_mode` 僅接受 dry／fan 的非空清單，且只能與 power=off 搭配；後端以新讀的 Sheet 判斷，
已非該模式時成功 no-op、未知狀態拒絕，不把條件傳入 legacy handler。no-op 也必須去重。
同手勢明確選模式優先於關掉另一模式開關，主電源 OFF 仍優先；未確認結果不可重送或假設成功。
部署先後端再插件，舊後端會拒絕新增欄位。不要把模式關閉改成無條件關機。

v1.39.3 Sheets：`_get_spreadsheet` 持續重用連線，RLock 僅保護建立／失效，不再每分鐘重新認證；google-auth 管理 token 更新，GET 暫時性重試用盡才失效讓下次建立。`update_device_state_fields` 一次讀取最新欄位及 Device ID 所在列，再 RAW 批次写入，缺失／歧義拒寫，未知寫入不重試；不要恢復用 ctx 舊 row index 或長期快取欄位位置。詳見 README、voice-timing 及 `tests/test_sheets_reuse.py`；不提供 Sheets 外部並行編輯的交易保證。

v1.39.2 I/O 精簡：`get_lighting_area_info` 以 `load_area_settings(read_only=True)` 讀既有內容；不得在每次 prompt 組裝時重新建表／ensure_columns，原探索與設定路徑仍負責 schema。`maintain_ac_auto_schedule` 只在實際增刪分支取 worksheet，保留 timer anchor 與先封存後刪除。細分計時的 `parent_span_id` 表示包含關係，不能把外層與子階段相加；詳見 voice-timing 文件與 `tests/test_voice_io.py`。

Siri 獨立指令（v1.39.1）：`process_message(..., voice=True)` 使用 `ask_claude(..., include_history=False)`，一般與降級請求都只送當句；初始讀取五張表，略過對話暫存。背景存檔保留，LINE 預設歷史不變。不得因共用解析器把 LINE 歷史一併關閉；Siri 跨輪省略／確認不再依賴上一句，文件與測試見 README、`tests/test_voice_history.py`。未重写完整 SYSTEM_PROMPT。

語音請求計時見 [docs/voice-timing.md](docs/voice-timing.md)。`request_timing.py` 只在兩個 Siri 路由啟用，以 ContextVar 串起 `[TIMING]`；總時間從路由函式進入算起，不包含代理／threadpool 等待。新計時不可寫入輸入、身分、設備參數或金鑰；`completed` 不是硬體成功。原有模型設定、降級／重試與 `{reply}` 契約保持不變。純觀測不 bump Dashboard 版本。

# 歷史設計：自動夜燈場景指紋（v1.53.0 已退役）

以下僅解釋舊版設計；lighting_auto 已移除，不可照這段恢復主線夜燈引擎。

`lighting_auto` 判斷可不可以自動關燈時，問的是**「這個房間現在亮著的是不是那個夜燈場景」**
（`_is_night_light`），不是「這盞燈是不是 auto 開的」。

**為什麼不能用 ownership**：使用者多半用 **Hue 遙控器 / Hue App** 開燈，那些操作
**完全不經過 home-butler**——我們連知道都不知道。所以「記住是不是自己開的」先天記不全：
手動點開的夜燈永遠被當成別人的燈，天亮了、時段結束了都沒人關（實際遇到的困擾）。
in-memory ownership 還會在 Render 重啟後歸零，同樣的洞再開一次。

**指紋來源是 bridge 自己記的 `scene.status`**（Hue API v2，實機確認有；agent 端由
`_hue_scene_status` 帶出來，掛在 `hue.list_areas` 每個 area 的 `scenes[]` 裡，
不需要額外 API 呼叫）。agent recall、遙控器、App 更新的是同一份欄位，天生一視同仁。

判斷分兩段，**第二段才是主力**：
1. `status.active` 非 `inactive` → 燈此刻就是這個場景。
2. 否則比 `status.last_recall`：這個房間裡最後被叫起來的場景就是夜燈 → 算數。

**為什麼需要第二段**（非顯而易見）：`_fire_scene_on` 會在 recall 之後蓋上規則的
`brightness`，bridge 判定「已偏離場景」→ `active` 立刻掉回 `inactive`。也就是說
**auto 自己開的夜燈，`active` 多半是 inactive**，只靠第一段會連自己開的燈都認不出來。
使用者事後用遙控器微調亮度也一樣。`last_recall` 不受這些影響。
（想只靠 `active` 的話，得把亮度直接編進場景、拿掉那步覆寫——但那會改變實際亮度，
沒做。）

刻意不要求 `last_recall` 夠新：使用者按遙控器電源鍵直接開（不 recall 任何場景）時燈會
回到上次的夜燈狀態，而夜燈仍是最後被 recall 的場景——那確實該算夜燈。

**`auto_on` ownership 留著當 fallback**，沒有刪：agent 還沒更新到會回傳 status 的版本、
或規則的 `scene_id` 不屬於該區域時，`_is_night_light` 回 `None`，行為退回改動前
（只關 auto 自己開的）。所以部署後在 agent 自動更新完成前不會有行為變化，也不會因為
拿不到新資料就亂關燈。

**取捨**：判斷的是「長相」不是「意圖」。把燈調成夜燈的樣子想讓它整天亮著 → 還是會被關；
recall 別的場景 → auto 完全不碰。另外這個機制**救不到**「拿過時亮值誤動作」——過時的
『已經變亮』讀值仍可能把還在暗處的夜燈關掉、下一輪又開，那是感應器讀值新鮮度的問題。
