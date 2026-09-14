# Home Butler：HA 主動連接 HomeButler

v1.50.0 Hub 2 推送通知：既有 SwitchBot → Render Webhook → HA WSS → 原生 API 驗證讀取。
HA 仍管理光照實體與夜燈規則；這條快速資料路徑依賴 Render，詳見 [Hub 光照](hub-light.md)。

Hub 2 光照可透過獨立 [光照等級整合](hub-light.md) 補入 HA；其 1～20 級不是 lux，
目前不加入本整合的 illuminance 同步通道。

若 Apple Home 的空調室溫固定 21°C，使用獨立本機整合 [空調室溫配對](room-temperature.md)
補入可手動更換的溫度感測器。它不依賴本頁的 Render 連線，也不恢復回饋補償。

同步選定存在／亮度感測器；1.1.0 可額外允許 HomeButler 操作已遷移的原生 SwitchBot Cloud 空調。
1.4.0 新增 [感測器配對與 Hue 控制](sensors-and-hue.md)，保留原歷史與 Hue 區域 UUID；必須完成兩端設定才切換來源。
1.2.0 可額外勾選 [IR 電扇按鈕](ir-buttons.md)，只接受 switchbot_ir_buttons 平台的明確選取。
適用 HA OS；開發目標 Core 2026.9.2，實機驗證狀態見 [驗證紀錄](../docs/verification.md)。
不需要 Aqara 開發者帳號、不需要把 HA 的 8123 port 對外開放，也不用重新配對 FP2。

## 安裝

1. 先部署支援本整合的 home-butler 後端，再部署 Dashboard v1.46.0。
2. 在 Render 新增環境變數 `HOME_ASSISTANT_API_KEY`：使用獨立隨機金鑰（至少 32 字元）。
   可用密碼管理器產生 64 位英數密碼，或在本機 Python 執行
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`。
   這不是 HA 長期存取權杖；不可沿用 owner、Siri、Homebridge key。不要貼到聊天、repo 或 URL。
3. HA「設定 → Apps（應用程式；部分介面稱附加元件）」檢查已安裝清單。
   若沒有 **Terminal & SSH**，從 App Store 安裝官方版本。若找不到，先到個人資料開啟進階模式。
   啟動後選「開啟網頁介面」。本機網頁終端即可，不必對外開 SSH port。
4. 取得通過 CI 的 home-butler 完整 commit SHA，將下面 `COMMIT_SHA` 換掉，再在 HA 終端執行：

   ```sh
   curl -fL https://raw.githubusercontent.com/CZLin-TW/home-butler/COMMIT_SHA/homeassistant/install.sh -o /tmp/install-home-butler.sh
   sh /tmp/install-home-butler.sh COMMIT_SHA
   ```

   安裝器只替換 `/config/custom_components/home_butler`，若已有舊版會先備份到
   `/config/home_butler-backup-日期-程序編號`。不改 configuration.yaml、HomeKit Bridge 或 FP2 配對。
   若偏好手動：把 repo 的 `homeassistant/custom_components/home_butler` 整個資料夾放入
   HA 的 `/config/custom_components/`。File editor 單檔上傳不適合大量套件檔案。
5. 重新啟動 **Home Assistant**（不是只重載 YAML），「設定 → 裝置與服務 → 新增整合」找 **Home Butler**。
6. 輸入 Render HTTPS 網址、同一把專用金鑰，勾選 FP2 的區域存在與亮度感測器。
   網址只填 HTTPS origin，不加 /api 或 HA 內網網址。
7. Dashboard → 裝置 →「空間感測」檢查有人／無人與 lx。
   後續在 HA 整合的「設定」修改勾選清單；清空可停止分享所有讀值。

## 更新、金鑰變更與復原

使用新 commit 重跑安裝器並重啟 HA，原整合設定保留。
換 key 時先更新 Render，再於 HA 重新驗證；若連線尚未觸發重新驗證，可重新載入整合。
刪除 HA 的 Home Butler 整合即可停止連線，既有 FP2／HomeKit Bridge 繼續運作。
要復原套件，將目前 home_butler 資料夾移到備份位置，再把安裝器留下的前一版備份移回原路徑並重啟 HA。
後端 key 可另外刪除停用端點；Dashboard 先回退、後端後回退。

## 驗收順序

- 人進出每個選定區域：HA 與 Apple Home 先變，Dashboard 約下一次 5 秒更新跟上。
- 沒人時為「無人」，不是「未知」；零亮度顯示 0 lx。
- 暫時停用 **Home Butler 整合**：Dashboard 下一次更新顯示未知，Apple Home 的 FP2 仍可使用。
- 重新啟用：只顯示當下快照，不補播離線事件。
- 改區域名稱／entity ID：穩定 registry ID 保留對應；刪除來源顯示未知；取消勾選則移除卡片。
- FP2 本身離線：要等 HA 原整合判定 unavailable；HomeButler 的心跳不是額外的實體探測。

## API 與界線

| 入口 | 權限 | 用途 |
| --- | --- | --- |
| GET /api/home-assistant/health | 專用 HA key | 協定版本／能力 |
| WSS /api/home-assistant/ws | 首個 hello frame 的專用 HA key | HA 主動連入，同步選定觀測／空調／IR 按鈕可用性並接收限定命令 |
| GET /api/home-assistant/observations | HB owner key（Dashboard 伺服器持有） | 最新可用性與選定觀測 |

快照每 30 秒更新、90 秒過期；斷線立即未知。狀態改變約 0.2 秒合併後推送。
Snapshot 含遞增 sequence；重連重設，由 server 隔離舊連線。每 frame 上限 128 KiB，最多 128 個來源。
來源時間為 HA last_updated；received_at／age_seconds 只描述傳輸的新鮮程度。
觀測不存歷史、不呼叫 Sheets；空調下行只接受以下明確選取與白名單能力，不接受任意 HA service call。
單一 HA 對單一 HB process／worker；多副本部署前需要另設共享狀態協調。

離線 backend tests 在 repo 根目錄執行 unittest；真實 HA 框架 tests 在 Linux：
`pip install homeassistant==2026.9.2 pytest-homeassistant-custom-component` 後於本目錄 `pytest -q`。
測試不連家庭 HA，也不代替上述實機驗收。

## 空調遷移

1. HA 加入原生 **SwitchBot Cloud**（Token／Secret 只填 HA），確認空調的開關、冷暖／除濕／送風、整數溫度與風速可用。HA 新匯入的初始狀態不可當作實機已驗證。
2. 部署本版 HB 與 Dashboard，再用通過 CI 的完整 commit SHA 更新本整合並重啟 HA。
3. 確認 HA 原生空調顯示名稱與 Sheet 的設備名稱完全一致。例如「客廳空調」。
4. HA → Home Butler → 設定，保留原 FP2 勾選，另勾選要交給 HA 的原生空調。
   第一次勾選時固定 registry ID 與 HB 名稱；後續改 HA entity ID／名稱仍保留對應。
   若要改 HB 對應名稱，取消勾選保存，再以新名稱重新勾選；不要同時產生同名來源。
5. Render 設 `HOME_ASSISTANT_AC_NAMES` 為 JSON 名稱陣列，先一台或明確選定全部：
   `["客廳空調", "主臥空調", "次臥空調"]`。此設定是控制權切換，即使 HA 失聯也不退回舊 IR。
6. 檢查 Dashboard 顯示「由 HA 管理」：整數調溫、HA 模式／風速、HA 斷線未知；回饋介面退出；v1.55.0 可在 Dashboard 建立經 HA 執行的一次性手動排程。
   在 HA 手動操作後確認 Dashboard 與 LINE 查詢跟著更新，再測 Dashboard → HA。
7. 在 HA HomeKit Bridge 明確匯出選定 climate，確認 Apple Home 操作及狀態。
   遷移後使用新 HA 配件；舊 Homebridge 不列入相容驗收（HA 關機沒有模式，舊插件可能顯示無回應）。新配件驗證後再移除舊空調配件，避免重複。
   HA HomeKit 投影的除濕／送風 UI 及 Siri 功能須另外驗證，不假設與舊 Homebridge 模式開關完全相同。

已遷移空調的 HB 補償、防黴、自動關機停止執行；沒有明確 HA 手動標記的舊排程到期取消。
v1.55.0 起新建或明確編輯的手動排程會標記「使用者（HA）」並經 HA 執行；舊自動／防黴列不能轉換。
每日重複或條件式自動化仍建議在 HA 設定，Dashboard 的一次性排程由 HB 管理。舊 Sheet 設定與歷史保留但不作目前狀態來源，不能以空值回退舊讀值。
Dashboard／LINE 的半度輸入會 half-up 歸整；HA 原生實體步幅由 SwitchBot 整合決定。
已移轉後若要撤回，先停止該空調的 HA 自動化與新 HomeKit 配件，再明確清理舊排程／回饋設定，
最後才移除 Render 名稱白名單並重新建立舊後端狀態；單純 revert 程式不等於安全撤回控制權。

### 通道契約與限制

連線仍使用 protocol 1，以 `climate_control` 能力協商保持舊觀測客戶端相容。
完整 snapshot 另含至多 20 個選定 climate 的穩定 ID、固定 HB 名稱、可用性、模式、溫度及風速。
後端使用獨立 Render 名稱白名單限制發送；HA 再依本機來源白名單限制目標，不接受外部 entity ID、
任意 domain／service／attributes。HA 登記平台必須是 switchbot_cloud，避免 HB→HA→Homebridge→HB 循環。
命令只在當前已認證連線執行，有效期 15 秒；HA 動作期限 20 秒、HB 等待 25 秒。
單一命令可能需數次原生 climate 服務呼叫，部分成功／超時回 unknown，不自動重送。
HA 對最近 256 個 request ID 去重；命令不持久化、不離線重播，斷線立即使待處理結果未知。
HA 回覆成功還必須與請求設定一致；狀態來源依然只是整合觀察，不保證 IR 實體收到。
兩邊都以原生 HA 狀態為準，收到較舊的 snapshot 不覆蓋較新的命令回覆。
