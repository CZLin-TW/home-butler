# HomeButler Homebridge 插件

Apple「家庭」/ Siri → 家中 Homebridge → Render HomeButler → SwitchBot。
此目錄是 Node.js 插件，與 Windows Python PC agent 分開執行；只安裝在 Homebridge 主機。
支援允許清單中的紅外線空調：開關、冷氣／暖氣與 16–30°C 整數目標溫度，
除濕／送風則使用同一配件內的模式開關。實際可執行的功能仍以空調機型為準。
不呼叫 Claude，不提供待辦、食品、身分或任意 action 入口。

## 部署順序

1. 先部署包含新版 `homebridge_api.py` 的 home-butler，再更新插件；1.1.0 的模式關閉需要
   後端支援 `off_if_mode`。舊後端會拒絕這項新增欄位，不會退回無條件關機。
2. 在 Render 設定以下環境變數，儲存並等候重新部署：

| 變數 | 設定 |
| --- | --- |
| `HOMEBRIDGE_API_KEY` | 新產生的獨立隨機 Key，至少 32 字元；不得與 `HOME_BUTLER_API_KEY` 或 `DEVICE_VOICE_API_KEY` 相同 |
| `HOMEBRIDGE_DEVICE_NAMES` | JSON 名稱陣列，例如 `["客廳冷氣"]`；名稱必須與「智能居家」完全一致 |

未設定 Key／名稱清單時拒絕使用，不會開放原有 owner API。Key 不要提交 Git、不貼聊天，
僅填入 Render 環境變數與 Homebridge 插件設定。Homebridge 設定備份包含此 Key，請保管備份。
可在 Homebridge 網頁終端機用 `node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"`
產生；此操作只在你自己的終端顯示 Key。

3. 在 Homebridge 管理網頁的終端機執行下列指令（Linux 虛擬機內，不是 Windows PowerShell）。
首次安裝使用一個尚未存在的目錄：

```sh
git clone https://github.com/CZLin-TW/home-butler.git ~/home-butler-bridge
cd ~/home-butler-bridge/homebridge
npm pack --ignore-scripts
npm --prefix /var/lib/homebridge install --save --omit=dev --ignore-scripts "$(pwd)/homebridge-home-butler-1.1.0.tgz"
```

官方 VM 的插件目錄是 `/var/lib/homebridge/node_modules`，以上指令明確安裝到該位置。
用打包檔安装，避免只建立指向 checkout 的 symlink。插件沒有執行時 npm 依賴，
`homebridge` 的 devDependency 僅供離線測試。**不需 npm publish，也不需安裝 SwitchBot 插件**。
Homebridge 2.4.0 / Node 24 已做程式庫層測試；其他版本尚未實機驗證。

4. 重啟 Homebridge，在「插件」找到 `homebridge-home-butler` 並打開設定。
填入 **Render 後端的 HTTPS 網址**（不是 Dashboard，也不是家中的 Homebridge 管理網址），
以及相同的 `HOMEBRIDGE_API_KEY`。「插件名稱」可維持 HomeButler，冷氣名稱來自後端清單。
同位置只有一個感測器時可直接儲存並重啟，不必新增室溫來源。
需要指定時，在「室溫來源」按新增項目，再填冷氣及感測器的完整 Sheet 名稱。
例如冷氣 `客廳空調`、感測器 `SwitchBot Hub 2 客廳`；表單欄位填純文字，不加 JSON 方括號。
畫面高度不足時先點「連線設定」收合，再操作室溫來源。

插件包含可新增陣列欄位與可收合區塊。更新前先保存已填的連線資料，
等 Render 新版部署完成，再於 Homebridge 網頁終端機執行（原有 Key／設定會保留）：

```sh
cd ~/home-butler-bridge
git pull --ff-only
cd homebridge
npm pack --ignore-scripts
npm --prefix /var/lib/homebridge install --save --omit=dev --ignore-scripts "$(pwd)/homebridge-home-butler-1.1.0.tgz"
```

重啟 Homebridge 並重新整理管理網頁後，再開插件設定。不需要重新配對主橋接器。
既有允許清單及感測器對應不需修改。1.1.0 沿用空調配件 UUID，保留房間分配，
同一配件增加「空調名稱＋除濕」及「空調名稱＋送風」兩個開關；Apple Home 可能將控制項
放在同一配件頁內，實際分組／顯示需在 iPhone 確認。Sheet 位置不會自動建立 Apple Home 房間。

若使用 JSON 設定，在原 `platforms` 陣列新增一項，保留原有 `bridge`、`accessories`、UI 設定：

```json
{
  "platform": "HomeButler",
  "name": "HomeButler",
  "backendUrl": "https://YOUR-BACKEND.onrender.com",
  "apiKey": "YOUR-DEDICATED-HOMEBRIDGE-KEY",
  "pollSeconds": 5,
  "devices": [
    {"name": "客廳冷氣", "temperatureSensor": "客廳感測器的實際名稱"}
  ]
}
```

同一「位置」只有一個感測器時可省略 `devices`；有多個時需明確指定。
冷氣和感測器的 Sheet「位置」需一致。讀值取後端現有感測器快取，超過 15 分鐘／離線／
缺值會讓室溫特徵回傳無回應，不拿目標溫度假裝室溫。沒有感測器時 Apple Home 的呈現仍需
實機評估，可能顯示配件無回應；這種家庭應先用開關型配件方案，不能宣稱完整冷氣介面可用。

插件預設掛在已配對的主橋接器，成功發現冷氣後應自動出現於 Apple「家庭」。若自行啟用
Homebridge child bridge，該 child bridge 必須另行配對。Homebridge 主機必須持續開機；
Windows Hyper-V 設定自動啟動／正常關閉客體，Windows 不睡眠。建議路由器 DHCP 保留虛擬機 IP。

## 狀態與控制語意

- 後端只暴露允許名稱、狀態啟用、類型空調且名稱／Device ID 唯一的設備。ID 以雜湊穩定衍生，
  不以清單順序／房間名稱當配件 UUID；更換 Device ID 是另一個配件。
- GET `/api/homebridge/devices` 只讀背景既有快取，不觸發 Sheets、SwitchBot 或歷史讀取。
  後端冷啟動目錄未備妥回 503，插件稍後重讀。手動 Sheet 設定變更等待既有背景刷新。
- LINE／Dashboard／捷徑／排程透過原 handler 成功保存 AC 狀態，插件下一次輪詢同步。
  預設每次完成後等 5 秒，另加 API 時間；這不是後端 push，感測器也維持原有採樣週期。
- POST `/api/homebridge/devices/{id}/ac` 在下指令前重新讀取設備／排程與驗證允許清單。
  溫度等局部調整沿用最新的模式與風速；關機時不能只調溫度暗中開機。先在家庭 App 開啟，
  或明確選擇模式（mode + power=on）。從未有完整紀錄時，先在 Dashboard 設定完整狀態。
- 空調選單提供冷房與暖房，兩者溫度特徵對應同一個後端目標溫度，改溫度保留當前模式／風速。
  冷房 Current=cooling、暖房 Current=heating 是最後指令模式的推定，**沒有壓縮機運轉回報**。
  自動模式不在本版四種模式範圍，暫標示無回應；仍可明確改回四種模式或關閉。
  風速與特殊規則繼續在 Dashboard 操作。
- 打開除濕／送風開關會切換模式並開機；後端回覆成功後，另一模式開關自動同步關閉。
  關閉模式開關時，後端以當次新讀的 Sheet 確認仍在該模式才呼叫原關機 handler；
  已關機／已切到其他模式則回最新狀態、不發硬體指令。未知狀態回 409，不猜測。
  HAP 沒有 dry／fan 空調選單值，運行時主空調保留最近的冷／暖選擇並顯示 idle；
  **模式開關才表示除濕／送風是否啟用**，不能把主選單保留的冷／暖值當成目前模式。
- 防黴觸發時仍顯示通電（Active=on）及 idle，送風開關亮起；收尾排程成功後兩開關同步為 off。
  HomeKit 寫入完成後會再次套用後端結果，避免 optimistic OFF 蓋掉送風狀態。更新特徵不呼叫 SET。
- 同一 HomeKit 手勢的欄位以 300ms 合併；主電源關機優先，溫度等附帶欄位不混入關機。
  切換模式優先於同手勢關閉舊模式開關；同時關閉除濕與送風則合併條件並核對一次。
  同配件命令依序處理，輪詢快照不覆蓋期間發生的新命令。關機途中再次明確按關仍遵循原 handler。
- API 逾時／未知結果／保存失敗不自動重送，配件標無回應；請先查 Dashboard、Render log 與
  實體設備再決定是否重新操作。先前已排隊但尚未送出的手勢也會取消。HTTP timeout 不會撤銷
  已送到後端的請求。重新讀取狀態成功不等於實體 IR 已確認。
- `request_id`（UUID）於單程序記憶體去重 10 分鐘，最多 256 筆，ID 重用不同參數回 409；
  重啟／超時／逐出後不保證去重，插件本身不自動重試 POST。橋接寫入鎖只序列化橋接请求，
  不提供與既有 LINE／Dashboard／排程／外部 Sheets 編輯的跨入口交易保證。仍只部署一個 worker。
- 後端中斷或允許清單撤除時保留配件但標無回應，避免誤刪房間／自動化設定。
  需要永久移除時再從 Homebridge 管理 cached accessories。憑證不寫 log；API錯誤只回通用訊息。

## API 契約

全部使用獨立 `X-API-Key`；不得把 bridge Key 加進 owner verifier。
GET 回 `{protocol:1, devices:[{id,name,location,power,temperature,mode,fan_speed,updated_at,uncertain,state_source}], sensors:[{name,location,temperature,online,updated_at}]}`。
POST body 是 `{request_id, power?, temperature?, mode?, fan_speed?, off_if_mode?}`，禁止其他欄位／null。
`off_if_mode` 為一至兩項的 dry／fan 清單，只能與 `power:"off"` 搭配；不匹配時不發指令，
回 success 與當次讀取的已保存狀態，且同 request_id 重送仍回相同結果。
回 `{status:"success"|"failed"|"unknown", message, device}`。只有 success 帶已保存 device。
HTTP 200 不等於 command success；插件必須檢查 status。
`state_source=last_command` 是後端記錄，實體紅外線遙控器操作仍無法被偵測。

## 驗證與更新

```sh
cd homebridge
npm ci --ignore-scripts
npm test
```

測試使用真正 Homebridge PlatformAccessory／HAP 服務與特徵，以及假的後端／傳輸。
不 publish 配件、不開 mDNS、不操作家電；後端測試用假 Sheets／SDK。通過不代表 iPhone／Siri、
Render 環境變數或實體冷氣已驗證。

實機驗收依序：空調出現 → 冷氣／暖氣與調溫 → 除濕／送風開關互切 → Dashboard 四模式變更後 Home
同步 → Home 操作後 Dashboard 同步 → 已切暖氣時關掉除濕開關不關機 → 防黴送風與收尾 →
重開 PC 自動恢復。確認既有房間設定與新增開關名稱；這些操作會控制真實設備，需由家庭使用者執行。

後續更新在 clone 目錄 `git pull --ff-only`，重新 `npm pack`、安裝新版本 tgz 並重啟 Homebridge。
插件不自行更新。回復先停用插件或安裝已保留的舊 tgz，再依需要 git revert 後端；保留 HomeKit
配對／設定，不必刪除 bridge。移除 Render 的 `HOMEBRIDGE_API_KEY` 可立即在下次部署停用入口。

來源：[Homebridge VM](https://github.com/homebridge/homebridge/wiki/Install-Homebridge-on-Virtual-Machine)、
[動態平台範本](https://github.com/homebridge/homebridge-plugin-template)、
[HAP 服務](https://developers.homebridge.io/HAP-NodeJS/classes/Service.html)。
