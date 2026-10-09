# 驗證方式與範圍

## 2026-10-09 TCMb 在 M6 的意義（唯讀實測，僅文件）

M6 Mac mini（macOS 27.0.1）以一次性唯讀 SMC 負載測試確認 TCMb 是整顆 SoC 的最高點：閒置約 53°C、CPU 滿載 102.0°C、GPU 滿載 84.9°C，全程約等於 TVDC 與 TVDG 的較大值。數據與限制見 `agent/macos.md`。沒有修改 collector、sender 或後端；測試期間 CPU 滿載 45 秒，未製造其他負載，未寫入 SMC。

## 2026-10-09 連續記憶體壓力採樣

394 項後端離線測試及 AUTO_UPDATE native 假測試通過。涵蓋 100 減法、0/100、失敗與缺符號、越界與未寫回、level/pct 獨立缺值、嚴格 sender schema。Render 公開 OpenAPI 已確認接收端含可選 pct 後才發布 agent。macOS 27.0.1 靜態檢查確認 Activity Monitor 同一數值來源與計算，無壓力模擬；此版自然自動切換仍待 main CI 與背景驗收。

## 2026-10-09 連續壓力接收契約

393 項後端離線測試通過；新增 pct 接受 0、37、100、null，拒絕布林、字串、小數、越界、NaN／Infinity；舊 level-only payload 保留且 pct 為 null。此階段不改已安裝 collector。

## 2026-10-09 Mac 記憶體壓力（首次真實自動切版通過）

393 項完整後端離線測試通過；native AUTO_UPDATE 假測試通過。涵蓋 sysctl 1/2/4、未知值／逾時、舊 payload 相容、獨立狀態歷史與 sender 白名單。真實非 root 本機採樣為 normal，未送出測試 heartbeat，未製造記憶體壓力。

GitHub 0caaac624c86ea47baa099d311bdf2f622865b5f 的 push CI 成功後，UTC 17:54:45 與 17:55:47 自然新版 ACK，17:55:48 update_confirmed；已安裝版本與採樣 normal 核對成功。同一憑證 DR、不同 cdhash；未手改安裝目錄或手動重啟。

## 2026-10-09 macOS 完整自動更新（首次啟用通過）

公開 sentinel、拋棄式憑證：獨立 0x100 與 System 0x100 Keychain／非 root system LaunchDaemon
均完成 A → B → A；不同 cdhash 使用相同憑證限定 DR，錯誤 identifier／錯誤簽署者皆回 -25293。
每次 UID 正確，前後 ACL 雜湊相同；公開 item、暫時 jobs／目錄與測試私鑰均已移除。
System 實驗含單一惰性 creator-cdhash partition，未當成 0x200 的跨版本證據。

完整更新器的離線測試涵蓋 exact SHA／CI、損壞 blob、重複與文件更新、相依套件變更拒絕、
新版採樣失敗、自然回報缺失、程序中斷回復，以及首次啟用後半段失敗恢復舊 plist／ACL。
真實本機 signer helper 已在拋棄式身分測試 create/import/lock/unlock/sign；兩個完整 sender＋collector
版本均完成編譯、strict 簽章驗證與版本查詢：不同 cdhash、相同 DR。此建置實驗未使用正式 key 或 POST。
完整 Python 離線測試 387 項通過（含 macOS 66 項）；AUTO_UPDATE 原生假測試與正式 root guard
的 signer helper 編譯通過；Security deprecation 警告保留。

首次安裝會先執行獨立公開 System item 的實際移交／回復，成功才接觸正式 ACL。
正式 item 遷移已完成，見下方首次啟用結果；GitHub main 驅動的實際升級／回復、重開機未登入
及 0x200 尚未驗收。詳見 [自動更新](../agent/macos_daemon/auto_update/README.md)。

### 首次啟用前置失敗與修正

首次執行停在 PUBLIC metadata 查詢：bootstrap 使用 update-probe 前綴，但原公開 helper 僅允許
signing-probe，回 `probe_validation_failed` 被誤報為 already exists。未建立正式切換 journal／current，
舊服務仍持續自然 acknowledged。修正版 helper 已用實際同 service 的唯讀 metadata 查詢取得
-25300（不存在），未讀值、未改正式 item。補上錯誤分類與嚴格限定在正式切換前的接續流程；
保留本機簽署身分，先確認舊 PUBLIC item 不存在才能清除該唯一暫存目錄。修正後完整離線測試
392 項通過（macOS 71 項）；其後重試結果如下。

### 首次正式啟用結果

2026-10-09 01:22–01:24（Asia/Taipei）：公開 System item 的新 reader／原 reader 與 ACL metadata
完全復原通過；安裝 phase=active，來源 1a61d30。非 root system job 已連續三次自然 acknowledged，
均帶該版本完整 SHA，last exit 0。舊 startup plist 已移除，新 telemetry 每 60 秒及 root updater
每 300 秒的 LaunchDaemon 均已載入；保留原 binary／plist 備份，未重輸或匯出 API key。

已安裝 runtime 可匿名讀取 GitHub main 和 CI。首次更新檢查得到 github_check_failed／HTTP 404：
當時 main 仍為 188def0，尚不含 auto_update/trust.swift；此為合併前的缺檔，現役 telemetry 不受影響。
main 合併後先出現 agent_unchanged，其後 0caaac6 自動切版成功，見上方實測。

## 2026-10-04 TCMb／TCMz 本機讀取準備

GitHub main 5d3b135 基準。新增獨立唯讀溫度工具，不改 heartbeat 或已安裝常駐。M6 真實無 sudo 讀取 TCMb=46.05°C，TCMz=null。4 項離線測試涵蓋 datatype／有限與範圍值、缺失 max 不替代、權限失敗、連線清理及禁止寫入指令。缺值保留 unavailable；OSHI die average/max 定義不代表 M6 官方確認。無 push、部署、Keychain 或服務修改。


## 2026-10-03 Mac mini 獨立 telemetry collector（未部署）

基於 GitHub main `fe67e52` 獨立 checkout 新增 `agent/macos_metrics.py`，沿用既有 heartbeat/status schema，不修改後端 API、Sheet 欄位、Windows agent 或設備控制。預設只 stdout；外送須指定 --send/--url 和既有 key，禁止 redirect／環境 proxy，HTTP 僅 literal loopback。沒有常駐安裝、自我更新或秘密讀取。

Apple M6、12 核心、24 GiB、macOS 27.0.1 真實無 sudo 採樣：CPU 62.6%、RAM 56.6%、GPU Device Utilization 54%；load average 約 3.38/2.51/2.08 只保留本機。這是單點採樣，不是性能評測。可靠無特權攝氏來源未驗證，CPU/GPU 溫度固定 null。

Python 3.12.14 後端 `unittest discover -s tests -v` **249 項通過**（既有 243＋新增 6），新增測試涵蓋 driver counter 合法值／0／缺值、預設不外送、HTTPS 與 key 邊界、loopback 收件器、redirect 拒絕，以及真實 PCHeartbeatRequest schema → pc_state snapshot（阻止 Sheet writer）。編譯檢查、git diff --check 通過。真實取樣使用獨立 venv 的 psutil 7.2.2，套件由 PyPI 下載；未裝特權 helper。

沙箱最初阻止 GitHub DNS、sysctl 與 localhost bind，限定動作核准後完成，不是系統需要 sudo。沒有修改相機 task-4／8768／8771、HA、家電、DNS 或 VPN；沒有正式 heartbeat、Sheets／LINE 寫入、push、部署、launchd 或 persistent credentials。Dashboard 對應 v1.62.0 模擬驗證見其 repo。正式外送目的地與 key 供應、常駐方案仍需使用者核對授權。

## 2026-10-03 LINE 家庭成員入口驗證（發布前離線驗證）

以 GitHub main `a54066f356b92020e43560a2f1bfc25d640a3f0b` 建立獨立修復 checkout。
`main.py:handle_message` 在所有文字分支前要求非空發話者 ID、家庭成員精確匹配與啟用狀態；
驗證失敗早退，不進 assistant、控制、廣播、配對、全家庭資料載入或對話存檔。
合法成員配對、兒童角色、風格查詢與廣播維持；group／room 仍只使用發話者 ID。
新成員的 ID 由管理者從拒絕 log 核對登錄，不再依賴未授權對話寫入。

macOS／Python 3.12.14 的獨立 venv，依 requirements.lock 安裝 CI 所需 FastAPI／HTTPX：
`python -m unittest discover -s tests -v` **243 項通過**（既有 231＋新增 12）；
`python -m compileall -q .` 與 `git diff --check` 通過。
新增 `tests/test_line_member_auth.py` 執行真實 handler 函式、以 fake LINE／Sheets／assistant／thread 隔離；
涵蓋啟用、未知、停用、姓名冒充／ID 非精確匹配、所有文字分支拒絕、group／room、缺 ID、空表／欄位、
worksheet／records 讀取失敗、錯誤資料、合法廣播收件人、成人／兒童配對與失效碼、拒絕回覆失敗不落入存檔。
沒有 import／啟動正式 app、沒有付費模型呼叫、真實 LINE、家庭 Sheets 或家電操作。
本次未改 HA／Homebridge，未重跑其獨立 framework suites；未取得修復版 GitHub CI／部署結果。
上述離線驗證階段未 push、建立 PR、merge、部署或重啟服務；未修改 Dashboard 顯示版本。

## 2026-10-03 全部空調套用 Apple Home 恆溫器與自動風速

使用者在主臥完成後授權全部空調採相同設定，並要求省略非必要測試。本次沿用已安裝的
`ac_room_temperature` 1.1.0（功能 SHA `eabf44a9bec7a7800a8bc6394e97fcb55e9d6672`），未修改程式、
下載依賴或重新安裝；沒有重啟 HA Core，也沒有下達空調電源、溫度、模式或風速指令。

既有 HomeKit Bridge 的客廳、次臥配對 climate 從 Heater Cooler 改為 Thermostat，主臥保留 Thermostat；
橋接模式、匯出類群、三台原生空調排除清單及裝置觸發器維持原設定。客廳、次臥配對的固定自動風速
選項均儲存成功，室溫來源分別保留同房間 Hub 2 溫度；主臥沿用先前啟用的選項。
一次重新載入既有 HomeKit Bridge 後，HA 回報「整合已重新載入」。

macOS 家庭主畫面確認三台均顯示目標溫度：客廳「降溫到28.0°」、主臥「降溫到27.0°」、
次臥「降溫到26.0°」；客廳及次臥房間頁確認沒有空調風速卡片。這些是回讀的既有設定值，
本次未調整溫度。Dashboard／HA 原生空調仍保留手動風速，室溫仍由感測器提供。
僅執行必要的設定成功與 Home UI 核對，未重跑上一版已通過的離線測試；iPhone 同步、
各房間 Siri 句型及實體風速效果仍待使用者驗收。

回復固定自動風速時，取消對應配對選項並重新載入 HomeKit Bridge；回復舊空調樣式時，
在同一橋接設定流程將對應 climate 改回 Heater Cooler。保留配對與識別碼，不需刪除整合。

## 2026-10-03 主臥 Apple Home 固定自動風速

使用者指定只調整主臥，Apple 家庭移除風速控制、固定自動，Dashboard／HA 原生空調保留手動風速。
`ac_room_temperature` 1.1.0 新增逐配對 `fixed_auto_fan` 選項，預設關閉；啟用後移除 FAN_MODE 與
fan_mode／fan_modes 屬性，目標溫度、室溫與配對識別碼保留。非關機指令視需要先恢復原生 auto；
已是 auto 不多送指令，關機不附帶風速。保存、重載與感測事件均不送設備命令；未知不重送。
原生手動風速可保留到下一次配對操作，不設背景強制規則。

macOS Python 3.14.7／HA Core 2026.9.2／pytest-homeassistant-custom-component 0.13.365：
室溫配對 17 項、完整 HA 119 項通過；既有後端測試環境的 231 項離線測試通過，編譯及 diff 檢查通過。
新增測試覆蓋能力隱藏、保存與停用、原生手動保留、風速／主指令順序、關機、失敗不重送、
缺失 auto 能力、同配對並行及風速指令後來源替換。測試皆以假的原生服務執行。

驗證環境準備曾誤建完整 HA venv，下載非本功能需要的傳遞依賴；停止該下載後改用既有匹配環境完成驗證。
最初使用系統 Python 3.9 執行後端測試因缺 HTTP 依賴失敗；上列 231 項為改用既有正確環境後的結果。
未 push、未取得此變更的 CI 結果、未安裝到家庭 HA、未重啟或發送空調指令。
家庭 UI 唯讀確認當時主臥風速為自動；這不代表新版選項已生效或實際設備已驗收。
發布後需用通過 CI 的 SHA 更新本整合、重啟 HA，僅主臥啟用選項，確認 HomeKit 風扇服務移除與溫度卡，
再由使用者驗收 Siri／Apple 家庭與 Dashboard 手動風速的互動。

發布／安裝追記：使用者同意後，功能提交 `eabf44a9bec7a7800a8bc6394e97fcb55e9d6672` 已推送 main，
[GitHub CI 三項工作通過](https://github.com/CZLin-TW/home-butler/actions/runs/37035003116)。
家庭 HA 2026.9.4 的 Terminal & SSH 已以同一固定 SHA 安裝本整合，舊版保存於
`/config/ac_room_temperature-backup-20261003-003859-205`。HA Core 重啟後管理頁正常，
整合頁顯示 1.1.0；主臥保存固定自動選項成功，重開選項確認勾選值為 1，室溫來源仍為主臥 Hub 2 溫度。
未修改客廳／次臥選項。配對重載後 Apple Home 曾保留舊風扇服務；在既有 HomeKit Bridge 重新載入，
管理頁回報「整合已重新載入」，macOS 家庭主臥房間確認「主臥空調風速」卡片移除，
「主臥空調」仍顯示「降溫到26.0°」。HA HTTP 200，這是服務／UI 回讀，不擴張為實體設備驗收。
本次沒有發送空調電源、模式、溫度或風速指令；手機同步、Siri 原句型與跨入口風速效果仍由使用者驗收。
回復時取消主臥固定自動選項並重載 HomeKit Bridge；程式回版可使用上述本地整合備份，再重啟 HA。

## 2026-10-02 主臥 HomeKit 溫控器對照試驗

使用者提供的 HomeKit 除錯紀錄顯示 HA 為 2026.9.4：23:27:50「主臥空調冷氣調到26度」
寫入 CoolingThresholdTemperature=26，配對 climate 隨後回報 cool／26；
23:28:01「打開主臥空調27度」寫入 Active=1 與 HeatingThresholdTemperature=27，
未寫入冷氣溫度或切換模式，後續配對 climate 仍為 cool／26。
這確認兩句走不同溫度欄位，不證明 Siri 內部決策，也沒有捕捉先前「26變30」的操作。
完整家庭 log 留在使用者原下載檔，不複製至 repo。

經使用者授權，在既有 HA HomeKit Bridge UI 僅將主臥室溫配對改為溫控器；
成功儲存後重新進入設定，確認目前類型標籤為 Thermostat 且選項為溫控器。
客廳／次臥仍為 Heater Cooler，原生三台排除清單及室溫感測器保留。
未發送實體空調指令，未重啟 HA，未修改 HB／HA 程式、Render 設定或部署；本次僅更新文件，未 push。
使用者待驗：Apple Home 顯示、Siri 對高／低於室溫的溫度設定、開機合併指令及模式是否維持正確，
另檢查參照主臥舊控制項的場景／自動化。設定成功不等同語音或實體空調驗收通過。

2026-10-03 追記：使用者回報改成溫控器後可以正常調溫，但合併卡片未顯示設定溫度。
macOS Apple 家庭 UI 確認合併溫控器與風扇的卡片顯示「全部已開啟」；
既有風扇的設定頁提供「手動／自動」切換與手動風速滑桿。
依使用者要求保留簡潔的原生控制，改為顯示個別方塊，將風扇服務命名為「主臥空調風速」，
關閉此風速卡的「加入家庭顯示方式」，保留在主臥房間；未新增 HA 實體或 HomeKit 服務。
重開 macOS 家庭 App 回讀確認主畫面僅保留主臥溫控器，卡片顯示「降溫到27.0°」。
未操作電源、溫度、模式、風速或自動切換；當時風扇 UI 顯示手動／67%，保持原值。
iPhone 同步後卡片顯示、自動風速實體效果與既有場景仍待使用者確認；
使用者的調溫成功回報不擴張為所有模式與全部語音句型皆通過。

## 2026-10-02 Mac mini 遷移與 PC 指標分離

本 repo 僅更新文件、維護既有發布紀錄，沒有改 HB／HA 程式、API 或模型提示。HA 備份恢复到 Mac HAOS VM 後，使用者確認原有控制可用；本次另完成兩劇院程序遷移，HA 保留原 Theater Agent entry 改址。HB 唯讀劇院摘要回 `agent_id=home_assistant`，Mac 兩程序版本與新鮮心跳、Apple TV 查詢正常；Windows agent 僅宣告 `pc_monitor`，原劇院排程及舊 HA／Homebridge VM 自動啟動已停用。

使用者重開 Mac 後，未用 SSH／共享螢幕登入前，MBP 讀到 HA HTTP 200 與劇院健康／Apple TV 查詢。未操作實際家電作回歸測試、未發 LINE、未改 Sheets；停電復電、實體劇院連動及長期異機備份仍待驗收。實際 theater-agent 發布／CI／更新由該 repo 記錄，不以本文推定新版本已部署。

發布追記：文件提交 `4834826` 已推送 main，[CI 三項工作通過](https://github.com/CZLin-TW/home-butler/actions/runs/36892349157)。本次沒有後端程式變更；未取得 Render 正式程序 SHA，不以 CI 代替部署確認。

## 2026-09-23 同名待辦定位（系統 v1.61.2，未部署）

Python 3.12.14 臨時 venv 依 requirements.lock 安裝 FastAPI／HTTPX 後，完整 231 項後端離線測試與 compileall 通過。
新增 8 項測試涵蓋 schema 參數經 action 轉換到指定 ID 完成、同名不同日期／同日不同時間、私人選項隔離、
無效／已完成／重複 ID 不退回名稱、模型清單的 ID 可見性、修改歧義、澄清回覆不被模型成功文案蓋掉及後續選取、
指定完成後只停止該筆提醒，以及 Notion 同步保留記號不復活。

使用合成資料與假 Sheets／模型／推播；沒有正式 LINE、Notion、Sheets 或家電操作，沒有真實付費模型辨識評估，
口語上下文辨識仍待上線觀察。系統 Python 3.9.6 與初始 bundled Python 缺少測試依賴，初次完整測試未通過；
上述成功結果來自補齊依賴的臨時 Python 3.12 環境。Dashboard 僅更新版本至 1.61.2，UI／API／demo 契約不變。
兩個 repo 尚未 push／部署。

2026-09-23 發布進度：使用者授權上線後，功能提交 `723a69646d3b378de2cdee7e73cba24478af0259` 已推送 main；GitHub Actions 35820925967 三項工作全部成功。公開後端 `/` 回傳 `{"status":"ok"}`，但此端點不提供 SHA；Render 管理頁待登入，尚未確認該提交已在正式程序執行。未發 LINE 或操作正式待辦。

## 2026-09-20 ToDo 多區域提醒（系統 v1.61.0）

本機 223 項後端離線測試通過，含新增 6 項多區域測試：舊單值與 JSON 清單編解碼、
新增／編輯／停用、無關欄位編輯保留所有區域、四個 API 請求模型與轉送、週期模板產生待辦，
以及到期篩選、各區域派送、同區域同分鐘去重、未知結果當分鐘不重送和下一分鐘再提醒。
使用 fake Sheets、假時鐘與設備傳輸；未改家庭 Sheet、未控制真實燈具或發 LINE。
Dashboard 本機模擬介面另已確認多選保存、重載後保留、取消其中一區及空選取阻擋。
這些檢查不等同正式部署或實體燈效驗收；部署需先後端，再 Dashboard。

2026-09-19 HA 本地劇院開關：新增 `theater_agent` 1.0.0 與 Home Butler 1.7.0。
本機後端 217 項離線測試及 HA 程式／測試語法編譯通過。
Python 3.14.7、官方 Home Assistant 2026.9.2、pytest-homeassistant-custom-component 0.13.365
於 macOS 執行完整 HA 測試：108 項通過，其中 39 項為新增劇院測試。
涵蓋三個 switch、HB 共用控制器、未知不重送、輪詢／寫入互斥、移除不 fallback、
改址保留實體、重新驗證，以及劇院離線不阻止 Home Butler 載入。
案例位於 `homeassistant/tests/test_theater_agent.py`，Agent I/O 全部模擬。
提交 `3054213c7793a275bb207a76f6d52f0e00c2dbc0` 的 [Linux CI](https://github.com/CZLin-TW/home-butler/actions/runs/35444138747)
三項工作均通過。家庭 HA 完整備份後安裝兩個整合，設定檢查及重啟成功；
新增本地整合並在 Home Butler 選取共用控制器。Safari 確認 HA 三個 switch 可用，
Dashboard 劇院卡在線，摘要 API 回傳 `agent_id=home_assistant`，旗標與遷移前一致
（KEF=true、電視畫面自動關閉=false、AVR 隨電視開啟=true）。Render 既有 HA 路徑無需變更。
本次未送出 flags 寫入、未操作實體家電、未中斷 Render 測試；不能宣稱實際連動或斷網操作已驗收。
畫面核對時修正兩個開關的中英文名稱，與 Agent 實際功能及 Dashboard 一致。
正式切換與回復順序見 [部署說明](../homeassistant/theater-agent.md)。

2026-09-14 v1.57.0：時數僅由 Sheet 管理，Dashboard 使用原排程區編輯／刪除自動產生的本輪排程。
238 項後端測試與 34 項 Dashboard 測試、lint、正式 build 通過；涵蓋編輯後重啟保留、刪除不補回、off 清理不碰未來手動排程、改成 on 保留、未知不重送及舊 paused 相容。
封存測試確認已關閉 cycle 可清理而不等待同設備的未來手動排程；舊設定 POST 回 410 且不讀寫 Sheet。
Chrome 模擬家庭確認無時數面板、自動排程可編輯／保存／重新載入後保留、刪除後清單消失。
390×844 時頁面 scrollWidth=375，無水平溢出；不是 iPhone Safari 實機驗收。
未修改家庭 Sheet 時數、未操作實體空調或正式排程。
正式部署確認：Render 最後成功提交為 `0d6cbd997941e87a946b6dcf41a197d809909a0c`；
[後端 CI 34856288832](https://github.com/CZLin-TW/home-butler/actions/runs/34856288832) 成功。
Dashboard `c0e6bdd8af64cd88b39f4c6c5893148f68e13915` 的 [CI 34856304872](https://github.com/CZLin-TW/Dashboard/actions/runs/34856304872) 成功，正式頁與版本 API 為 1.57.0。
正式頁唯讀展開原有自動關機排程，原期限保留，顯示自動產生與編輯／刪除按鈕，無時數面板；未按保存或刪除。
實際關機後自動清理與下次開機重建已由離線測試驗證，家庭實機週期仍待使用者觀察。

2026-09-14 v1.56.0（部署前驗證）：232 項 HB 離線測試、34 項 Dashboard 測試、lint、正式 build 與 Python 編譯通過。
自動關機測試涵蓋首次 on、off→on、調溫不重置、時數修改／停用、相同設定不重置、手動優先含未知結果、離線／不確定不下令，以及結果持久化跨請求不重送。
實際 HTTP 路由使用假 Sheets，確認只改既有時數欄位且保存不控制家電；嚴格整數驗證與完整 key／橋接／語音 key 邊界通過。
真實 HB WebSocket 測試收到自動排程的 power=off，回覆前 Sheet 已先記待確認，legacy handler 未被呼叫。
一般計時 tick 重用既有 batch 資料，測試確認狀態不變時不再取得 worksheet、不寫 Sheet；必要異動才即時定位列。
Chrome demo 驗證 HA 測試空調保存 3 小時顯示預計關機時間、收合重開保留值、改 0 停用；390×844 的欄位高 38px，無水平溢出。
本次沒有寫入家庭時數或操作實體家電；不需更新 HA。上線後原 Sheet 有效時數會啟用，首次接手已開機從觀察起算，實體到期反應待使用者驗收。

v1.56.0 部署確認：Render 功能提交 `27784f7aaf8bd7343fe9661ba45b88ee8a6f8a72` 已啟動並顯示 service live，HA WebSocket 重新連線成功。
HB [CI 34852889166](https://github.com/CZLin-TW/home-butler/actions/runs/34852889166) 與 Dashboard
[CI 34852924077](https://github.com/CZLin-TW/Dashboard/actions/runs/34852924077) 均成功。
Dashboard `f27bd3a3013ac935a60fb2ab1e8c598a2c1aa95d` 正式頁面及 `/api/version` 均為 1.56.0。
正式頁唯讀展開三台空調「自動關機」，均成功讀取 Sheet 既有時數；已開機者顯示計時中與預計關機時間，其餘顯示等待下次開機。
未在正式頁保存設定或送出控制；實體到期關機仍待使用者驗收。

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

2026-09-08 空調室溫補償（系統 v1.42.0）：Python 3.12 的 157 項離線測試及變更模組編譯檢查通過，Homebridge 18 項測試通過。新測試涵蓋冷暖補償方向、保持舒適目標、死區與上限、同樣本去重、過期／關機／送風除濕不調整、重啟等待、未知結果持久化暫停、與手動關機共用鎖、設定不送 IR、獨立 API Key 邊界，以及缺欄拒絕部分保存。未發送真實家電指令，實際室溫穩定性仍待家庭環境觀察。詳見[功能說明](https://github.com/CZLin-TW/home-butler/blob/cffe7358e1ec84e66bac4c5ce3e1df40848c45c0/docs/ac-temperature-feedback.md)。

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

## 2026-10-04 TCMb / TCMz integration (local only)

Optional smc_temperature={tcmb_c,tcmz_c} travels collector -> strict backend schema -> bounded current/history -> Dashboard. No CPU/GPU field reuse. Null, unsupported and missing sensors remain unavailable; labels disclose AppleSMC, OSHI interpretation and unconfirmed M6 mapping. New temperatures have in-memory history only (maximum 24h); existing Sheet columns are unchanged and restart loses this history. Old agent payloads remain compatible.

Backend: 253 offline tests passed; after adding the child-timeout/invalid-value case, all 7 targeted collector/schema tests passed. Dashboard: 39 tests, typecheck, lint and production build passed. Native staged sender fake-only tests passed for strict nested fields, ranges, booleans, nulls and unknown fields; no Keychain/network access. Actual localhost demo UI confirmed TCMb value/line, TCMz unavailable and source/retention labels. IAB screenshot tool failed, so no pixel screenshot verification claimed. No push, deployment, runtime/LaunchAgent/Keychain changes.

Mobile DOM verification: 390px viewport, document scrollWidth390; TCMb label/value, TCMz unavailable and source text present. Real collector Python -I sample (stdout only, no send): TCMb46.3C, TCMznull.


## 2026-10-04 macOS daemon source consolidation（未部署整理版）

基準為 GitHub `main` e7246a9，獨立 feature branch；既有 collector／後端 API 不變。
收斂原生 sender、source-only builder、範例設定、System LaunchDaemon 安裝、續接、回滾及唯讀診斷。
實際設定、機器識別、cdhash、runtime、二進位、log、Keychain 與秘密不加入新增檔案。

驗證：27 項 Python fake OS／生命週期回歸（含 partition 被 helper 重置的拒絕、legacy/modern/unknown
格式、已成功續接不重送、未完成紀錄停止、SIGHUP／切換失敗回復、未知 job 不碰、並行操作拒絕）；
原生 Swift fake-only tests（payload、UTF-8 假 key roundtrip、採樣先於 key、明確 acknowledgement、
redirect、partition policy、非 root launchd）；完整 Python 後端 281 項測試通過、編譯、Black 格式、shell 語法、
`git diff --check` 與 `.invalid` 範例的本機完整套件建置。沒有以新版執行真 Keychain、採樣或 heartbeat。
Security SDK deprecated 警告保留。macOS CI 工作僅編譯／跑假測試，尚未在 GitHub 執行。

先前部署的本機版本已由使用者完成切換，收尾唯讀確認 system job／非 root UID／60 秒／exit 0、
自然 acknowledged、舊 GUI job 停用及實際 System database 0x100／唯一 sender ACL。
該證據不等於本次重構 binary 已部署，亦不等於 reboot-before-login 已驗收；本次不重啟、不改服務。

秘密檢查範圍為本次 staged additions／新增檔案與 shallow clone 中可見的相關 collector 歷史；
採 token/private-key/credential-assignment 特徵與本機識別規則、路徑／產物清單及人工 diff 審查。
不讀真正 API key 進行比對，沒有宣稱完整上游歷史或所有高熵值皆無秘密。
既有上游文件已有先前设备資訊；本次未擴散新的本機識別，也不改寫上游歷史。


## 2026-10-05 vision control phase 2（離線、未註冊）

從 remote main cd5da63 建立隔離 feature branch。新增 vision.v1 shared protocol、
scoped hashed credential registry、async hub 與未註冊的 router factory；不改 main.py、
HA 通道、Sheets、環境設定、正式憑證或現役服務。設計與啟用授權見 [vision control](vision-control.md)。

驗證：新的隔離 Python 3.12 venv 依 requirements.lock 安裝 FastAPI/httpx；完整
`python3 -m unittest discover -s tests -v` **303 項通過**（2.886 秒），其中新模組22項。
初次 sandbox 執行只有既有 metrics loopback fixture bind 被拒絕；授權重跑完整套件通過。
跨 repo `scripts/test-vision-pair.py --floor-checkout /path/to/floor-presence` **5 項通過**，
shared protocol byte-identical；floor 完整142項亦通過。Python compile、git diff --check 通過。
測試僅 fake credentials／synthetic data；HTTP/WebSocket 使用 TestClient，不建立外部連線。
既有 TestClient/httpx 相容性 deprecation warning 保留，無測試失敗。

registry／session／去重均非持久化，只支援單 process；未實作 production enrollment、
connector、真實 config writer、模型載入、媒體串流或正式 HA publish。不宣稱真相機整合完成。


## 2026-10-05 actual loopback control milestone

正式 main 接入 opt-in integration，預設disabled；即使VISION_CONTROL_ENABLED=1也為空registry，
本次沒有設定環境或注入正式credential。隔離fixture提供真正HTTP/WS127.0.0.1ephemerallistener。
全套Python **310 passed**（4.291s），視覺focused29項中5項actualsocket。
跨repo full-chain runner通過6項checks：真HTTP BFF→HTTP HB→outbound WS→temporary disk；
兩client同revision得到200/409，第二adapter reload確認revision1。沒有mock fetch。
floor全套154、Dashboard53、lint/typecheck/build、production-start browser9項通過。
僅新增測試venv依赖，未改現役環境。批准候選在 vision-activation-proposal.md，尚未執行。

## 2026-10-06 status-only pilot 程式碼（未部署）

main 改用預設停用的 status-only installer；SQLite 僅存憑證摘要與 scope／到期／撤銷 metadata，
每次授權重新讀 DB。缺失／損毀／不安全權限／檔案替換拒絕；offline init 建立專用 authority lock。
單 filesystem flock 與 worker 宣告 guard 不代表跨 replica 安全；render 尚未配置持久 disk。
沒有真 token enrollment、正式 env、部署或真 mini 連線。

完整 HB `python -m unittest discover -s tests -v` **334 passed**（3.166 秒）；
其中新 pilot 9 項，覆蓋 digest-only、持久 revoke 與重啟、雙 authority、storage fail closed、
expiry、status-only routes、default-off、CLI private secret-file 與成功 status 回覆。
首輪只有既有 main AST assertion 仍期待舊 installer；按新入口更新後全套通過。
完整測試使用已授權 synthetic ephemeral loopback fixtures，未 import 家庭 main 做測試。
`git diff --check` 通過；TLS local-CA 跨程序 runner 由另一驗證工作接續，這裡不宣稱已完成。

## 2026-10-06 existing login and Sheets vision integration (not activated)

Full `python -m unittest discover -s tests -v`: **349/349 passed**, 3.268 seconds; log `/tmp/hb-sheets-tests.log`. The 15 new Sheets authorization tests use fake readers and fake server keys, never real Sheets or household credentials. Coverage includes startup denial, shared reads, 30-second refresh/60-second monotonic TTL, immediate failure denial, singleflight timeout, read-start aging, shutdown late completion, enabled members/kid/grants/session expiry, rejection of household keys as device credentials, revocation and same-ID digest rotation, late-result denial, replaced authority lock and mocked read-only source transport.

Independent read-only security review verified credential binding, shutdown publication guards and authority PID/inode checks; no remaining definite blocker was found. This validates code and offline fixtures, not actual Sheets/Render/Keychain/camera deployment. Global pairing/login code was unchanged. Dashboard records the corresponding cross-process TLS integration results.
完整349項後補上 closed snapshot 禁止再次啟動 read 的小型 guard；相關15項重新通過（0.061秒）。

## 2026-10-06 owner-only local health 候選（未啟用）

明確 VISION_STATUS_OWNER_USER_ID pin 與啟用成員／status grant 缺一即拒絕，不查詢或推測 owner ID。
其他 member 即使有 grants 仍拒絕；owner 回傳 preview/edit 固定 false，kid 與可信 expiry 規則維持。
shared protocol 新增 strict local-health metadata variant；不接收圖片／ROI／URL／模型宣稱。
測試未呼叫固定本機 health URL、真 Sheets 或 Keychain；未啟動正式服務。

完整 HB **354 passed**（3.287秒），其中新增 owner／health 5 項與更新的 snapshot fixtures；
涵蓋缺失／無效 owner pin、其他有 grant 的 member、owner 停用／缺 status、kid、健康／失聯格式、
未知私密欄位拒絕與 synthetic discriminator 保留。`git diff --check` 通過。
實際 native health reader 與 Dashboard SSR 隔離整合由各自模組測試，不以本 HB payload 單元測試取代。

## 2026-10-06 status-only 部署重疊修正（未部署）

Sheets status-only installer 移除 single-authority ACK／filesystem lock 依賴；保留每 container
worker=1、TLS gate、owner/member/grant/session-expiry/device digest／nonce 檢查。
每個 instance 只使用自己的 device session，routing miss 回 503 device_unavailable，不做轉送或 fallback。
status cache lookup 前先要求 live local session，且拒絕新 session 取回舊 nonce 的快取結果。
media／SQLite authority 與家庭 scheduler/write 未修改；不宣稱整個 HB 已具備跨 replica 協調。

完整 HB **359 passed**（3.364 秒），新增5項雙 hub rollout/routing/disconnect/revoke 回歸；
既有 installer 測試改成兩個無 lock／無 ACK 的 app 可同时 startup。
獨立唯讀 review 無 definite blocker；文件已明示 routing miss／各 snapshot 撤銷延遲與非高可用限制。
只用假 reader、假 token、injected device send；完整套件既有 synthetic loopback fixture 維持。
未連 Render、真 Sheets 或平台服務，沒有 Redis、disk、secret 或部署設定變更。diff check 通過。
