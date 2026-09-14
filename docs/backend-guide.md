# 後端設定與 API 參考

本頁保存完整建置、Sheets 欄位、語音、API 與業務規則細節，配合目前程式修訂。
新使用者先讀 [README](../Readme.md) 選擇架構，再使用本頁；本頁不是 v1.44.0 的凍結手冊。
若使用 HA 前的固定版本，請讀 [版本選擇](version-selection.md) 所連結的同版本 README。

**路徑約定：** 已列入 HA 名稱清單的空調／IR 電扇／感測器，以及啟用 HA Hue 的照明，
都由 HA 提供控制或資料；以下直接雲端、PC Hue、舊空調補償／自動排程內容僅適用未切換的設備；v1.55.0 手動排程可經 HA 執行。
除濕機控制、Sheets、LINE、待辦與庫存仍由 HB 負責。

快速入口：[建置](#完整建置流程) · [環境變數](#render-環境變數) · [API](#server-端點) ·
[Siri](#siri-語音控制ios-捷徑) · [家電專用語音](#device-only-voice) · [HA 安裝](../homeassistant/README.md)

## 完整建置流程

### 一、環境準備

安裝以下工具：
- Python：後端本機離線測試使用 Python 3.12；HA 框架另有 Linux／Python 3.14 測試環境。Windows 安裝時勾選 **Add Python to PATH**。[下載](https://www.python.org/downloads/)
- Git：https://git-scm.com/download/win
- VS Code：https://code.visualstudio.com/

確認安裝成功：
```
python --version
git --version
code --version
```

---

### 二、Line Bot 申請

1. 前往 https://developers.line.biz/ 登入
2. 建立 Provider（名稱隨意，例如「家庭管家」）
3. 點「Messaging API」→「Create a LINE Official Account」
4. 填入帳號名稱（例如「家庭管家」）、email、業種（選個人）
5. 回到 Official Account Manager →「設定」→「Messaging API」→ 選剛才的 Provider →「同意」
6. 回到 Line Developers Console，進入 Channel →「Messaging API」分頁
7. 點「Issue」產生 Channel Access Token，複製保存
8. 記下 Channel Secret（同頁面）
9. 關閉自動回覆：Official Account Manager →「回應設定」→「自動回覆訊息」關閉

---

### 三、本機開發環境

先 fork 本 repo，或 clone 原始碼。不要對已 clone 的目錄再執行 `git init`。
若需要舊版，先照 [版本選擇](version-selection.md) 取得成套的固定快照。

```sh
git clone https://github.com/CZLin-TW/home-butler.git
cd home-butler
python -m venv .venv
```

Windows PowerShell 啟用 `.venv\Scripts\Activate.ps1`；macOS／Linux 使用 `source .venv/bin/activate`。
在虛擬環境執行 `python -m pip install -r requirements.lock`，沿用 repo 的鎖檔及 `.gitignore`。
將本頁環境變數設在執行環境；不要提交金鑰、Google 憑證、個人 Sheet 或 agent 設定。
本機啟動 `uvicorn main:app --host 127.0.0.1 --port 8000` 會啟動背景工作，應使用獨立測試資料。
只想看 UI，請用 Dashboard 的獨立 demo，不必啟動真實後端。

### 四、部署到 GitHub + Render

將自己 fork 的 repo 連到 Render Web Service，依 [render.yaml](../render.yaml) 設定：

- Build Command：`pip install -r requirements.lock`
- Start Command：`uvicorn main:app --host 0.0.0.0 --port $PORT`
- 設定本頁列出的必要環境變數；維持單一 process／worker。
- 將自己的 `https://<後端網址>/callback` 設為 LINE Webhook URL，啟用 Use Webhook。

若使用平台自動部署，push 到追蹤分支會部署新程式；固定舊版時要另外固定平台部署來源，
不能只在個人電腦切換 commit。方法見 [版本與部署固定](version-selection.md#固定部署與更新)。
Render／Vercel 設定及服務限制可能變動，選擇能符合自己可用性需求的方案；
背景工作不會在程序被休眠或停止時執行。

---

### 五、Google Sheets 設定

前往 https://sheets.google.com 建立試算表「家庭管家」，建立以下分頁（第一行為標題列）：

**食品庫存**
| 品名 | 數量 | 單位 | 過期日 | 新增日 | 新增者 | 狀態 |
|------|------|------|--------|--------|--------|------|
- 狀態值：有效 / 已消耗

**食品封存**
| 品名 | 數量 | 單位 | 過期日 | 新增日 | 新增者 | 狀態 |
|------|------|------|--------|--------|--------|------|
- 已消耗的食品自動移至此分頁，Claude 不會讀取

**待辦事項**
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 | 來源 | 屬性 | 燈光提醒 | 燈光區域ID | 規則ID |
|------|------|------|--------|------|------|------|------|----------|------------|--------|
- 狀態值：待辦 / 已完成
- 類型值：公開 / 私人
- 來源值：本地 / Notion（程式自動填入，本地新增的待辦填「本地」，外部行事曆同步的填來源名稱）
- 屬性值：讀寫 / 唯讀（本地項目為「讀寫」，外部項目依成員的權限設定填入）
- 燈光提醒：TRUE/FALSE。只有有「時間」且已到期、狀態仍為待辦時，PC agent 會每分鐘觸發 Hue breathe 一次，直到該待辦完成
- 燈光區域ID：Hue grouped_light id。Dashboard 用顯示名稱下拉選擇，Sheet 內保存穩定 ID；LINE Bot 未指定區域時預設使用「客廳」
- 規則ID：程式自動加欄（`ensure_columns`）。由「週期待辦模板」生成的當次待辦會帶上模板的規則ID（list 上以 🔁 標記、完成後同日不重生靠它去重），一般待辦留空，手動建 sheet 時不需填

**待辦封存**
| 事項 | 日期 | 時間 | 負責人 | 狀態 | 類型 |
|------|------|------|--------|------|------|
- 已完成的待辦自動移至此分頁，Claude 不會讀取

**家庭成員**
| 名稱 | Line User ID | 狀態 | 稱謂 | 管家風格 | Notion Database ID | Notion 篩選 | Notion 權限 | Google Calendar ID |
|------|-------------|------|------|---------|-------------------|------------|------------|-------------------|
- 狀態值：啟用 / 停用
- 稱謂例如「父親,老公,爸爸」（逗號分隔）
- 管家風格：選填，自訂管家回覆風格（例如「回覆簡短，多用 emoji，語氣活潑」），空白則使用預設風格
- Line User ID 取得方式：家人加好友後傳訊息，從「對話暫存」分頁複製（U 開頭）
- Notion Database ID / Notion 篩選：選填，有填才整合（詳見下方說明）
- Notion 權限：「唯讀」或「讀寫」，會被寫進待辦 Sheet 的「屬性」欄。「唯讀」項目在 modify_todo / delete_todo 會被拒絕（避免改到別人 Notion 的事件）；「讀寫」則允許本地修改／完成。空白預設「唯讀」
- Google Calendar ID：選填，預留欄位

**對話暫存**
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 角色值：user / assistant
- 每位用戶保留最近 6 則（預設值，可在程式碼中調整 `limit` 參數），超過自動移至對話封存
- 6 則為 user + assistant 合計，不分角色，例如 3 問 3 答
- 推播訊息也會存入，Claude 可參考之前推過的內容

**對話封存**
| Line User ID | 角色 | 內容 | 時間 |
|-------------|------|------|------|
- 超過保留則數的舊對話自動移至此分頁，Claude 不會讀取

**智能居家**
| 名稱 | 類型 | 位置 | Device ID | 狀態 | 按鈕 | Auth | 控制類型 | 最後電源 | 最後溫度 | 最後模式 | 最後風速 | 最後更新時間 | 溫度補償 | 濕度補償 | 自動關機小時數 |
|------|------|------|-----------|------|------|------|---------|---------|---------|---------|---------|------------|---------|---------|------------|
- 類型值：空調 / 感應器 / IR / 除濕機
- 狀態值：啟用 / 停用
- 按鈕欄：僅 IR 設備需要填寫，逗號分隔（例如「電源,風速+,風速-,擺頭」）
- Auth 欄：僅 Panasonic 除濕機需要填寫
- 控制類型值：`command`（標準指令，例如 turnOn/turnOff、setAll）／`customize`（DIY IR 自訂按鈕）。對應 SwitchBot API 的 commandType；空白時 IR 開/關按鈕走 command、其他按鈕走 customize
- 最後電源／最後溫度／最後模式／最後風速／最後更新時間：**僅空調設備使用**，由程式自動寫入，不需手動填。Dashboard 用來顯示最後狀態，LINE bot 用來支援「調低 1 度」這類相對指令。手動建 sheet 時這 5 欄保持空白即可
- 空調溫度回饋設定／空調溫度回饋狀態：首次於 Dashboard 保存補償設定時自動補上 JSON 欄位；`最後溫度` 仍是舒適目標，實際 IR 下發溫度及待確認標記另存於回饋狀態。預設不啟用，詳見[補償說明](ac-temperature-feedback.md)。
- 溫度補償／濕度補償：**僅感應器設備使用**。填入數字（正或負），程式讀取 sensor 數值後自動加上此補償值。例如 Hub 2 貼牆導致濕度偏高 5%，填 `-5`。空白 = 不補償。濕度補償後會自動限制在 0~100% 範圍
- 自動關機小時數：**僅空調設備使用**。填入整數，系統會在空調開啟後 N 小時自動加一筆 off 排程。例如填 `8` → 開空調後最晚 8 小時關。空白或 0 = 停用此功能。使用者每次對該空調發送「從關→開」的命令會重置計時；純調整溫度/模式/風速不會重置。如果使用者自己設了 off 排程，系統會清掉自動排程讓使用者的決定優先
- SwitchBot Device ID 取得方式：瀏覽器打開 `https://home-butler.onrender.com/switchbot/devices`

**排程指令**
| 設備名稱 | 動作 | 參數 | 觸發時間 | 建立者 | 建立時間 | 狀態 | 來源 |
|---------|------|------|---------|--------|---------|------|------|
- 動作值：control_ac / control_ir / control_dehumidifier
- 參數：JSON 字串（例如 `{"temperature":27,"power":"on"}`）
- 狀態值：待執行 / 已執行 / 已過期 / 已取消
- 來源值：使用者 / 使用者（HA） / 自動 / 防黴。HA 空調新建或明確編輯的手動排程為「使用者（HA）」；其餘手動為「使用者」。執行時只允許與目前控制來源一致的列；HA 空調舊列不自動升級，自動／防黴列不能編輯為 HA 手動排程。

**排程封存**
| 設備名稱 | 動作 | 參數 | 觸發時間 | 建立者 | 建立時間 | 狀態 | 來源 |
|---------|------|------|---------|--------|---------|------|------|
- 設備所有排程完成後統一移至此分頁，Claude 不會讀取

#### 自動建立的分頁（不需手動建）

home-butler 啟動 / 第一次寫入時自動建出來，header 也自動補：

| 分頁 | 寫入者 | 內容 |
|------|--------|------|
| 感測器歷史 | `sensor_state.py` 每 5 min 紀錄最新快照 | timestamp, device_name, location, temp, humidity, co2（24h 後自動 trim） |
| 空調歷史 | `ac_history.py` 每 5 min polling 寫入 | timestamp, device_name, location, power, temp, mode, fan_speed（24h 後自動 trim） |
| 除濕機歷史 | `dehumidifier_history.py` 自動模式 polling 時寫入 | timestamp, device_name, location, power（24h 後自動 trim，給 Dashboard 自動模式 chart 背景畫運轉區段） |
| 除濕機自動規則 | `dehumidifier_auto.py` 規則設定 / 評估時寫入 | device_name, auto_mode, sensor_name, duration_min, threshold, on_mode, auto_phase, countdown_min, last_event, last_event_at（每台一行，覆蓋更新） |
| 週期待辦模板 | `handlers/recurring_todo.py` 設定週期規則時寫入 | 規則ID, 事項, 重複類型, 星期, 月日, 間隔天數, 時間, 負責人, 類型, 燈光提醒, 燈光區域ID, 起始日期, 結束日期, 狀態, 最後生成日期, 建立者, 建立時間 |
| 裝置配對 | `device_auth.py` Dashboard 登入配對時寫入 | user_code, device_token, status, line_user_id, name, picture, created, expires（5 分鐘過期、單次使用） |

---

### 六、Google Cloud 設定

前往 https://console.cloud.google.com：
1. 建立新專案（名稱 home-butler）
2. 搜尋並啟用「Google Sheets API」
3. 搜尋並啟用「Google Drive API」
4. 搜尋「Service Accounts」→「建立服務帳戶」，名稱填 home-butler
5. 進入剛建立的帳戶 →「金鑰」→「新增金鑰」→ JSON → 下載
6. 下載的 JSON 改名 credentials.json，放到專案資料夾（已在 .gitignore，不會上傳 GitHub）
7. 複製 Service Account 的 email（格式：xxx@xxx.iam.gserviceaccount.com）
8. 去 Google Sheets 右上角「共用」，貼上這個 email，權限選「編輯者」
9. 把 credentials.json 的完整內容（從 `{` 到 `}`）貼到 Render 的 GOOGLE_CREDENTIALS 環境變數

---

### 七、Claude API 設定

前往 https://console.anthropic.com：
1. 選 Individual 方案
2. 儲值 $5
3. 設定 monthly spend limit $5（Billing → Spend limits，防止爆量）
4. 建立 API Key，複製保存
5. 貼到 Render 的 ANTHROPIC_API_KEY 環境變數

---

### 八、SwitchBot 智能居家設定

使用 HA：先在 HA 加入原生 SwitchBot Cloud，Token／Secret 直接填 HA，測試設備後依
[空調遷移](../homeassistant/README.md#空調遷移)、[IR 按鈕](../homeassistant/ir-buttons.md)、
[感測器配對](../homeassistant/sensors-and-hue.md) 逐台授權 HB。Sheet 名稱必須與配對名稱一致。

不使用 HA：在 SwitchBot App 取得開發者 Token／Secret，設定後端的 `SWITCHBOT_TOKEN`、
`SWITCHBOT_SECRET`；以帶 owner Key 的 `GET /switchbot/devices` 查裝置 ID，填入「智能居家」。
不要把金鑰放 URL，也不要把單純瀏覽器開啟受保護端點當成完整驗證方式。
IR 自訂按鈕依設備設定使用標準命令或 customize；有獨立 ON／OFF 或單一 toggle 須按實際遙控器建模。

HA Hub 2 快速更新仍使用 HB 註冊的 SwitchBot Webhook，因此需要保留後端的 SwitchBot 憑證與公開接收網址；
這不表示已遷移感測器仍由 HB 輪詢。詳見 [Hub 光照與 Push](../homeassistant/hub-light.md)。

### 九、Hub 2 物理按鈕（選配，尚未在家庭實測）

可評估透過 Matter 將 ON／OFF 按鈕事件加入 HA，由 HA 自動化指定燈光或空調動作；
目前的 SwitchBot Cloud 整合不提供這兩個按鈕事件。此功能尚未部署，不是本專案預設能力。
SwitchBot 官方要求先替按鈕綁定場景以啟用用途，之後可移除原場景；須避免按一次執行兩份動作。
參考 [SwitchBot 支援清單](https://support.switch-bot.com/hc/en-us/articles/13282638111127-Which-SwitchBot-Devices-Can-Be-Added-to-Apple-Home-As-Sub-devices-via-Matter)
與 [HA Matter 按鈕自動化](https://www.home-assistant.io/integrations/matter#automate-on-a-button-press)。

IR 沒有實體回讀，即使所有指令從同一入口送出，也不能保證每次設備都收到，
更不能把「最後設定」寫成「永遠與實際狀態一致」。

---

### 十、除濕機設定（Panasonic / LG）

除濕機支援多台並存，依「智能居家」分頁的 **品牌** 欄位分流到對應 API。品牌欄空值預設為 Panasonic（向下相容）。

**Panasonic（Smart App API）**

1. 在 Render.com 新增環境變數 `PANASONIC_ACCOUNT` 和 `PANASONIC_PASSWORD`（Panasonic Smart App 帳密）
2. 抓裝置參數：帶 `X-API-Key` 打 `GET /panasonic/devices`，列出帳號下所有機器的 GWID / Auth
3. 在「智能居家」分頁新增一行：
   - 名稱：自訂（多台請取不同名字，建議用位置區分，例如「客廳除濕機」）
   - 類型：除濕機
   - 品牌：Panasonic（或留空）
   - Device ID：Panasonic 的 GWID
   - Auth：Panasonic 的 Device Auth
   - 狀態：啟用

**LG（ThinQ Connect API）**

1. 那台 LG 除濕機需先在手機 **LG ThinQ App** 加入、能遠端控制
2. 前往 https://thinq.dev，用同一個 LG 帳號登入，產生 **PAT（Personal Access Token）**，勾選裝置讀取 + 控制權限
3. 在 Render.com 新增環境變數 `LG_PAT`（貼上 PAT）、`LG_COUNTRY`（台灣填 `TW`）
4. 抓裝置參數：帶 `X-API-Key` 打 `GET /lg/devices`，找到該除濕機的 **deviceId**
5. 在「智能居家」分頁新增一行：
   - 名稱：自訂（取不同名字）
   - 類型：除濕機
   - 品牌：LG
   - Device ID：LG 的 deviceId
   - Auth：留空（LG 不需要）
   - 狀態：啟用
6. **校準**：LG 除濕機的 property 欄位名 / 值因機型而異。部署後打 `GET /lg/devices/{deviceId}/profile` 與 `GET /lg/devices/{deviceId}/state`，對照回應調整 `lg_api.py` 頂部「校準點」常數（電源 / 模式 / 目標濕度的 node / key / 值）。

> **LG 自動模式策略**：LG 使用 **智慧除濕**（`lg_api.py:AUTO_MODE_JOBMODE`），並把機體目標濕度設成「外部 sensor 目標 − `AUTO_TARGET_OFFSET`(10%)」。巨觀的開關仍由外部 sensor、hysteresis 與等待時間掌控。
> 　不用「快速除濕」是因為 LG 的快速除濕跑一陣子會自己跳回智慧除濕，害自動模式的手動介入偵測（`state_diverged`）誤判 mode 被改而整個關閉；智慧除濕是它會跳回去的穩定模式。機體目標壓低 10% 是補償機體周邊比房間乾的落差，讓機器多跑、不提早停。

---

### 十一、天氣預報設定

1. 前往 https://opendata.cwa.gov.tw 註冊帳號
2. 登入後到「會員中心」→「取得授權碼」
3. 在 Render.com 新增環境變數 `CWA_API_KEY`（貼上授權碼）

天氣功能免費，支援全台 22 縣市所有鄉鎮，預報範圍一週，包含體感溫度。

---

### 十二、Notion 行事曆整合（選配）

Notion 整合會將事件同步到待辦事項 Sheet，並依權限設定標記為唯讀或讀寫。同步會在以下時機自動執行：使用者查詢待辦（query_todo）、每日推播（/notify）、即時提醒 tick（/notify_realtime，每 5 分鐘）。

**建立 Integration：**
1. 前往 https://www.notion.so/my-integrations 建立 Internal Integration（需 Workspace Owner 或 Admin 權限）
2. 複製 Integration Token
3. 在 Render.com 新增環境變數 `NOTION_TOKEN`

**連結 Database：**
1. 打開 Notion 行事曆 Database 頁面
2. 右上角「...」→「Connections」→ 加入剛建立的 Integration
3. 從 Database 頁面網址取得 Database ID（`https://www.notion.so/xxxxx?v=yyyyy` 中的 `xxxxx`）

**設定 Google Sheets：**
在「家庭成員」分頁對應的成員行填入：
- `Notion Database ID`：Database ID
- `Notion 篩選`：篩選條件，格式為 `欄位名:值,欄位名:值`
- `Notion 權限`：「唯讀」或「讀寫」，控制同步進待辦後是否允許本地 modify / delete。一般情況填「唯讀」，避免不小心改到 Notion 來源事件；只有確定要把同步進來的事件當本地待辦操作才填「讀寫」（注意：本地修改不會回寫 Notion，下次同步該事件仍以 Notion 為準）

篩選條件範例：
- `Status:Incoming,person:CZ` — 只顯示狀態為 Incoming 且 person 為 CZ 的事件
- `Status:Incoming,person:CZ,名稱分析:!休假事件` — 再排除名稱分析為「休假事件」的項目
- `!` 開頭表示排除條件

每個家庭成員可以各自設定不同的 Database ID 和篩選條件，沒有填的成員不會整合 Notion。

---

### 十三、常駐與健康檢查

後端的 `GET /`／`HEAD /` 可供健康監控。既有部署以 UptimeRobot 每 5 分鐘檢查後端，
用途包含降低閒置休眠機率；不要把這種監控當成常駐或準時執行的保證。
HB 的排程與提醒依賴後端程序運行；需要可靠常駐時選用相應的主機方案。
HA 本地規則不等待 Render，但所操作的 SwitchBot Cloud 設備仍依賴網際網路。

---

### 十四、排程與推播（in-process scheduler）

背景工作由 `main.py` 註冊到 `job_runner.py`，每項使用獨立 thread：設備排程每 60 秒；一般感測器／歷史、照明、Notion、待辦提醒、每日推播檢查、agent 健康檢查各每 300 秒；空調回饋每 60 秒先更新使用中的感測器再評估。不需要外部 cron。每項不重疊、按固定期限運行，錯過週期不密集補跑。

- **工作隔離**：`run_schedule_tick` 執行設備排程與封存；`run_todo_tick` 生成週期待辦與提醒；Notion 獨立同步。`run_realtime_tick` 僅保留相容入口，正式背景執行不串在一起。
- **每日綜合推播**（`notify.run_daily_push_if_due`）：每天過了 `DAILY_PUSH_HOUR`（環境變數，預設 `21` = 晚上 9 點）後的第一個 tick 觸發一次；用 Sheet「系統狀態」分頁的 `最後每日推播日期` marker 去重，跨 Render 重啟存活——不重發也不漏發。
- **Agent 失聯告警**（`health_alert.run_checks`）：由獨立 `agent-health` 工作每 300 秒執行，純觀察不控制設備。詳見 `AGENTS.md` 的「Agent 失聯告警」。

上述工作只有在後端程序運行時才會執行。既有 UptimeRobot 監控用途見「十三、常駐與健康檢查」，變更主機方案時需一併考慮。HA Hue 提醒另有每 60 秒的 `lighting-reminders` 工作；HA 管理空調不執行舊補償與自動排程，新建的手動排程經 HA 執行。

`/notify`、`/notify_realtime` 端點仍保留，但只當**手動觸發**（debug / 補發；手動 `/notify` 不檢查也不更新每日 marker）：

```bash
curl -X POST https://home-butler.onrender.com/notify_realtime -H "X-API-Key: <key>"
curl -X POST https://home-butler.onrender.com/notify -H "X-API-Key: <key>"
```

> **從舊版 GAS 遷移**：早期版本用 Google Apps Script 每 15 分鐘打 `/notify_realtime`、每日晚間打 `/notify`。改為 in-process 後，請到 https://script.google.com 對應專案，把那兩條觸發條件（時鐘圖示 → 觸發條件）**刪除或停用**，避免與 thread 重複執行。建議順序：先部署新版、確認 Render log 出現 `[startup] independent periodic jobs started`，並從 `/api/system/jobs` 核對各工作執行時間與錯誤，**再**關掉 GAS——重疊期很短、且工作本身冪等（排程狀態翻「已執行」後第二跑者會 skip、提醒有去重），無害。

---

### 十五、取得家庭成員 Line User ID

每位家庭成員：
1. 掃 QR Code 加管家好友
2. 傳任何一則訊息
3. 從「對話暫存」分頁複製 User ID（U 開頭）
4. 填入「家庭成員」分頁

---

## Render 環境變數

| 變數名稱 | 說明 | 必要 |
|----------|------|------|
| HOME_ASSISTANT_API_KEY | HA 主動 WSS 使用的獨立金鑰，至少 32 字元；不與其他 key 共用 | 使用 HA 同步／控制時 |
| HOME_ASSISTANT_AC_NAMES | HA 管理的空調精確名稱 JSON 陣列；預設 `[]` | 逐台選配 |
| HOME_ASSISTANT_IR_NAMES | HA 管理的 IR 電扇名稱 JSON 陣列；預設 `[]` | 逐台選配 |
| HOME_ASSISTANT_SENSOR_NAMES | HA 唯一即時來源的感測器名稱 JSON 陣列；預設 `[]` | 逐台選配 |
| HOME_ASSISTANT_HUE_ENABLED | `true` 將 Hue 控制／提醒切至 HA；預設 `false` 使用 PC Agent | 選配 |
| LINE_CHANNEL_ACCESS_TOKEN | Line Bot 的 Channel Access Token | 必要 |
| HOMEBRIDGE_API_KEY | 僅 `/api/homebridge/*` 接受的獨立 Key，至少 32 字元，不可與 owner／device-voice Key 相同；未設定停用 | 橋接選配 |
| HOMEBRIDGE_DEVICE_NAMES | 允許橋接的冷氣名稱 JSON 陣列，例如 `["客廳冷氣"]`；精確名稱，未設定停用 | 橋接選配 |
| LINE_CHANNEL_SECRET | Line Bot 的 Channel Secret | 必要 |
| SPREADSHEET_ID | Google Sheets 的試算表 ID（網址中間那串） | 必要 |
| GOOGLE_CREDENTIALS | Google Service Account 的 JSON 金鑰（整個內容，從 { 到 }） | 必要 |
| ANTHROPIC_API_KEY | Claude API Key（sk-ant- 開頭） | 必要 |
| HOME_BUTLER_API_KEY | 完整 API 認證金鑰，保護 `/api/*` `/notify*` `/switchbot/*`，家電專用入口另用下列金鑰。建議用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 產生 | 必要 |
| DEVICE_VOICE_API_KEY | 只用於 `POST /api/assistant/devices` 的家電專用金鑰。使用上列指令另產生一把，至少 32 字元且不可等於 `HOME_BUTLER_API_KEY`；未設、太短或重複時此入口回 503，不影響原有 API。不要放在 Dashboard 或完整捷徑中 | 選配 |
| DASHBOARD_URL | Dashboard 部署網址（例如 `https://dashboard.example.com`）。home-butler 啟動後會 runtime 從 `{DASHBOARD_URL}/api/version` 撈使用者體感版本（1 小時 cache）注入到 LINE bot 的 SYSTEM_PROMPT。沒設或撈不到時 LINE 回答版本會是「未知」，其他功能不受影響 | 建議 |
| SIRI_USER_ID | 完整 Siri 入口（`/api/assistant`）沒帶 `user_id` 時的匿名 fallback，預設 `siri`。**維持中性，不要設成家人的真實 Line ID**，以免冒名及混用記憶。空白 ID 不會限制成家電權限；只需家電功能請用獨立入口與 `DEVICE_VOICE_API_KEY`。家電入口不使用此變數 | 選配 |
| LG_PAT | LG ThinQ Connect 的 Personal Access Token（thinq.dev 產生，需勾裝置讀取 + 控制權限）。有 LG 除濕機才需要 | 選配 |
| LG_COUNTRY | LG ThinQ 國碼，台灣 = `TW`（決定區域 endpoint）。預設 `TW` | 選配 |
| LG_CLIENT_ID | LG ThinQ client 識別字串，固定一組即可。預設 `home-butler-client` | 選配 |
| SWITCHBOT_TOKEN | SwitchBot 開發者 Token | 選配 |
| SWITCHBOT_SECRET | SwitchBot 開發者 Secret Key | 選配 |
| PUBLIC_BASE_URL | 本服務的公開網址，startup 用來向 SwitchBot 註冊 webhook（HA Hub 刷新提示）。**Render 上不用設**（自帶 `RENDER_EXTERNAL_URL`），部署在其他平台才需要。兩者都沒有時跳過註冊，無法使用這条推送路徑；已安裝的 HA Hub 整合仍有本機備援查詢 | 選配 |
| PANASONIC_ACCOUNT | Panasonic Smart App 帳號 | 選配 |
| PANASONIC_PASSWORD | Panasonic Smart App 密碼 | 選配 |
| CWA_API_KEY | 中央氣象署開放資料授權碼 | 選配 |
| NOTION_TOKEN | Notion Internal Integration Token | 選配 |
| RECURRING_TODO_ENABLED | 週期性待辦「生成」總開關（kill-switch），預設**關閉**。設為 `1`/`true`/`yes`/`on` 才會啟用「週期待辦模板 → 每 5 分鐘維持一筆『下一次要做的』待辦」的生成邏輯。關閉時模板 CRUD（新增/修改/停用規則）仍可用，只是不會自動長出待辦實例；上線或收手只需改這個變數，不必 revert code | 選配 |
| DAILY_PUSH_HOUR | 每日晚間綜合推播的觸發鐘點（24h 制整點），預設 `21`（晚上 9 點）。polling thread 每 tick 一旦過了這個鐘點、且當天還沒推過（Sheet marker 判斷）就觸發一次。沿用原本 GAS 晚間時段；要改推播時間改這個變數即可 | 選配 |
| AGENT_OFFLINE_ALERT_SECONDS | PC agent 幾秒沒回報就推失聯告警，預設 `900`（15 分）。刻意比 Dashboard 畫灰點的 5 分鐘寬——agent self-restart／網路抖動／Render 重啟後 backfill 都會造成幾分鐘空窗，門檻太緊會誤報。下限鎖在 300 秒 | 選配 |
| AQARA_APP_ID | Aqara Cloud Open API 的 App ID（developer.aqara.com 建立應用後取得）。僅使用舊 Aqara 雲端診斷路徑才需要；HA 的 FP2 路徑不需要 | 選配 |
| AQARA_KEY_ID | Aqara Open API 的 Key ID | 選配 |
| AQARA_APP_KEY | Aqara Open API 的 App Key（簽名用，等同密鑰） | 選配 |
| AQARA_REGION | 帳號所屬機房：`CN` / `USA` / `KR` / `RU` / `GER` / `SG`，預設 `CN`。不確定就先留預設，部署後打 `/aqara/probe` 讓它每一區試一遍（那支不會寄授權信） | 選配 |
| AQARA_API_BASE | 完全覆寫 Aqara base URL（角色同 `LG_API_BASE`），probe 出來的機房不在上面清單裡時用，例如 `https://open-sg.aqara.com` | 選配 |
| AQARA_ACCOUNT | 授權用的 Aqara 帳號（Email 或手機號），授權碼會寄到這裡 | 選配 |
| AQARA_ACCOUNT_TYPE | `getAuthCode` / `getToken` 的 accountType，預設 `0`（Aqara 帳號） | 選配 |
| AQARA_TOKEN_VALIDITY | accessToken 有效期，預設 `7d`。到期前 10 分鐘會自動 refresh，所以這個值只影響「多久換一次」 | 選配 |
| AQARA_FP2_DID | FP2 的 did（`/aqara/devices` 查得到）。填了就省掉每次「先列裝置再挑 FP2」那一趟 API | 選配 |
| AQARA_FP2_PRESENCE_RESOURCE | 「有沒有人」對應的 resource id。空白時用名稱關鍵字猜；用 `/aqara/devices/{did}/values` 對照真機確認後填進來釘死 | 選配 |

---

## Server 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| / | GET / HEAD | 健康檢查（UptimeRobot 用） |
| /callback | POST | Line Webhook 接收訊息 |
| /notify | POST | 手動觸發晚間綜合推播（外部行事曆同步 + 明日天氣 + 食品過期 + 明日與未完成待辦摘要）。日常由 polling thread 每日晚間（預設 21 點）自動驅動，不再靠 GAS |
| /notify_realtime | POST | 手動觸發 realtime tick（外部行事曆同步 + 即將到時的待辦提醒 + 執行已到時間的設備排程 + 封存）。日常由獨立背景工作執行：排程每 60 秒，Notion／待辦／健康各每 300 秒 |
| /switchbot/devices | GET | 查看 SwitchBot 帳號下所有設備與 Device ID |
| /switchbot/test/{device_id}/{button} | GET | 測試 IR 按鈕（customize 模式） |
| /switchbot/test_turnon/{device_id} | GET | 測試 turnOn 指令 |
| /switchbot/webhook | POST | SwitchBot Cloud webhook 接收端，Hub 2 changeReport 轉送選定 HA Hub 的刷新提示；不再評估 HB 夜燈。無簽章，HA 值須由原生 API 驗證讀取，詳見 Hub 光照文件 |
| /switchbot/webhook/status | GET | Debug：查 SwitchBot Cloud 目前註冊的 webhook URL，確認 HA Hub 更新提示路徑 |
| /panasonic/devices | GET | 列出 Panasonic 帳號下所有設備（GWID / Auth），新增除濕機抓參數用 |
| /panasonic/dehumidifier/{name}/full_status | GET | Debug：掃某 Panasonic 除濕機 CommandType 0x00~0x1F 全欄位 |
| /lg/devices | GET | 列出 LG ThinQ 帳號下所有裝置，抓 deviceId 用 |
| /lg/devices/{device_id}/profile | GET | LG 裝置能力 profile，校準除濕機 property 欄位用 |
| /lg/devices/{device_id}/state | GET | LG 裝置目前狀態，對照 profile 校準解析 |
| /aqara/probe | GET | Debug：六個 Aqara 機房各打一次（不帶權杖），找出帳號屬於哪一區 |
| /aqara/token | GET | Debug：目前授權狀態（權杖遮蔽過，只露頭尾） |
| /aqara/auth/code | POST | 授權第一步：請 Aqara 寄授權碼到帳號（Email / 簡訊）。⚠️ 真的會寄信 |
| /aqara/auth/token | POST | 授權第二步：`?auth_code=` 換權杖，成功即寫入「系統狀態」分頁（跨重啟存活） |
| /aqara/auth/refresh | POST | Debug：手動換一次權杖。正常不用打（到期前自動換、過期也會被動補換） |
| /aqara/devices | GET | 列出 Aqara 帳號下所有裝置（did / model / 名稱），抓 FP2 的 did 用 |
| /aqara/devices/{did}/resources | GET | 該裝置 model 開放了哪些 resource（id / 名稱 / 說明），不含當下值 |
| /aqara/devices/{did}/values | GET | 該裝置**所有**開放 resource 的當下值。FP2 的 resource id 就是靠這支對出來的 |
| /aqara/fp2 | GET | FP2 當下狀態（presence + 全部原始 resource）。`?did=` 可指定，不帶則用 `AQARA_FP2_DID` |
| /aqara/raw | POST | Debug：直送任意 intent（`{"intent": "...", "data": {...}}`），給還沒封裝的 API 探路 |

### Dashboard REST API（/api）

供網頁版 Dashboard 使用，所有業務邏輯重用現有 handlers，不重複實作。

> **🔒 認證**：`/api/*` 端點要求 `X-API-Key` header，使用環境變數 `HOME_BUTLER_API_KEY`；`POST /api/assistant/devices` 另使用 `DEVICE_VOICE_API_KEY`；HA／Homebridge 路由也各有專用金鑰。各類金鑰不互通，家電金鑰不能呼叫完整語音、Dashboard API 或其他控制入口。
> 沒有 header 或 key 不對會回 401；伺服器端未設定 `HOME_BUTLER_API_KEY` 則回 503（fail-closed）。
> 同樣的保護也套用在 `/notify*` 和 `/switchbot/*` 端點。`/`（健康檢查）、`/callback`（LINE webhook，由 X-Line-Signature 驗證）和 `/switchbot/webhook`（SwitchBot Cloud 推播，SwitchBot 不支援自訂 header，無簽章；HA 路徑只接收受驗證的刷新提示，不採信其中的讀值）不在保護範圍內。

| 端點 | 方法 | 說明 |
|------|------|------|
| /api/devices | GET | 列出所有啟用裝置基本資料（Sheet 欄位 + AC 上次指令快照），不含即時讀值 |
| /api/devices/status | GET | 統一裝置狀態快取，包含空調 last-command、感應器與除濕機狀態，回傳 `{裝置名稱: 狀態}`。無 name 時立即回快取並在背景更新雲端裝置；`?name=` 用於單台命令確認 |
| /api/devices/options | GET | 各類裝置的可用選項（空調模式/風速、除濕機模式/濕度），供前端動態渲染 |
| /api/devices/control/ac | POST | 控制空調（power, temperature, mode, fan_speed）；HA 管理空調採整數目標／HA 狀態。未遷移空調仍接受半度目標，無回饋時四捨五入 |
| /api/devices/control/ir | POST | 控制 IR 裝置（device_name, button） |
| /api/devices/control/dehumidifier | POST | 控制除濕機（power, mode, humidity）。自動模式啟用時拒收外部控制 |
| /api/devices/sensor | GET | 查詢感測器（device_name） |
| /api/sensors/status | GET | 所有感測器當下讀值 + 24h history（溫度 / 濕度 / CO2），給 Dashboard chart 用 |
| /api/ac/status | GET | 所有空調當下狀態 + 24h history snapshot，給 Dashboard chart 背景畫 AC on 區段用 |
| /api/ac/feedback | GET / POST | 僅未遷移 HA 空調、owner Key 專用：读取／保存空調補償；`evaluate_now: true` 可要求立即評估，省略 config 則只評估現有設定。符合條件才發 IR。Dashboard 另需成員 Session，不接受 Homebridge／家電語音 Key |
| /api/dehumidifier/auto-rule | GET | 列出所有除濕機的自動規則 + runtime state，並回傳後端計算的 `humidity_on_threshold` / `humidity_off_threshold`，供 Dashboard 共用同一組 hysteresis |
| /api/dehumidifier/auto-rule | POST | 設定 / 更新除濕機自動規則（device_name, auto_mode, sensor_name, duration_min, threshold, on_mode）。toggle ON 時會立即評估 sensor 當下值決定要不要 fire ON/OFF |
| /api/todos | GET | 依可信 `X-Dashboard-User` 過濾私人事項；無此 header 的 API-key 系統呼叫維持家庭級權限 |
| /api/todos | POST | 新增待辦事項 |
| /api/todos | PATCH | 依 `todo_id` 定位並重驗權限；舊呼叫可用明確名稱／日期／時間，重名拒絕 |
| /api/todos | DELETE | 完成可操作的待辦；Notion 項目保留完成記號 |
| /api/todos/light-reminders | GET | 回傳已到期、未完成、且燈光提醒=TRUE 的待辦（含 light_area_id/name），HA Hue 啟用時回空避免重複通知；否則供 PC agent 每分鐘依區域觸發 Hue breathe |
| /api/food | GET | 列出所有有效食品庫存 |
| /api/food | POST | 新增食品 |
| /api/food | PATCH | 修改食品 |
| /api/food | DELETE | 消耗（刪除）食品 |
| /api/schedules | GET | 預設列待執行；`include_attention=true` 加入失敗／待確認紀錄 |
| /api/schedules | POST | 新增一次性手動排程；HA 空調標記使用者（HA），到期經 HA 執行 |
| /api/schedules | DELETE | 取消待執行排程，或以 `execution_id` 封存失敗／待確認紀錄，不重送指令 |
| /api/weather | GET | 查詢天氣（date, location） |
| /api/members | GET | 列出所有啟用的家庭成員 |
| /api/recurring-todos | GET | 依同一可信使用者邊界過濾啟用模板，附人類可讀「摘要」；系統呼叫維持家庭級權限 |
| /api/recurring-todos | POST | 新增週期待辦模板（item, recur_type 每天/每週/每月/每季/半年/每年/間隔天，選填 weekdays/month_day/interval_days/time/person/type/light_notify/light_area/start_date/end_date；每季/半年/每年用 start_date 當錨點） |
| /api/recurring-todos | PATCH | 修改週期待辦模板（Dashboard 走 rule_id 精準定位，或用 item + recur_type 消歧） |
| /api/recurring-todos | DELETE | 停整個週期（模板狀態 → 停用，不刪除；可帶 rule_id 或 item + recur_type） |
| /api/auth/device/create | POST | Dashboard 裝置配對登入：發一組 6 位 user_code + device_token（device_token 由 PWA 保管）給前端顯示與輪詢用 |
| /api/auth/device/status | GET | Dashboard 裝置配對登入：PWA 帶自己保管的 `token`（device_token）輪詢配對狀態（pending/approved/expired/consumed/not_found）；approved 時回核准者身分 `{lineUserId, name, picture}` 並把狀態標成 consumed（單次使用） |
| /api/computers/heartbeat | POST | PC agent 每 60 秒 push 指標（cpu_pct, ram_pct, gpu_pct, gpu_temp_c, cpu_temp_c, fah, ip, hostname, cpu_model, gpu_model）|
| /api/computers/status | GET | 列出所有 PC 的 current snapshot + 24h raw history（每 60s 一點），供 Dashboard 折線圖渲染 |
| /api/agent/ws | WebSocket | PC Agent 主動連回 Render，提供 hello／heartbeat／在線狀態、劇院中繼，以及未切換 HA Hue 時的照明通道 |
| /api/agent/status | GET | 列出目前連線中的 agent 與 capabilities，需 `X-API-Key` |
| /api/lighting/areas | GET | 按設定透過 HA／PC Agent 讀取 Hue 區域、狀態、場景與燈效，合併 Sheet 顯示名稱；HA Hue 啟用時僅讀取本機選定區域 |
| /api/lighting/areas/{area_id} | PATCH | 更新 Hue 區域顯示名稱，寫入 Sheet「Hue 照明區域」 |
| /api/lighting/areas/{area_id}/state | PATCH | 控制 Hue 區域 grouped_light 的電源 (on) 與亮度 (brightness 1–100)，透過 agent 的 `hue.set_state` 下發 |
| /api/lighting/scenes/{scene_id}/recall | POST | 套用 Hue App 內已建立的一般 scene 或全天 smart_scene，透過 agent 的 `hue.recall_scene` 下發 |
| /api/lighting/areas/{area_id}/notification | POST | 對 Hue 區域下發通知動作，例如 `alert:breathe` 呼吸燈；若 Bridge 回傳 signaling 支援值也會列入通知清單 |
| /api/lighting/areas/{area_id}/effect | POST | 對區域內支援指定 effect 的燈具套用燈效，透過 agent 的 `hue.set_effect` 下發；部分支援時只套用支援的燈 |
| /api/lighting/breathe | POST | 按設定經 HA／PC Agent 對指定 Hue grouped_light 觸發 breathe |
| /api/lighting/auto/rules | GET | 相容舊客戶端，回空 rules 與 retired=true，不讀 Sheet |
| /api/lighting/auto/rules/{area_id} | PATCH | 已退役，回 410，不再啟用或儲存規則 |
| /api/lighting/auto/rules/{area_id} | DELETE | 已退役，回 410；舊 Sheet 設定保留 |
| /api/lighting/auto/sensors | GET | 舊網址保留的唯讀光感應器清單，不建立或評估規則 |
| /api/lighting/auto/sensors/{device_id}/light-level | GET | 唯讀光照：已遷移取 HA 快照，其餘讀 SwitchBot status；沒有夜燈 Webhook 快取 |
| /api/homebridge/devices | GET | 獨立橋接 Key，僅允許冷氣與同位置室溫感測器投影；直接讀快取，不查 Sheets／設備 |
| /api/homebridge/devices/{id}/ac | POST | 獨立橋接 Key，明確 AC 局部設定與 UUID request_id；即時重驗允許清單、保留防黴排程，不經 AI。完整契約見 homebridge/README.md |
| /api/assistant | POST | 自然語言入口（Siri 捷徑用）。body `{text, user_id?}`，與 LINE 共用意圖／控制，回 `{reply}`（語音精簡、去 emoji／朗讀格式）；每次只送當句，不帶歷史對話，仍背景存檔供查閱。`user_id` 不帶則用 `SIRI_USER_ID` |
| /api/assistant/devices | POST | 家電專用自然語言入口，只接受 `DEVICE_VOICE_API_KEY`。body 僅 `{text}`，1–500 字元；不接受 `user_id` 等額外欄位。回 `{reply}`，單句獨立解析，不讀寫家庭對話紀錄、不提供私人資料給模型 |

---

## Siri 語音控制（iOS 捷徑）

讓你用「嘿 Siri」就能控制家電、查詢、記待辦。Siri 只負責**把語音聽寫成文字**，POST 到 `/api/assistant`，剩下交給後端跑跟 LINE bot 一模一樣的 Claude pipeline（解析 → action 分派 → 組句），回傳 `reply` 給 Siri 朗讀。

### 運作原理

```
語音 →(Siri 聽寫)→ 文字 →POST /api/assistant→ process_message（共用 LINE 那套）→ {reply} →(Siri 朗讀)
```

`/api/assistant` 與 LINE webhook 共用 `assistant.py:process_message` 的意圖解析與控制。語音入口另外啟用 `voice=True`：純設備操作優先使用 handler 的實際執行結果，避免模型事先產生的冗長回覆；最後經 `voice_reply.py` 整理成適合朗讀的文字。LINE 保留原本的文字回覆路徑。

Siri 每次解析視為獨立指令：`voice=True` 會傳入 `ask_claude(..., include_history=False)`，一般及降級模型呼叫都只送當句文字，保留即時家庭／設備資料與使用者身分。初始 Sheets 載入略過「對話暫存」，但對話仍背景存檔供查閱。請說完整設備與動作，不依靠「再低一度」「把它關掉」或跨輪確認；涉及永久刪除／停止的追問，需保留上下文時改用 LINE。LINE 維持既有多輪對話；家電專用入口原本就不帶歷史，也不存對話。少數業務 handler 為辨識既有提醒仍可能補讀對話暫存，這不會把舊對話放進 Siri 的模型 messages。此變更未修改 prompt 內容，速度及辨識影響需用部署後的實測判斷。

### Siri 精簡回覆（v1.38.2）

- 原有捷徑繼續讀 `reply` 即可，不必新增來源參數；此格式用於整個 `/api/assistant` 語音入口，不影響 LINE webhook 與 Dashboard 設備 API。
- 例如 IR handler 回 `✅ 主臥電扇「開」指令已送出`，Siri 收到 `已送出主臥電扇的電源指令。`；IR 沒有狀態回讀，不宣稱設備已開啟／關閉。
- 移除 emoji、Markdown 裝飾；選項中的 `/` 換成停頓，百分比／攝氏溫度／完整斜線日期轉成可朗讀文字。保留負數、數值、時間；無法判定語意的數字斜線（例如 `3/4`）保留，不亂猜日期或比例。
- 失敗、結果未確認、部分成功及追問不截斷；查詢與混合指令保留原本內容再做格式整理。沒有另外增加 AI 呼叫，也不修改共用 SYSTEM_PROMPT。
- `對話暫存` 保存實際收到的輸入及格式化後的語音回覆，以便比對手機實際聽到的內容。

### 身分辨識（重要）

捷徑送的 HTTP request 除了你寫進去的內容**沒有任何身分資訊**（不像 LINE 有簽章帶 user_id），所以「誰在講話」只能靠捷徑裡帶的 `user_id`：

- **每位家人（含自己）的捷徑都各自帶自己的 Line User ID** → 後端認得出是誰，給對應名字、自訂風格、各自的對話紀錄（Siri 不帶歷史）。
- 沒帶 `user_id` → fallback 成 `SIRI_USER_ID`（預設中性 `siri`）：當匿名訪客，照常控制家電，但無名字/無風格、記憶獨立一份，不會冒名任何人。
- 因此 **`SIRI_USER_ID` 環境變數不要設成任何人的真實 Line ID**，維持中性即可。
- Line User ID 怎麼拿：見上方「取得家庭成員 Line User ID」，或直接從「家庭成員」分頁複製。

### iPhone 捷徑設定

「捷徑」App → **+** 新增捷徑，依序加 4 個動作：

1. **聽寫文字（Dictate Text）**
   - 把語音轉成文字。語言設成「國語（台灣）」。
   - 展開把 **「停止聽寫」設成「暫停後（After Pause）」**，這樣講完停頓就自動結束，不會卡在等待狀態。

2. **取得 URL 內容（Get Contents of URL）**
   - URL：`https://<你的 render 網址>/api/assistant`
   - 展開「顯示更多」：
     - **方法**：`POST`
     - **標頭**：新增一列 `X-API-Key` = 你的 `HOME_BUTLER_API_KEY`
     - **請求內文**：選 `JSON`，新增欄位：
       - 文字 `text` = 步驟 1 的「聽寫文字」變數
       - 文字 `user_id` = **你自己的 Line User ID**（每人填自己的）

3. **取得字典值（Get Dictionary Value）**
   - 取得「值」，鍵 `reply`，來源是步驟 2 的「URL 內容」。

4. 讓步驟 3 的**「字典值」當捷徑的最終輸出** → 用「嘿 Siri」喊出來時，Siri 會**自動朗讀**這段 reply。
   - ⚠️ 不要靠「朗讀文字（Speak Text）」動作：它只在編輯頁測試時出聲，透過 Hey Siri 觸發時會被 Siri 的 audio session 壓掉而不朗讀。把回覆文字當最終輸出交給 Siri 唸才可靠。

把捷徑取個好喊的名字（例如「管家」），之後說「**嘿 Siri，管家**」→ 聽到提示音 → 講指令（「把冷氣調到 26 度」）→ Siri 朗讀回覆。

### 語音漏字與設備名稱（2026-09-06）

使用者回報：手動輸入「打開主臥電風扇」可送出，但語音有時只傳入「主臥電風扇」；iPhone 即時辨識畫面顯示完整句子，也不代表捷徑最後取得的文字完整。「要求輸入」可自訂提示，但本次回報中它與「聽寫文字」都曾漏掉開頭動作，尚未確認是否與 Siri／HomeKit 意圖處理有關，也不能宣稱切換動作即可修復。

- 用不連 API 的「聽寫文字 → 顯示結果」小捷徑，比較播放鍵啟動與 Siri 啟動的輸出；保留 iOS 版本、啟動方式、原句及實際結果。
- 後續使用者對照確認：直接聽寫不漏「打開」，Siri 啟動會漏。建議在開頭加「關閉 Siri 並繼續」（Dismiss Siri and Continue），接「朗讀提示 → 聽寫文字 → API → 取得 reply → 朗讀回覆」；使用者回報此方式看起來可用。此路徑由捷徑負責朗讀，與上方 Siri 保持啟用、讀取最終輸出的流程不同；鎖定手機／HomePod 未驗證，不承諾免解鎖，也未證實是 HomeKit 攔截。
- 後端 `對話暫存` 的 `user` 內容是 `/api/assistant` 收到的文字（去除頭尾空白）；完整設備指令卻找不到名稱與動作漏字是不同問題。
- IR 控制現在先匹配啟用 IR 設備的完整名稱，再以結尾「電風扇／電扇」作等價比對，保留房間前綴。唯一匹配才送出，使用設定中的 Device ID 與名稱回覆。不做任意模糊比對、不替遺失的開／關補動作。
- 名稱省略且僅有一台啟用 IR 設備時可自動選取；明確指定但不相符的名稱不再改用唯一一台設備。重名、別名歧義、停用、錯誤類型或缺少 ID 都拒絕送出。
- 新比對器涵蓋 LINE／Siri、Dashboard IR 端點及執行時走共用 IR handler 的排程；排程的建立／修改仍有各自的設備定位，不代表所有入口都已支援別名。也不保證 AI 在收到完整文字後一定產生正確 action。

### 家人共用

需要完整功能的家人可以使用原捷徑，在自己手機把 `user_id` 改成自己的 Line User ID。這是對話身分，不是權限憑證；持有 `HOME_BUTLER_API_KEY` 代表完整 API 存取能力，留空 ID 也不會變成受限使用者。多支完整捷徑沒帶 ID 時會共用 `siri` 對話紀錄，但 Siri 模型請求不帶入歷史。

<a id="device-only-voice"></a>

### 家電專用捷徑：長輩／小孩（v1.39.0）

不必加入 LINE 家庭成員，也不必填 User ID。權限由獨立的金鑰及後端入口決定，不由 Siri 聲紋、年齡或傳入的使用者名稱決定。

1. 在自己的終端執行 `python -c "import secrets; print(secrets.token_urlsafe(32))"`，另產生一把新金鑰。
2. 在 Render 的 home-butler 服務 **Environment** 新增 `DEVICE_VOICE_API_KEY`，貼上新金鑰並套用部署。保留原本 `HOME_BUTLER_API_KEY`。新金鑰至少 32 字元且必須不同；沒有設定時新入口保持停用。
3. 複製已能正常收音的捷徑，改名為「家電管家」。保留目前的「關閉 Siri 並繼續 → 朗讀提示 → 聽寫文字」流程；調整取得 URL 內容：
   - URL：`https://<你的 render 網址>/api/assistant/devices`
   - 方法：`POST`
   - 標頭：`X-API-Key` = 新的 **DEVICE_VOICE_API_KEY**；`Content-Type` = `application/json`
   - JSON 主體只留 `text` = 聽寫文字，**刪除整個 `user_id` 欄位**（不是留空）；不要傳 `role`、`mode` 或 `actions`。
4. 繼續取回字典的 `reply` 並朗讀。分享前檢查副本不再含原本的完整金鑰或 User ID；只分享這個副本。

| 可用 | 不提供 |
| --- | --- |
| 已啟用的 IR 設備開關及已設定按鈕（如電扇風速） | 待辦、食品的查詢／新增／修改／刪除 |
| 冷氣開關、16–30 度、模式及風速 | 成員資料、個人風格、私人對話或身分切換 |
| Panasonic／LG 除濕機開關、支援的模式、40–70% 每 5% 一級的目標濕度 | 建立／修改／查詢排程、修改除濕機自動規則 |
| 感應器溫濕度、除濕機狀態、上述支援設備清單 | 未列入允許清單的其他 API（包含 Hue／劇院管理） |

使用完整單句，例如「打開主臥電扇」「主臥冷氣調到二十六度」「客廳除濕機濕度設五十五」。此模式沒有共用聊天記憶，「再低一度」這類依賴上一句的指令需改說完整名稱及設定值。IR 只能確認指令送出，不能確認實際開關狀態。

後端只載入 `智能居家` 並將名稱、類型、位置、按鈕、控制類型及品牌投影給專用解析器，**不傳 Device ID、Auth、家庭成員、待辦、食品或對話紀錄**。模型解析後，後端會先驗證整批動作、參數範圍及唯一設備名稱，全部通過才執行；不使用完整 `ACTION_HANDLERS`。範圍外要求回固定拒絕句，不由模型編造查詢結果。最多四個動作；有效動作執行時的硬體失敗不具交易回復能力，發生例外會保留前面結果、停止後續動作且不自動重送。

未遷移 HA 的冷氣控制仍由原 handler 維護既有防黴收尾與自動關機排程；HA 管理空調不執行這些舊規則。這是設備控制的必要副作用，不代表此金鑰可直接管理排程。除濕機自動模式鎖定時，仍需由完整 Dashboard 關閉自動模式才能手動控制，家電捷徑不能繞過鎖定。

錯誤判讀：401 為金鑰錯誤；422 通常是仍帶 `user_id`／多餘欄位或輸入長度錯誤；400 為全空白文字；503 可能是未啟用、金鑰設定不合規或上游暫時不可用。停用時移除 `DEVICE_VOICE_API_KEY` 並套用部署；輪替則換新值及更新共用捷徑，原家電金鑰失效，完整金鑰不必更換。所有持有家電金鑰的人權限相同；沒有個人識別、逐人撤銷或逐台設備限制。

離線驗證涵蓋真正的 FastAPI 認證與輸入驗證、假模型／Sheets／設備處理；部署後仍需確認 Render 設定與手機收音。家電模式不寫 `對話暫存`，若要確認 Siri 有無漏字，先在捷徑顯示聽寫文字；不要靠私人對話歷史除錯。

---

## Claude API 支援的 action

### 食品庫存

| action | 說明 | 欄位 |
|--------|------|------|
| add_food | 新增食品 | name, quantity, unit, expiry |
| delete_food | 食品全部用完，移至封存 | name |
| modify_food | 修改食品 | name，選填：name_new, quantity, unit, expiry |
| query_food | 查詢食品庫存 | 無 |

### 待辦事項

| action | 說明 | 欄位 |
|--------|------|------|
| add_todo | 新增待辦（指派他人時自動通知） | item, date，選填：time, person, type, light_notify, light_area |
| modify_todo | 修改待辦（唯讀項目會被拒絕） | item，選填：item_new, date, time, person, type, light_notify, light_area |
| delete_todo | 標記完成，移至封存（唯讀項目會被拒絕） | item |
| query_todo | 查詢待辦（自動同步外部行事曆） | 無 |
| add_recurring_todo | 新增週期提醒（系統維持一筆「下一次要做的」待辦，完成後才出現再下一次） | item, recur_type（每天/每週/每月/每季/半年/每年/間隔天），選填：weekdays（每週，[1,3,5]，一=1…日=7）, month_day（每月，1~31）, interval_days（間隔天，>=1）, time, person, type, light_notify, light_area, start_date（每季/半年/每年的錨點）, end_date |
| modify_recurring_todo | 修改週期提醒（多筆同名加 recur_type 消歧） | item，選填：item_new, recur_type_new, weekdays, month_day, interval_days, time, person, type, end_date |
| stop_recurring_todo | 永久停止整個週期（模板改停用，可再啟用；執行前先反問確認） | item，選填：recur_type |
| query_recurring_todo | 列出啟用中的週期提醒 | 無 |

有時間的家事/起身處理類待辦（例如收衣服、倒垃圾、拿包裹、關瓦斯）在對話新增時會預設開啟 `light_notify=true`；使用者明確說不要燈光提醒時會優先關閉。若只說要燈光提醒但沒指定區域，預設使用客廳。

週期 vs 單次：說「每天/每週X/每月N號/每隔N天提醒」走 `add_recurring_todo`；說「明天/某個日期」走 `add_todo`。對週期產生出的當次待辦說「做完了」用 `delete_todo`（只完成當次，模板不動、下次照常出現）；說「不要再…了/停掉每天的X」才用 `stop_recurring_todo`（永久停整個週期，執行前會先反問確認）。

### 智能居家

| action | 說明 | 欄位 |
|--------|------|------|
| control_ac | 控制空調（IR） | device_name，選填：power, temperature, mode, fan_speed |
| control_ir | 控制 DIY IR 設備 | device_name, button（開/關自動轉 turnOn/turnOff） |
| query_sensor | 查詢溫濕度感應器 | device_name |
| query_devices | 列出所有已設定設備 | 無 |
| control_dehumidifier | 控制除濕機 | device_name，選填：power, mode, humidity |
| query_dehumidifier | 查詢除濕機狀態 | device_name |
| set_dehumidifier_auto | 設定 sensor 條件式自動除濕模式；可說「主臥除濕機開自動，目標55%」或「全家除濕機都開自動模式，目標55%」。未指定 sensor 時依除濕機位置配對同位置感應器，沒有同位置感應器會回報 | device_name 或 scope=all, auto_mode(on/off), threshold，選填：duration_min, sensor_name |

### 天氣

| action | 說明 | 欄位 |
|--------|------|------|
| query_weather | 查詢天氣預報（含體感溫度） | 選填：date（YYYY-MM-DD，最多 7 天）, location（鄉鎮或縣市） |

天氣查詢使用兩次 Claude API 呼叫：第一次解析使用者意圖（查哪裡、哪天），第二次根據實際天氣數據用管家語氣回覆。支援自然語言如「週末台北冷嗎」「明天會下雨嗎」。

### 廣播

在 LINE 傳送 `@all 訊息內容` 即可對全體家庭成員發送廣播訊息。

- 格式：`@all` + 空格 + 內容
- 全體啟用中的家庭成員都會收到（包含發送者自己）
- 訊息格式：`📢 發送者名稱：內容`
- 廣播內容會存入每位收到者的對話暫存，Claude 可參考上下文
- 不經過 Claude 解析，直接發送

### Dashboard 登入（裝置配對）

在 LINE 傳送 `登入 123456`（6 位驗證碼）即可核准一台 Dashboard PWA 登入。

- 驗證碼由 Dashboard 登入頁顯示，使用者在 Bot 輸入後核准，前端輪詢拿到 session
- 身分（lineUserId / 名字 / 頭像）來自「誰在 Bot 輸入碼」（webhook user_id，已由 LINE 認證）
- 非家庭成員輸入會被拒絕
- 不經過 Claude 解析，regex 抽出 6 位純數字後直接核准（早退、零成本）；對應後端 `device_auth.py` 與 `/api/auth/device/*` 端點

### 排程

| action | 說明 | 欄位 |
|--------|------|------|
| add_schedule | 新增定時排程 | device_name（可唯一設備可省略）, target_action, params, trigger_time |
| delete_schedule | 取消排程（移至封存） | device_name，選填：trigger_time, all |
| query_schedule | 查詢目前所有待執行排程 | 無 |

設備排程由獨立 schedules 工作每 60 秒檢查一次，通常延遲在一個檢查週期內（仍受網路、服務休眠影響）。觸發時間超過 2 小時未執行的排程自動標記為已過期。設備所有排程完成後統一通知建立者（含執行結果與設備目前狀態）。

**冷氣防黴送風（僅未遷移 HA 的空調）**：關冷氣時，若這次以冷氣/除濕（會結露的模式）從最後一次開機算起已運轉 ≥30 分鐘，home-butler 不直接關，而是先切「送風」吹乾蒸發器約 5 分鐘（正常情況另加不到一個 60 秒排程週期，仍受網路、工作耗時與服務休眠影響）再由排程自動關閉，降低濕氣悶在機內長黴。全部空調自動套用、免設定；送風期間若重新開冷氣，收尾關會自動取消。經 home-butler 的所有關機路徑（LINE / Dashboard / Siri / Hub 2 按鈕 / 自動關機 timer）都會觸發；唯獨直接用實體遙控器關機因繞過 home-butler 無法攔截。對應排程在「排程指令」分頁以「來源=防黴」標記。**門檻（預設 30 分）與送風時長（預設 5 分）可在「智能居家」分頁逐台調整**：欄位「防黴運轉門檻分鐘」「防黴送風分鐘」，留空用預設、門檻填 0 代表每次關都送風。

### 風格

| action | 說明 | 欄位 |
|--------|------|------|
| set_style | 設定管家回覆風格 | style（精簡 prompt 指令，30 字以內；空字串 = 恢復預設） |

### 其他

| action | 說明 | 欄位 |
|--------|------|------|
| unclear | 語意不清時反問 | message |

---

## 回覆邏輯

| 情境 | 回覆來源 |
|------|---------|
| 食品 / 待辦的查詢 | Sheet 真實數據（含同步的外部行事曆）→ 第二次 Claude 用管家語氣回覆 |
| 食品 / 待辦的操作（新增、修改、刪除） | Claude 的 reply（管家語氣） |
| 操作唯讀項目（外部行事曆） | 系統回傳提示，請使用者到原本的日曆上操作 |
| 天氣查詢 | 兩次 Claude：第一次解析意圖 → 查天氣 API → 第二次根據數據回覆 |
| 溫濕度查詢 | 查感應器 API → 第二次 Claude 用管家語氣回覆 |
| 設備列表、除濕機狀態 | 程式的即時數據結果 |
| 設備控制成功 | Claude 的 reply |
| 排程操作（新增、取消、查詢） | Claude 的 reply |
| 排程自動執行 | `schedules` 工作執行，回報已執行／失敗／待確認結果給建立者 |
| 設備控制失敗（❌） | 程式的實際錯誤訊息 |
| 廣播（@all） | 直接轉發，不經 Claude |
| 指派待辦給他人 | Claude 的 reply + 自動推送通知給被指派者 |
| 查看風格（「查看風格」「我的風格」「目前風格」） | 直接讀取 Sheet 原值回覆，不經 Claude |
| 設定風格（set_style） | Claude 的 reply（管家語氣） |
| Claude 回傳異常 | 友善的錯誤提示，不顯示 traceback |

---

## 推播機制

| 推播 | 觸發時間 | 內容 |
|------|---------|------|
| 晚間綜合推播 | 晚上 9 點 | 明日天氣預報（含今日比較與體感溫度）+ 食品過期提醒 + 明日與未完成待辦（週期待辦因為「下一筆」平常就已生成為普通待辦，會自然被這裡納入，不需另外從模板預告） |
| 即時提醒 | 每 5 分鐘 | 三階段，各發一次：任務前 20 分內事前提醒／逾時 10~60 分「未完成」／逾時滿 1 小時起每小時「已逾時約 N 小時」（逾時提醒只在任務當天發，過午夜自停）；設備排程另由每 60 秒工作執行 |
| Hue 燈光提醒 | HA Hue 啟用時 HB 每 60 秒送 HA；否則 PC agent 每 60 秒 | 有時間、已到期、未完成且燈光提醒=TRUE 的待辦，對每筆設定的 Hue grouped_light 觸發 breathe；同一區域同一輪多筆待辦只呼吸一次 |
| Agent 即時通道 | PC agent 常駐 WebSocket | PC agent 每約 25 秒 heartbeat 到 `/api/agent/ws`，後端可用 `/api/agent/status` 確認在線狀態 |

**晚間綜合推播**的訊息由 Claude 組成自然語氣文字，包含貼心提醒（快過期催促、天氣變化提醒等）。原本分為早上每日推播與晚間天氣兩次推播，現已合併為單一晚間綜合推播，減少 LINE 推播額度消耗。

**即時提醒**則相反：文字**由程式規則產生的固定字串**（不經 Claude 潤飾）。因為去重是用「這則文字有沒有出現在最近對話裡」精確比對，Claude 每次改寫措辭會讓比對失效 → 每個 tick 重發洗版（實際發生過）。文字內嵌逾時小時數，於是同一小時內相同（被去重擋掉）、跨小時才不同（放行下一次）。

---

## 自動夜燈機制

v1.53.0 起已移除 HB 夜燈引擎與 Dashboard 表單。背景五分鐘工作、Webhook、HA 環境快照
皆不再觸發 HB 自動開關燈；舊「照明自動規則」Sheet 保留，不載入、不修改，原 enabled 值不會使規則復活。
舊讀取 API 回空規則並標示 retired，寫入／刪除回 410。夜燈請在 HA 建立；本次未新增或更動 HA 規則。

SwitchBot Webhook 仍轉送 HA 已選 Hub 的更新提示，HA 原生 API 驗證讀值及整合內備援輪詢不受影響。
HB 待辦燈光提醒已改用獨立共用傳輸函式，繼續經 HA Hue 執行；除濕機全部維持 HB。
舊版夜燈設計請看 [歷史版本](version-selection.md)，回復舊程式前先檢查 Sheet 的 enabled 值，避免重複規則。

---

## Aqara FP2：HA 主路徑與舊雲端工具

目前推薦 FP2 → HA HomeKit Device → HA HomeKit Bridge／HomeButler。
這條路徑可讓 HA 本地自動化與 Apple Home 使用存在／亮度資料，HB 同步明確選取的觀測，
不需申請 Aqara 開發者帳號。安裝見 [HA 整合](../homeassistant/README.md)。

以下僅保留 `aqara_api.py` 舊雲端探索工具的設定。它能授權、列裝置與讀 resource，
但不等同目前 HA 空間感測同步，也沒有自動把資料接進 Dashboard／LINE 或背景輪詢。
選用 HA 的使用者可以跳過以下 Aqara 雲端設定。

### 一次性設定流程

1. **建應用拿憑證**：到 [developer.aqara.com](https://developer.aqara.com/) 建立應用，
   取得 App ID / Key ID / App Key，填進 Render 環境變數 `AQARA_APP_ID`、`AQARA_KEY_ID`、
   `AQARA_APP_KEY`，另外填 `AQARA_ACCOUNT`（你的 Aqara 帳號）。
2. **找機房**：`GET /aqara/probe`。App 憑證是綁機房的，所以**回權杖相關錯誤的那一區就是
   你的**（代表它認得你的 AppId、只是還沒授權）；回 appid / sign 錯誤的不是。把區碼填進
   `AQARA_REGION`（清單外的機房則填 `AQARA_API_BASE`）。
3. **要授權碼**：`POST /aqara/auth/code` → Aqara 寄一組碼到你的 Email / 手機。
4. **換權杖**：`POST /aqara/auth/token?auth_code=<剛收到的碼>`。成功後 accessToken /
   refreshToken 會寫進 Sheet「系統狀態」分頁，**跨 Render 重啟存活**，之後全自動續期
   （到期前 10 分鐘主動換；真的過期也會在下一次呼叫吃到 code 108 時自動補換）。
5. **對 resource id**：`GET /aqara/devices` 抓 FP2 的 did → `GET /aqara/devices/{did}/values`
   把所有欄位連當下值印出來。人走進 / 走出感測範圍各打一次，diff 一下就知道哪個
   resource id 是「有沒有人」，填進 `AQARA_FP2_PRESENCE_RESOURCE`（順手把 did 填進
   `AQARA_FP2_DID` 省一趟 API）。

第 5 步之前 `/aqara/fp2` 的 `presence` 是靠**名稱關鍵字猜**的，猜不到就回 `null`——
不會硬挑一個看起來像的欄位假裝知道。不管猜中沒有，原始資源都原樣附在 `resources` 裡。

### 為什麼沒有寫死 resource id

Aqara 的每個欄位都是一組 `x.y.z` 數字（例如 `3.51.85`），官方文件按 model 分開列，
網路上找得到的多半是別人抄來抄去、對不上自己那台韌體的版本。**寫死一組猜來的 id，
錯了會靜默失效**——讀到的永遠是空值，而不是報錯。

所以 `aqara_api.read_device()` 是「先打 `query.resource.info` 問這個 model 開放哪些
resource，再照那份清單去讀值」，清單快取 6 小時。這樣新裝置（FP2 以外的 Aqara 產品）
不用改任何 code 就能讀，也不會因為韌體改版多了欄位而漏讀。

---

## 資料封存機制

| 分頁 | 觸發條件 | 封存至 |
|------|----------|--------|
| 食品庫存 | delete_food 或 modify_food 數量歸零 | 食品封存 |
| 待辦事項 | delete_todo | 待辦封存 |
| 對話暫存 | 同一用戶超過 6 則（預設值，可調整） | 對話封存 |
| 排程指令 | 排程執行完成或被取消 | 排程封存 |

封存分頁 Claude 不會讀取，只作為歷史紀錄保存。

---

## 外部行事曆整合

### 設計原則

- **同步快取**：外部行事曆事件同步寫入待辦事項 Sheet，讓 Claude 隨時可見
- **屬性控制**：透過「來源」和「屬性」欄位區分本地/外部、可讀寫/唯讀
- **每人獨立**：每個家庭成員可設定不同的外部行事曆來源、篩選條件和權限
- **Sheet 控制**：透過「家庭成員」分頁的欄位控制整合行為，不用改程式碼
- **定期同步**：query_todo、/notify、/notify_realtime 都會觸發同步（依穩定 ID 差異更新，不重建整張表）

### 同步機制

每次同步時（`sync_external_events`）：
1. 在待辦寫入鎖外抓取每位成員的 Notion 事件；失敗成員不動資料。
2. 取得待辦共用寫入鎖後重讀 Sheet，以外部ID及成員計算差異，保留同步期間新增的完成記號。
3. 先更新欄位，再由下往上刪列，最後插入新列；不變時不寫資料。完成記號、待辦ID與燈光提醒受到保留。同步不回寫 Notion。

同步頻率：
- 使用者查待辦時即時同步
- /notify（每日推播）觸發同步
- `notion` 獨立工作每 300 秒觸發同步；`/notify_realtime` 僅保留手動補做入口

### 目前支援

| 來源 | 設定欄位 | 篩選欄位 | 權限欄位 |
|------|---------|---------|---------|
| Notion | Notion Database ID | Notion 篩選 | Notion 權限 |

### 篩選語法

格式：`欄位名:值,欄位名:值`（逗號分隔，AND 關係）

- 包含條件：`Status:Incoming` — Status 欄位包含 "Incoming"
- 排除條件：`名稱分析:!休假事件` — 名稱分析欄位不包含 "休假事件"
- 比對不分大小寫

### 顯示方式

- Claude 的 context（`{todo_info}`）中，唯讀項目以 `[唯讀]` 前綴顯示
- 使用者嘗試刪除或修改唯讀項目時，系統直接回傳提示而非「找不到」
- 推播中外部行事曆事件以 📅 標記

---

## 天氣功能技術細節

- **API**：中央氣象署開放資料 F-D0047 系列（鄉鎮一週逐 12 小時預報）
- **涵蓋範圍**：全台 22 縣市所有鄉鎮，未來 7 天
- **地點解析**：支援模糊比對，「竹北」→ 自動嘗試竹北市/區/鄉/鎮，遍歷所有縣市。台/臺自動轉換
- **顯示資訊**：天氣現象、最高/最低溫度、體感溫度、降雨機率
- **主動查詢**：使用者問天氣 → Claude 解析意圖 → 程式查天氣 API → Claude 用管家語氣回覆
- **推播**：早上帶今日天氣（含體感溫度），晚上帶明日+今日天氣（支援溫差比較）

---

## 效能優化

- **Google Sheets 批次讀取**：RequestContext 使用 values_batch_get 一次讀取所有分頁，取代原本多次個別 API 呼叫
- **Google Sheets 連線重用**：同一程序持續重用已認證的 spreadsheet／HTTP session，不再每 60 秒重建；google-auth 按需要更新憑證，既有暫時性重試用盡後讓下次存取重建連線。初始化以 RLock 避免同時重建；不是跨程序鎖，也不快取業務資料或長期保存欄位位置。
- **未遷移冷氣的狀態寫入**：成功送出後，一次讀取「智能居家」最新內容，依 Device ID 唯一定位設備列及現有欄位，再一次 RAW 批次寫入；省掉 worksheet metadata 查詢，也不再沿用請求開始時的舊列號。缺表、重複欄名／ID 或目標消失時不寫入，寫入失敗不重送設備。Google Sheets 仍無讀取與寫入之間的交易保障。
- **統一裝置狀態快取**：Dashboard 讀取既有快取與 HA 即時投影。已遷移設備不再直接雲端查詢；HA 失聯回未知，歷史不替代現在狀態。
- **Google Sheets 集中寫入**：新增資料統一走 `append_record()`，多欄位修改統一走 `update_row_fields()` 的 batch update，減少 API 呼叫也避免欄位位置散落在 handler 裡
- **背景寫入**：save_conversation（對話暫存）在背景 thread 執行
- **先回覆再存檔**：reply_message 在 save_conversation 之前，使用者體感更快
- **精簡 SYSTEM_PROMPT**：減少 token 數，加速 Claude 回應
- **Notion API 日期預過濾**：在 API 層加 `on_or_after` 過濾，只拉今天以後的事件，大幅減少回傳資料量
- **外部行事曆批次寫入**：sync_external_events 使用 `append_rows` 一次寫入所有事件，減少 API 呼叫次數
- **快取索引同步**：多筆刪除操作時同步更新記憶體快取，避免行號偏移

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| main.py | FastAPI 主程式（LINE Webhook、SwitchBot Cloud webhook 接收與 startup 註冊、啟動 polling thread、SwitchBot debug 端點）。訊息處理委派給 `assistant.py` |
| assistant.py | 自然語言處理核心：`process_message`（Claude 解析 → action 分派 → 組句）與 action 路由表。LINE webhook 與 `/api/assistant`（Siri）共用，避免邏輯複製兩份 |
| device_voice_api.py / device_voice.py | 家電專用語音入口、獨立模型目錄與 schema、動作及參數白名單。只掛載 `/api/assistant/devices`，不使用完整對話 pipeline；`tests/test_device_voice.py` 驗證權限邊界 |
| config.py | 環境變數、LINE/Claude 初始化、時區設定 |
| sheets.py | Google Sheets 存取封裝（RequestContext 批次讀取、快取、append_record / update_row_fields 集中寫入） |
| device_status.py | Dashboard 共用的統一裝置狀態 in-memory cache、裝置目錄與背景刷新 single-flight 控制 |
| prompt.py | SYSTEM_PROMPT 與 Claude 提示詞組裝 |
| conversation.py | 對話暫存管理、Claude API 呼叫、推播訊息生成 |
| notify.py | 獨立背景工作的排程／待辦／推播函式；/notify 與 /notify_realtime 為手動補做入口 |
| calendar_sync.py | 外部行事曆同步（Notion → 待辦 Sheet） |
| health_alert.py | Agent 失聯告警：PC agent heartbeat 斷線 / 劇院 agent 無回應時推 LINE，狀態翻轉才推、marker 存 Sheet 跨重啟去重。**只觀察不控制任何設備** |
| handlers/food.py | 食品庫存 handler（新增、刪除、修改、查詢） |
| handlers/todo.py | 待辦事項 handler（新增、刪除、修改、查詢） |
| handlers/recurring_todo.py | 週期性待辦：模板/實例分離，每 5 分鐘確保每條啟用規則都掛著一筆「下一次」待辦（冪等依據＝活表有無該規則的待辦實例，完成/刪除才補下一筆）+ add/modify/stop/query 的 CRUD handler，受 RECURRING_TODO_ENABLED 控制生成 |
| handlers/todo_helpers.py | 待辦 / 週期待辦共用工具（燈光提醒自動判斷、布林解析、照明區域解析），從 todo.py 抽出供兩邊複用 |
| handlers/device.py | 智能居家 handler（空調、IR、感應器、除濕機、天氣） |
| handlers/schedule.py | 排程指令 handler（新增、刪除、查詢） |
| handlers/style.py | 自訂風格 handler |
| switchbot_api.py | SwitchBot API v1.1 封裝（認證、設備控制、感應器讀取 含 Meter Pro CO2、DIY IR、webhook 註冊管理） |
| panasonic_api.py | Panasonic Smart App API 封裝（登入、除濕機控制與狀態查詢） |
| lg_api.py | LG ThinQ Connect API 封裝（PAT 認證、裝置探索、除濕機控制與狀態查詢）。除濕機 property 校準點集中在檔案頂部常數 |
| aqara_api.py | Aqara Cloud Open API v3.0 封裝（MD5 簽名、accessToken/refreshToken 生命週期與 Sheet 持久化、裝置與 resource 探索、FP2 語意層）。**沒有寫死的 resource id**——先問 `query.resource.info` 再照清單讀值 |
| weather_api.py | 中央氣象署 API 封裝（一週預報、全台鄉鎮查詢、體感溫度） |
| observation_api.py | 中央氣象署觀測站即時資料 API（補 weather_api 預報以外的「現在實際多少」） |
| notion_api.py | Notion API 封裝（唯讀查詢、Sheet 篩選條件解析、事件格式化） |
| web_api.py | REST API（裝置控制、待辦、週期待辦、食品、排程、天氣、成員查詢、PC 監控、感測器/空調歷史、除濕機自動規則、Dashboard 裝置配對登入，以及 Siri 自然語言入口 `/api/assistant`） |
| device_auth.py | Dashboard 裝置配對登入（OAuth Device Grant 風格）：發 user_code/device_token、LINE Bot 端核准、PWA 輪詢領 session。狀態存 Sheets「裝置配對」分頁。解 iOS PWA 登入被踢去 Safari 的問題 |
| agent_ws.py | PC agent WebSocket registry（agent hello / heartbeat / 在線狀態），讓 Render 有一條可回到家中區網的即時通道 |
| lighting_api.py | Hue 區域、電源、亮度、場景、通知與燈效 API，依 lighting_transport 送 HA／PC；保留已退役夜燈端點的明確回覆與唯讀光照探查 |
| lighting_transport.py / lighting_reminders.py | 共用 HA／PC 照明通道與背景待辦提醒；不依賴已移除的夜燈模組 |
| hue_area_settings.py | Sheet「Hue 照明區域」讀寫：保存 Hue ID 與 Dashboard 顯示名稱的對應 |
| pc_state.py | PC 監控 in-memory ring buffer（24h × 60s/PC），給 `/api/computers/heartbeat` 寫、`/api/computers/status` 讀 |
| sensor_state.py | 感測器 24h 歷史與 Sheet append/backfill；HA 即時投影／未遷移 sensor_polling 與五分鐘歷史採樣分離 |
| sensor_polling.py | 感測器共用讀取與每分鐘回饋取值；同一 Device ID／時段去重，補償只套用一次，不直接寫歷史或 Sheet |
| ac_history.py | 空調 24h 歷史與 Sheet append/backfill；已遷移設備使用 HA 狀態，未遷移使用 Sheet 最後指令 |
| ring_buffer.py | pc_state / sensor_state / ac_history / dehumidifier_history 共用的純機制（`to_float_or_none`、24h `trim_sheet`）。各模組資料形狀差異刻意不抽繼承基類，只共用這兩段逐字重複的工具 |
| dehumidifier_auto.py | 除濕機條件式自動 ON/OFF（hysteresis + sensor 失聯 fallback + 排他鎖）。品牌無關狀態機，控制/狀態委派給 dehumidifier_driver。runtime state in-memory，rule 設定值持久化到 Sheet「除濕機自動規則」 |
| dehumidifier_driver.py | 除濕機品牌無關 driver：把 Panasonic（auth+gwid+CommandType）與 LG（deviceId+ThinQ property）的控制/狀態差異收斂成統一介面，給 dehumidifier_auto 用。LG 自動模式用「智慧除濕」+ 機體目標 = 外部目標 −`AUTO_TARGET_OFFSET`(10%) |
| dehumidifier_history.py | 除濕機 ON/OFF 狀態 in-memory ring buffer（24h）+ Sheet append/backfill，給 Dashboard 自動模式 chart 背景畫運轉區段 |
| agent/ | Windows PC 端 monitoring agent（agent.py + agent_config.example.py + README）含 watchdog 防 hang + 預設每 5 分鐘自動 git pull + py_compile 驗新 code syntax + 自己 spawn detached process 重啟（不靠 Task Scheduler restart-on-fail）。Render 部署不會 import 這個目錄 |
| requirements.txt | 直接依賴（程式直接 import 的，已 pin 版本）。升級從這裡改起 |
| requirements.lock | 完整鎖檔（含 transitive，共 51 套），由 requirements.txt 在乾淨環境 pip freeze 重生。render.yaml 實際安裝走這個檔以求每次 rebuild 可重現 |
| render.yaml | Render.com 部署設定 |

---

## 後續維護

**調整 Bot 行為**：修改 prompt.py 裡的 SYSTEM_PROMPT，push 後自動重新部署。

**新增功能**：
1. Google Sheets 新增對應分頁
2. 更新 prompt.py 的 SYSTEM_PROMPT 加入新功能描述
3. 在 handlers/ 目錄加入對應的 handle 函數
4. 在 assistant.py 的 `ACTION_HANDLERS` 註冊新的 action 路由（query 類另需視情況加進 `SEMANTIC_ACTIONS` / `REALTIME_ACTIONS`）
5. 如需推播，更新 notify.py 的推播邏輯

**修改 Google Sheets 寫入**：新增列請用 `sheets.append_record()`；同一列多欄位修改請用 `sheets.update_row_fields()`，不要在 handler 裡分散呼叫多次 `update_cell()`。欄位名稱應集中放在該分頁的 header 對照，避免日後調整欄位順序時要到多個 handler 找行號與欄位號。

**新增 SwitchBot 設備**：先決定交由 HA 或 HB 管理，依本頁「SwitchBot 智能居家設定」操作。
HA 路徑需原生整合、HB 名稱配對及對應允許清單；未遷移路徑才新增後端直連設備設定。
新增 AI action 或參數也必須同步 schema 與 handler，不能只修改自然語言 prompt。

**新增外部行事曆**：
1. 在 Render.com 設定對應的環境變數（如 NOTION_TOKEN）
2. 在 Google Sheets「家庭成員」分頁填入對應欄位和權限設定
3. 若是新的行事曆來源（如 Google Calendar），需新增 API 封裝模組並更新 sync_external_events

**新增監控 PC**：
1. 在那台 PC 上 clone 本 repo
2. 依 `agent/README.md` 流程設置（裝 Python、LibreHardwareMonitor、agent_config.py 填本機 model + API key）
3. Task Scheduler 註冊 ButlerAgent 開機自啟
4. Dashboard `/devices` 頁的「電腦」區塊會自動出現新卡（按 IP 字串排序）

**費用控管**：Claude API 依使用量計費；LINE 推播、託管與其他服務依所選方案及額度。
在供應商控制台設定適合自己的預算與用量提醒；不以單一家庭過去的月費推估所有使用者。
AI 解析成本與效果的既有實測見 [評估紀錄](../evals/README.md)。

## v1.37.0 架構改善與維護邊界

- 待辦寫入：`todo_coordination.todo_write` 將即時讀取、ID 補齊、權限檢查、定位和寫入放在同一個 RLock。一般待辦、週期生成與 Notion 同步共用；Notion 網路查詢在鎖外，另有同步鎖避免舊結果覆蓋新結果。
- 私人待辦：Dashboard BFF 驗證 session，轉送 `X-Dashboard-User` 的 LINE ID；後端以啟用家庭成員精確匹配，不接受前端姓名前綴當權限。私人事項與週期規則在回應之前過濾，修改／完成也重驗。LINE 的 request context 同樣帶 actor。沒有此 header 的既有 API-key 系統呼叫仍有家庭級權限；API key 只能留在受信任伺服器／agent，不能交給瀏覽器。
- 穩定身分：Sheet 新增「待辦ID」欄，舊資料在首次讀取或寫入時補 UUID；Dashboard 修改／完成傳 `todo_id`，舊呼叫仍可用明確的名稱日期時間。匹配多筆一律拒絕，不能取第一筆。
- 天氣：`weather_budget` 的單次查詢預算 10 秒，HTTP timeout 使用剩餘時間；`weather_service` 最多 4 個工作、同日期地點共享進行中請求、滿載立即 503，整批最多等 12 秒後回 504。已開始的同步 HTTP 不能強制中止，但不會無限排隊；失敗不進成功快取。生活摘要 `include_weather=false` 完全不等天氣。
- 工作健康：帶 API key 的 `GET /api/system/jobs` 提供週期、執行中、開始／成功／下次時間、耗時及最近錯誤類別。這表示 callback 的完成情況，不是每個外部裝置已成功；子流程自行捕捉的錯誤仍需看服務 log。狀態在重啟後重建；業務去重仍在 Sheet。
- 部署先 home-butler 再 Dashboard。新版前端需要後端 ID／成員邊界。若要復原，先退 Dashboard，再退後端；新增 Sheet 欄位可以保留，不需刪資料。
- 執行 `python -m unittest discover -s tests -v` 與編譯檢查。測試使用假 Sheets／SDK，不向家電或 LINE 發送訊息。
- 部署仍限定單一 Python process／worker。RLock、工作排程與記憶體快取不是跨主機鎖；Sheets 也沒有多步交易，手動直接改表不受鎖保護。需要多 worker 或多實例時，必須先抽出唯一 scheduler／writer 並導入可交易的資料庫或分散式協調，不能只增加 worker 數。

## 文件入口

- [三個 repo 系統導覽](system-overview.md)：責任、資料流、部署／回復與後續 session 的閱讀順序。
- [驗證紀錄](verification.md)：離線測試、CI 與實際服務觀察的界線。
- [開發指引](../AGENTS.md) 與 [PC agent 維護](../agent/README.md)：程式維護與本機部署。

僅未遷移 HA 的空調回饋 v1.44.0：評估間隔最低 1 分鐘（上限 30）、最短調整間隔最低 1 分鐘（上限 60）。預設仍 5／10 分鐘，既有設定不變；回饋使用中的感測器約每分鐘取得最新讀值，歷史記錄仍約每 5 分鐘；同一顆感測器共用查詢，相同讀取樣本不重複調整。


### 照明光色 API（v1.54.0）

照明 state PATCH 可另帶互斥的 hs_color 或 color_temp_kelvin；需要 HA home_butler 1.5.0。
詳見 [光色控制契約](../homeassistant/sensors-and-hue.md#光色控制home_butler-150)。
