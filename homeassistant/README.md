# Home Butler：HA 主動連接 HomeButler

第一版只同步選定的存在／亮度感測器到 Render 與 Dashboard，不控制設備。
適用 HA OS；開發目標 Core 2026.9.2，實機驗證狀態見 [驗證紀錄](../docs/verification.md)。
不需要 Aqara 開發者帳號、不需要把 HA 的 8123 port 對外開放，也不用重新配對 FP2。

## 安裝

1. 先部署支援本整合的 home-butler 後端，再部署 Dashboard v1.45.0。
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
| WSS /api/home-assistant/ws | 首個 hello frame 的專用 HA key | HA 主動連入，僅接收完整觀測快照 |
| GET /api/home-assistant/observations | HB owner key（Dashboard 伺服器持有） | 最新可用性與選定觀測 |

快照每 30 秒更新、90 秒過期；斷線立即未知。狀態改變約 0.2 秒合併後推送。
Snapshot 含遞增 sequence；重連重設，由 server 隔離舊連線。每 frame 上限 128 KiB，最多 128 個來源。
來源時間為 HA last_updated；received_at／age_seconds 只描述傳輸的新鮮程度。
不存歷史、不呼叫 Sheets、不啟用自動化、不接收 HA service call。
單一 HA 對單一 HB process／worker；多副本部署前需要另設共享狀態協調。

離線 backend tests 在 repo 根目錄執行 unittest；真實 HA 框架 tests 在 Linux：
`pip install homeassistant==2026.9.2 pytest-homeassistant-custom-component` 後於本目錄 `pytest -q`。
測試不連家庭 HA，也不代替上述實機驗收。
