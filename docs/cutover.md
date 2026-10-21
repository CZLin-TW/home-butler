# 切換到重寫後的 HomeButler（v2）

這份是切換當天照著做的清單。設計與理由在 [重構計畫](refactor-plan.md)。
這裡不寫任何金鑰或網址的實際值；值只放在 Render、Vercel、HA 與各台電腦自己的設定裡。

## 切換時不會改變的東西

新後端接住外部服務已經在用的每一個網址，所以切換和退回都**不需要改任何外部設定**：

| 誰 | 打的網址 | 新後端 |
| --- | --- | --- |
| LINE 後台的 webhook | `/callback` | 同一個處理（也接 `/v2/line/webhook`） |
| SwitchBot 的推送 | `/switchbot/webhook` | 同上（也接 `/v2/switchbot/webhook`）；**不要**設 `SWITCHBOT_REGISTER_WEBHOOK`，網址就維持原樣 |
| UptimeRobot | `/` | 回 `{"ok": true}` |
| HA 現在的整合（1.8.0） | `/api/home-assistant/health`、`/ws` | 接受，用協定 1；之後升到 1.9.0 會自動改用 `/v2` 與協定 2 |
| Mac mini、Windows 的監控 | `/api/computers/heartbeat` | 新版回報程式先試 `/v2/telemetry/heartbeat`，404 才用舊的 |
| Render 的啟動指令 | `uvicorn main:app` | `main.py` 已是新系統的進入點 |

## 事先準備（切換前，舊系統照常運作）

### 1. 產生新金鑰（使用者）

每一把都是一串長亂碼，在終端機用這行產生一次一串：
```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```
要準備：Dashboard 金鑰、Dashboard 簽章金鑰、管理用金鑰、Windows 監控金鑰。各自不同。

### 2. Render 環境變數（使用者；加了不影響舊系統）

| 變數 | 值 |
| --- | --- |
| `DASHBOARD_API_KEY` | Dashboard 金鑰 |
| `DASHBOARD_ASSERTION_KEY` | Dashboard 簽章金鑰 |
| `ADMIN_API_KEY` | 管理用金鑰 |
| `TELEMETRY_KEY_MAC_MINI` | 現在 `HOME_BUTLER_API_KEY` 的值（Mac mini 沿用舊鑰匙，只剩監控用途） |
| `TELEMETRY_KEY_PC` | Windows 監控金鑰 |
| `PANASONIC_DEVICE_AUTH` | 用 Mac mini 上的 `panasonic-auth.sh` 放進剪貼簿再貼上 |
| `DEFAULT_LIGHT_AREA` | 待辦燈光提醒預設的區域名稱（舊系統寫死的那個） |
| `BACKUP_FOLDER` | 備份資料夾名稱（舊系統沒設時用預設名稱；新系統要明確設） |
| `MIGRATE_FROM_OLD` | `1`（切換後第一次啟動自動搬一次資料；搬過就不再搬） |

其餘沿用：`DATABASE_URL`、`SPREADSHEET_ID`、`GOOGLE_CREDENTIALS`、`LINE_CHANNEL_*`、`ANTHROPIC_API_KEY`、
`HOME_ASSISTANT_API_KEY`（HA 整合的金鑰不必換）、`CWA_API_KEY`、`NOTION_TOKEN`、`DASHBOARD_URL`、
`SWITCHBOT_*`、`PANASONIC_ACCOUNT`／`PASSWORD`、`LG_*`、`DAILY_PUSH_HOUR`、`AGENT_OFFLINE_ALERT_SECONDS`。

### 3. Vercel 環境變數（使用者）

| 變數 | 值 |
| --- | --- |
| `HOME_BUTLER_DASHBOARD_KEY` | 與 Render 的 `DASHBOARD_API_KEY` 相同 |
| `DASHBOARD_ASSERTION_KEY` | 與 Render 相同 |

## 切換當晚（約 30 分鐘）

1. **推 HomeButler 的 `main`**（v2 合併）。Render 等 CI 通過後部署；新服務啟動時建立新資料表、登記金鑰、搬資料。
   - 確認：`/` 回 ok；Render Logs 有 `migration: read ... written ...`，對照試跑時的筆數。
   - 確認：LINE 傳一句話給管家有回應；傳「查看風格」看得到原本的設定。
   - HA 用舊版整合會在一分鐘內自動連上（Render Logs 或 Dashboard 的 HA 狀態）。
2. **推 Dashboard 的 `main`**（v2 合併）。Vercel 建置通過後部署。
   - 確認：首頁、裝置頁、待辦、庫存、照明都正常；裝置頁每張卡有「幾秒前更新」。
   - 遙控器平板要重新用密碼登入一次（舊的 session 沒有遙控器編號）。
3. **Windows PC**：把 Windows 監控金鑰貼進那台的 `agent_config.py` 的 `HOME_BUTLER_API_KEY`，重啟代理程式（已不用就跳過）。
4. **HA 整合升到 1.9.0**（可以隔天做）：照 `homeassistant/README.md` 安裝並重啟 HA，確認 HA 真的起來了。
5. **Render**：移除 `MIGRATE_FROM_OLD`；把 `HOME_BUTLER_API_KEY` 換成新的一串亂碼（舊的 Siri 捷徑從此失效；Mac mini 的舊鑰匙仍在 `TELEMETRY_KEY_MAC_MINI` 裡，不受影響）。

## 退回

把兩個 `main` 退回合併前的提交並推送。試算表與舊系統的資料都沒有被動過，舊系統照常接手。
代價：切換後在新系統裡新增或修改的資料不會回到舊系統。HA 整合 1.9.0 也能連舊系統，不必降級。

## 切換後兩週內的收尾

- 確認每日備份有寫入雲端硬碟。
- 刪掉試算表裡不再使用的分頁（只留家庭成員、遙控器、智能居家）。
- 移除舊系統的程式與測試、CI 裡的 `v2` 觸發、`public.history` 表。
- Mac mini 監控程式改成從設定檔讀鑰匙（見計畫 §8d）。
