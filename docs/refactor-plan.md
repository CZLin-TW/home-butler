# HomeButler 與 Dashboard 重構計畫（草案，2026-10-20）

這份是重構的依據。決定的背景在對話裡：資料全面改成正規的 SQL，同時重新整理整體架構；
**以新架構最健康為目標，不為了能局部替換而遷就寫法**。新系統完整建好、資料一次搬移、整體切換，舊系統留作退路。

公開 repo：本文不寫家裡的位址、網址、帳號或真實區域名稱。

---

## 1. 原則

1. **HA 是唯一的設備層。** 本地或雲端的設備都只在 HA；HA 的整合就是「接只有雲端 API 的東西」的節點。
2. **HomeButler（HB）只當大腦。** 家庭資料、排程、AI 管家、通知、Dashboard 的後端，以及 Notion、天氣、Claude 這類資料服務。
   HB 永遠只透過 HA 看與控制設備，自己不直接連任何設備。
3. **既有的分工原則不變。** 即時反應歸 HA；家人要看、要改的排程歸 HB；每台設備、每條規則同時只有一個控制主體。
4. **每張資料表都有戶別。** 戶別以 HA 的「樓層」為準，其他地方讀它。
5. **每個模組換得掉。** 資料庫只用 SQLite 與 PostgreSQL 都支援的寫法；設定值有一份清單；HB 放 Render、Mac mini 或別台只是設定選擇。
6. **最小權限。** 拿掉萬用金鑰；每個呼叫者有自己的、可個別停用的金鑰。

## 2. 分層與資料流

```
家人 ── Dashboard（Vercel）──┐
家人 ── LINE ────────────────┤
                             ├──> HomeButler（Render，大腦）──> Supabase（PostgreSQL）
                             │        │  ▲
Notion、天氣、Claude <────────┘        │  │ HA 主動連進來的那條線（WebSocket）
除濕機（Panasonic／LG 雲端）<── HB 的隔離模組（§8，之後移除）
                                      ▼  │
                       Home Assistant（家裡，唯一的設備層）
                         ├─ SwitchBot 雲端：空調、IR 電扇、Hub 2
                         ├─ Hue、FP2、Floor Presence（MQTT）
                         └─ 劇院服務（經 HA 的 Theater Agent 整合）
家人 ── Apple 家庭 ──> HA 的 HomeKit 橋接器
```

- HB 不再有設備的驅動程式：SwitchBot 直連、PC 代理程式的 Hue 備援都拿掉。
- 例外一：**除濕機**留在 HB，做成隔離模組（§8）。除濕機之後會淘汰，不值得搬進 HA；隔離是為了到時候能乾淨刪除。
- 例外二：**SwitchBot 推送轉交**：SwitchBot 雲端的 webhook 只能打到公開網址，HA 沒有，所以暫時由 HB 收、只轉「去重讀」的提示給 HA。
  這做成一個只做這件事的隔離模組（§4），**待辦：HA 有對外網址後改成直接通知 HA，刪掉這個模組。**

## 3. 資料模型

欄位名用英文、型別明確、用 `id` 操作、封存改成狀態欄位。結構變更用 **Alembic** 管版本，部署時自動套用。
時間一律存 UTC（`timestamptz`），顯示時轉台北時間。

### 3.1 家庭與身分

| 表 | 主要欄位 | 說明 |
|---|---|---|
| `households` | id, name | A 戶、B 戶。對應 HA 的樓層。 |
| `members` | id, name, title, line_user_id, default_household_id, status, butler_style, alert_opt_in, notion_database_id, notion_filter, notion_permission | 取代「家庭成員」。`butler_style`（管家風格）由成員透過 LINE 設定，存在資料庫，**不再從試算表同步**（它本來就是程式寫入的欄位，和「Hue 照明區域」同理）；搬移時從試算表複製一次。 |
| `api_tokens` | id, kind, member_id?, label, hash, scopes, created_at, last_used_at, revoked_at | 每個呼叫者一把：HA 整合、Mac mini 監控、Windows 監控、Dashboard（之後若加新的管家入口，也在這裡發）。只存雜湊。 |
| `remotes` | id, household_id, name, credential_hash, enabled | 取代「遙控器」。 |
| `pairing_codes` | id, member_id, code_hash, expires_at, used_at | 取代「裝置配對」（LINE 六位數登入碼）。 |

### 3.2 設備（HA 為準）

| 表 | 主要欄位 | 說明 |
|---|---|---|
| `devices` | id, ha_entity_id, name, kind, area, household_id, enabled | HA 實體的鏡像，加上 HB 需要的少量屬性。由 HA 整合推送更新。 |
| `device_settings` | device_id, auto_off_hours, auto_off_hold, temperature_offset, humidity_offset, ir_buttons | 你手動維護的設定（取代「智能居家」的設定欄）。 |
| `device_state` | device_id, power, mode, temperature, fan, updated_at, last_on_at | 程式寫的目前狀態（取代「智能居家」的「最後…」欄）。 |
| `light_areas` | id, household_id, name, ha_target | 取代「Hue 照明區域」。 |

Panasonic 除濕機的登入權杖目前直接放在試算表「智能居家」的一欄；新系統改放 Render 的環境變數，不再從試算表讀。

### 3.3 家庭應用

| 表 | 主要欄位 | 說明 |
|---|---|---|
| `todos` | id, household_id, title, due_date, due_time?, assignee_id?, visibility, status, source, read_only, external_id?, light_reminder, light_area_ids, template_id?, created_by, created_at, completed_at?, archived_at? | 取代「待辦事項」與「待辦封存」。封存＝`archived_at` 有值。 |
| `todo_templates` | id, household_id, title, rule（重複規則）, assignee_id?, light_reminder, light_area_ids, active, next_due | 取代「週期待辦模板」。 |
| `food_items` | id, household_id, name, quantity, unit, expires_on?, added_on, added_by, status, consumed_at? | 取代「食品庫存」與「食品封存」。 |
| `schedules` | id, household_id, target_kind, target_name, params, due_at, source, cycle_id, status, attempt_id, result, created_by, finished_at, dismissed_at | 取代「排程指令」與「排程封存」（已寫好，第 5 階段）。source：user／auto_off。status：pending → claimed（送出前先記）→ done／failed／unknown，或 expired、cancelled；失敗與未確認留著等人移除，不重送。不再有封存分頁：看不到的就是已結束的。 |
| `auto_off_cycles` | id, device_id, started_at, hours, closed_at | 空調自動關機的「本輪」（從看到開機到看到關機）。資料庫保證同一台空調同時只有一輪，取代舊系統塞在參數裡的 `_auto_*` 標記；被刪掉的本輪關機因此不會被重建。 |
| `conversation_messages` | id, member_id, role, content, created_at | 取代「對話暫存」與「對話封存」（已寫好，第 6 階段）。管家讀最近 6 則，保留 90 天。 |
| `system_state` | key, value, updated_at | 延用。 |

### 3.4 歷史（改成正式欄位）

| 表 | 欄位 |
|---|---|
| `sensor_readings` | device_id, t, temperature, humidity, co2, light_level |
| `ac_history` | device_id, t, power, mode, temperature, fan |
| `dehumidifier_history` | name, t, power, mode（除濕機模組自己的表，見 §8） |
| `computer_metrics` | host, t, cpu_pct, ram_pct, gpu_pct, cpu_temp, gpu_temp, soc_temp, mem_pressure_pct, mem_pressure_level, power_w |

保留 400 天，舊資料自動刪除。第一段已存進資料庫的 JSON 歷史在搬移時轉成這些欄位。

### 3.5 手動設定的編輯介面

短期仍在試算表編輯「家庭成員、智能居家的設定、遙控器」，但**程式只讀資料庫**：
HB 每分鐘問 Google「試算表有沒有被改過」（很輕的查詢），有改才把這三張讀進 `members`、`device_settings`、`remotes`。
「Hue 照明區域」不留在試算表（2026-10-21 使用者同意）：那張表其實會被程式自動寫入（HA 發現新區域時），所以改成資料庫的 `light_areas`，由 HA 自動發現；使用者改過的顯示名稱在切換時搬過來，之後在 Dashboard 改。
以後做成 Dashboard 管理頁時，只是把編輯介面從試算表換成網頁，讀取的程式完全不用改。
搬完後試算表只剩這三張，其餘分頁留兩週再刪。

## 4. HomeButler 的程式結構

改成一個套件，依責任分層。**只有 `store` 碰資料庫，只有 `integrations` 碰外部服務。**

```
homebutler/
  app.py              FastAPI 組裝、啟動與關閉
  settings.py         所有環境設定（有型別、啟動時檢查）
  store/              資料表定義、存取函式、Alembic 版本
  domain/
    households/  members/  todos/  food/  schedules/  devices/  notifications/
  assistant/          管家的自然語言處理（Claude）；目前只有 LINE 一個入口，處理流程和入口分開，以後加入口不影響它
  integrations/
    ha/               HA 連線（HA 主動連進來的 WebSocket）、設備指令與讀取
    line/  claude/  notion/  weather/
    switchbot_push/   只收 SwitchBot webhook、轉提示給 HA（待辦：之後刪除）
  dehumidifier/       除濕機的隔離模組：Panasonic／LG 連線、依濕度規則、自己的資料表與路由（之後整個刪除）
    sheets_config/    四張手動設定表 → 資料庫
  api/                每個用途一組路由：dashboard、assistant、integrations、admin
  jobs/               背景工作（排程派送、提醒、每日推播、Notion 同步、告警、備份、設定同步）
```

- 現在約 70 個檔案扁平放置、`web_api.py` 一個檔案 42 個端點、待辦的邏輯散在 19 個檔案；新結構裡每個領域的規則只在自己的 `domain/` 一處。
- 已完成遷移的開關（例如 `HOME_ASSISTANT_AC_NAMES`、`HOME_ASSISTANT_IR_NAMES`、`HOME_ASSISTANT_SENSOR_NAMES`、`HOME_ASSISTANT_HUE_ENABLED`、`THEATER_VIA_HA`、`RECURRING_TODO_ENABLED`）與舊路徑全部拿掉，只剩真的需要的設定。
- 背景工作仍在同一個程序裡跑（單一實例）；每項工作有自己的間隔與錯誤隔離，一項失敗不影響其他項。

## 5. API 與身分

新 API 一律在 `/v2/`，用 ID 操作，回應格式一致（錯誤有代碼與可顯示的訊息）。

| 呼叫者 | 怎麼證明身分 | 能做什麼 |
|---|---|---|
| Dashboard | Dashboard 自己的金鑰，加上每個請求附帶一張**由 Dashboard 簽發、幾分鐘就過期的使用者憑證**（取代現在「相信 Dashboard 送來的使用者標頭」） | 該使用者能做的事 |
| LINE | LINE 的簽章（不變） | 管家 |
| HA 的 HomeButler 整合 | HA 專用金鑰 | 回報設備與狀態、接收指令 |
| Mac mini、Windows 監控 | 各自一把 | 只能回報指標 |

- 萬用金鑰拿掉，切換時換掉舊的那把。
- **Siri 的管家入口（`/api/assistant`）不帶進新系統**（使用者 2026-10-20：沒在使用；Siri 控制家電已在 Apple 家庭裡完成）。以後需要時再加一個入口。

## 6. Dashboard 健檢與重構

**發現：**

1. **伺服器位置繞遠路。** `vercel.json` 沒有指定區域，Vercel 的伺服器端程式預設在美國東岸，HB 和資料庫在美國西岸（Oregon）。每個請求都要多橫跨一次美國。
2. **一頁發出很多請求。** 裝置頁開啟時分別要設備、感測器、空調、除濕機規則、除濕機歷史、電腦、排程、天氣、HA、劇院……，其中五項每 60 秒各自重抓。每一個都經 Vercel 轉到 HB，而 HB 每次又去讀試算表——這正是快速切頁時卡住或失敗的原因（試算表每分鐘約 60 次的讀取上限）。
3. **待辦刻意不存在手機上**（私人資料），每次進入待辦頁都從空白開始等。
4. 程式本身（約 10,000 行）結構尚可：有共用的查詢快取、測試與展示模式。

**重構：**

- Vercel 的伺服器端程式改到美國西岸，和 HB 同區。
- **每頁一個彙整請求**：例如首頁、裝置頁、待辦頁各有一個 `/v2/...` 端點，一次拿齊那頁需要的東西；之後再用小請求更新變動的部分。
- 資料來源改成資料庫後，HB 的回應從秒級降到幾十毫秒，HB 也不再需要為了保護試算表而排隊。
- **狀態要貼近現況**（使用者 2026-10-21）：HB 大約 1 秒內就知道設備的變化（HA 一變就送、每 30 秒心跳），落後主要在 Dashboard。裝置頁開著時改成約 5～10 秒更新一次（只更新會變的狀態）；每張卡顯示「幾秒前更新」；顯示的是手機上的快取時明確標出。HB 與 HA 斷線超過 90 秒標成未知的做法不變。
- 待辦仍不存在手機的儲存空間，但同一次使用期間在記憶體裡保留，切回待辦頁先顯示上次的內容再更新。
- 資料存取層照新的 API 重寫；畫面與元件大致沿用。
- 之後再考慮「快速頁」（只有待辦／庫存、可加入主畫面）；先看上面這些做完後的速度。

## 7. 拿掉的東西

- homebridge 的 API 與 `homebridge/` 插件（Apple 家庭已改走 HA）。
- Siri 的兩個入口：管家（`/api/assistant`）與「設備語音」（Siri 已在 Apple 家庭裡控制設備，管家捷徑沒在使用）。
- 直連 SwitchBot 的程式（除濕機的直連移進隔離模組，見 §8）。
- 已完成遷移的開關與舊排程來源。
- Windows 的 PC 監控與劇院的 PC 中繼：**等那台電腦淘汰時**再拿掉，在那之前新系統照常支援。

## 8. 除濕機：隔離模組（使用者 2026-10-20 決定）

除濕機之後會淘汰（新家改中央除濕），所以**不搬進 HA**，在 HB 裡做成可以整個拿掉的模組：

- `homebutler/dehumidifier/` 放 Panasonic／LG 的連線、依濕度的自動規則（含分時目標），以及它自己的資料表（`dehumidifiers`：一台一列，存規則、規則進度與最後讀到的狀態；`dehumidifier_history`）與路由（`/v2/dehumidifiers`）。已寫好（第 4 階段）。
- 規則進度（等待計時、感測器失聯次數、手動介入偵測的基準）存在資料庫，**重啟不再歸零**（舊系統放記憶體，重啟後從頭等）。每 5 分鐘讀一次所有除濕機的狀態（舊系統只讀開了自動模式的），所以手動模式的除濕機也有歷史與「幾分鐘前更新」。
- 除濕機清單來自「智能居家」分頁（類型除濕機、啟用）；房間濕度來自 HA（套用感測器的濕度補償），HA 斷線就算感測器沒回報。
- 核心（待辦、排程、管家、通知）不直接 import 這個模組；`homebutler/modules.py` 列出可選模組，啟動時呼叫各自的 `install`，模組把自己放進 `app.state.modules`。之後排程、管家、Dashboard 彙整要用除濕機時都經由這裡。沒有這個模組時，系統其他部分照常運作。
- Dashboard 的除濕機卡片只在後端回報有這項能力時出現。
- 移除時：刪掉模組資料夾與 `modules.py` 那一行、用一個 Alembic 版本刪掉它的兩張表、拿掉 Dashboard 的卡片程式；不會在別處留下殘骸。
- 照舊的規則：不 fallback、未知結果不重送、Panasonic 只允許明確 token 417 的認證重試。
- 憑證改放 Render 環境變數，不放試算表：`PANASONIC_ACCOUNT`、`PANASONIC_PASSWORD`、`PANASONIC_DEVICE_AUTH`（JSON，Device ID 對應該台的 Auth 值；切換時由使用者從試算表貼過去，Claude 不經手）、`LG_PAT` 等。
- 和舊系統的小差異（都是更保守）：開機成功才接著設模式（舊的開機失敗仍會送模式）；切換自動模式的當下讀不到電源狀態就不做立即判斷（舊的會當成關著而開機）。

## 9. 切換步驟

1. **凍結舊系統的新功能**（只修錯）。
2. **在分支上建新系統**：測試用 SQLite，CI 另用 PostgreSQL 跑同一套；沿用現有測試裡有價值的情境，管家的評估題（`evals/`）照跑。
3. **搬移腳本**：試算表與第一段的歷史 → 新資料表。可以重跑，跑完自動比對筆數與內容。
4. **試跑環境放在 Mac mini**（不另開 Render：免費方案所有服務合計每月 750 小時，正式服務保持清醒就用掉約 720 小時）：新 HB 用本機 SQLite 與資料副本，Dashboard 用開發模式接它。試跑環境**不派送排程、不發 LINE、不控制真實設備**（設備用假的 HA）。管家用一個只在試跑環境開放的文字入口與 `evals/` 驗證；LINE 只是入口，切換當晚用實際訊息確認，不另開測試頻道。
5. **切換（約十幾分鐘，選晚上）**：停掉舊系統的背景工作 → 最後一次搬移 → 正式的 Render 服務換成新程式、換掉萬用金鑰 → Dashboard 換新版 → 更新 HA 的 HomeButler 整合與 Mac mini 監控 → 確認排程、LINE、Dashboard、Apple 家庭都正常。
6. **之後兩週**：試算表的舊分頁保留，舊程式隨時可以部署回去。
7. **收尾**：刪除試算表的舊分頁、刪掉舊程式。

**退路**：舊程式保留標籤，可以隨時部署回去；切換後新增的資料用一個腳本寫回試算表。

## 10. 工作順序

| 階段 | 內容 |
|---|---|
| 1 | 地基：套件結構、設定、資料表與 Alembic、戶別與成員、金鑰、手動設定表的同步 |
| 2 | 待辦：含週期待辦、Notion 同步、到期提醒（LINE 與燈光） |
| 3 | 食品 |
| 4 | 設備：經 HA 的狀態、控制、歷史、劇院、SwitchBot 推送轉交、電腦監控；除濕機隔離模組 |
| 5 | 排程：含空調自動關機、派送（排程要用到設備，所以排在設備之後） |
| 6 | 管家：LINE、每日推播、失聯告警、每日備份 |
| 7 | Dashboard：新 API、彙整請求、區域 |
| 8 | HA 的 HomeButler 整合、Mac mini 監控程式改接新 API |
| 9 | 搬移腳本、試跑、切換、收尾 |

每個階段做完都跑完整測試、在本機與試跑環境驗證；推送與部署照舊先問。

## 11. 帶進新系統的硬規則（來自 `AGENTS.md`）

照舊成立，新程式要有對應的測試：

- **未知結果不自動重送**：送出後沒拿到明確結果就維持待確認，重啟後也不補送。
- **不 fallback 到另一條路徑**：HA 失聯、設定錯誤、provider 不一致時一律拒絕或取消；感測器失聯就是未知。
- **無簽章的內容不能寫進 HA 狀態**（SwitchBot 推送只當「去重讀」的提示）。
- **1–20 光照等級不是 lux**。
- **空調目標是整數 16–30°C**；色彩／色溫不附帶開機，整批預驗證後才寫入。
- **禁止用家裡的實體家電做測試**。
- **單一程序、單一 worker**：新系統的資料庫有交易，但背景排程仍只能有一個執行者；要多實例前先設計唯一的排程者。
- **排程派送前重驗來源與 provider**，不一致直接取消。
- **LINE 只接受啟用成員**，群組不以群組 ID 代替成員。
- **告警只在狀態翻轉時推播**（LINE 免費方案每月 200 則），marker 寫在推播成功之後。
- **Notion 完成記號以 page id 為主鍵**，Notion 查詢失敗的成員整輪不動。
- **寫入型動作回報真實結果**（`TRUTHFUL_ACTIONS` 的精神）：管家不能回「已完成」卻什麼都沒做。
- **系統版本以 Dashboard 的 `package.json` 為準**。

因這次的決定而改變的：

- 除濕機仍留在 HB，但從散落各處改成隔離模組（§8）。
- 四把金鑰的權限邊界改成 §5 的每個呼叫者一把；家電專用語音與 homebridge 的金鑰隨功能拿掉。
- 「不新增 Sheet 欄位」不再適用：資料不在 Sheet 了；四張手動設定表的欄位由新系統定義。

## 12. 待辦與風險

- **SwitchBot 推送改成直接通知 HA**（§2）。
- **管家的行為**：改寫管家的處理時，用 `evals/` 的題目確認回答與動作和現在一致。
- **排程不能重複執行**：試跑環境絕不派送；切換時先停舊的再開新的。
- **時間**：整體是幾週的工作；切換前舊系統照常可用。

## 13. 使用者的決定（2026-10-20）

1. 同意這份計畫。
2. 除濕機不進 HA，改成 HB 裡的隔離模組（取消原本的「第 0 步」與 HACS 安裝）。
3. 試跑環境放在 Mac mini，不另開 Render 服務。
4. LINE 不另開測試頻道，切換時驗證。
5. Siri 的管家入口不帶進新系統（沒在使用）。
