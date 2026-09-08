# 三個 repo 的系統導覽

本頁記錄目前的責任與接手入口；設備位址、配對資料與憑證以各部署端設定為準，不複製到公開文件。

| Repo | 責任 | 主要入口 |
| --- | --- | --- |
| [Dashboard](https://github.com/CZLin-TW/Dashboard) | UI、LINE 配對登入、JWT Session、可信使用者身分與 API 代理、瀏覽器快取、獨立 demo | [README](https://github.com/CZLin-TW/Dashboard/blob/main/README.md)、[AGENTS](https://github.com/CZLin-TW/Dashboard/blob/main/AGENTS.md)、[demo](https://github.com/CZLin-TW/Dashboard/blob/main/docs/demo-mode.md) |
| [home-butler](https://github.com/CZLin-TW/home-butler) | LINE／Siri 意圖與家庭 API、Sheets、私人待辦權限、設備排程與規則；`agent/` 提供 PC 指標、Hue 與劇院中繼 | [README](../Readme.md)、[AGENTS](../AGENTS.md)、[PC agent](../agent/README.md) |
| [theater-agent](https://github.com/CZLin-TW/theater-agent)（私人） | AVR／KEF 連動、KEF 事件、Bravia 畫面恢復、Apple TV 監控及本服務的健康更新流程 | [README](https://github.com/CZLin-TW/theater-agent/blob/main/README.md)、[AGENTS](https://github.com/CZLin-TW/theater-agent/blob/main/AGENTS.md)、[可靠性](https://github.com/CZLin-TW/theater-agent/blob/main/docs/reliability.md)；需要既有 repo 存取權 |

## 資料與控制路徑

```text
瀏覽器 → Dashboard（Session／API 邊界）→ home-butler → Sheets／設備雲端
LINE／Siri ─────────────────────────→ home-butler
Apple 家庭／Siri → 家中 Homebridge ──→ home-butler（獨立設備權限 API）
                                           │
                                      PC agent WebSocket
                                           ├→ Hue Bridge
                                           └→ 同機 theater-agent → AVR／KEF／Bravia
                                                   └ Apple TV monitor（獨立程序）
```

Dashboard 關閉不會停止後端排程或劇院連動。PC agent 的 heartbeat、WebSocket 在線、劇院 API 在線、Apple TV 心跳及實際設備狀態是不同層級；某層在線不表示整條鏈路已通過控制測試。

Siri 有兩條權限路徑：完整 `/api/assistant` 用 `HOME_BUTLER_API_KEY`，`user_id` 只決定對話身分；家電專用 `/api/assistant/devices` 用獨立 `DEVICE_VOICE_API_KEY`，由 `device_voice_api.py`／`device_voice.py` 限制可見目錄、動作、參數及設備名稱。家電入口不讀寫家庭對話、不接受身分覆寫，也不能用同一把金鑰呼叫其他 API；既有設備 handler 的防黴及自動關機仍運作。啟用及分享方式見 [README 家電專用捷徑](../Readme.md#device-only-voice)。

## 週期與事件的責任

Homebridge 插件位於 home-butler 的 `homebridge/`，與 Windows PC agent 分開部署，
不需要第四個 repo。以獨立橋接 Key 和設備名稱清單限制明確 AC 控制；每輪完成後約 5 秒
重新讀取後端快取，不呼叫 AI、不增加 Sheets 輪詢。冷氣狀態仍為最後指令；室溫來自同房間
感測器。冷／暖房使用空調控制，除濕／送風使用同配件內的模式開關；關閉模式開關會在後端
新讀資料後核對當前模式，已切換時不關機。權限、安裝、同步延遲及限制見 [橋接文件](../homebridge/README.md)。

| 工作 | 實作／頻率 | 限制 |
| --- | --- | --- |
| 家電排程 | `main.py` 註冊 `schedules`，每 60 秒 | 工作耗時、服務休眠及外部 I/O 仍影響延遲；送出前記錄執行識別碼，未知結果不自動重送 |
| 空調室溫補償 | `ac-temperature-feedback` 每 60 秒先更新回饋使用中感測器，再檢查；各設備預設 5 分鐘評估，最低 1 分鐘 | 預設關閉，僅已開機冷／暖房；不改舒適目標、不重設排程，未知結果暫停。[設定與 IR 限制](ac-temperature-feedback.md) |
| 感測器、照明、Notion、待辦、每日推播檢查、agent 健康 | 各自獨立工作，每 300 秒 | 工作不重疊，錯過週期跳過密集補跑；不是一條 realtime 工作依序包辦 |
| PC 指標 | `agent/agent.py` 每 tick 回報，預設 60 秒 | 心跳只代表 PC agent 的回報 |
| AVR／KEF | theater-agent 的 AVR push、KEF 長輪詢事件、每輪完成後等 15 秒補漏 | 事件需核對連動旗標及 AVR 狀態；不能保證 15 秒內完成硬體喚醒 |
| Apple TV／畫面 | Apple TV push 加主動查詢與畫面恢復狀態機 | 只恢復自己關閉的畫面；細節以私人 repo 為準 |
| Dashboard 刷新 | 各頁 hook／query store | 部分使用可見性控制，部分裝置頁歷史仍用定期 timer；不等同家電 push |

`GET /api/system/jobs`（既有 API key）可讀 home-butler 工作的開始／成功／下次時間及錯誤；這是 callback 健康，不是每個外部設備都成功。

## 部署、版本及回復

- Dashboard 的 `package.json:version` 是 Dashboard／LINE 顯示版本；home-butler 約每小時重新讀 `/api/version`。劇院兩程序另外顯示實際執行 SHA。純文件或註解不調整顯示版本。
- 有 API／資料契約相依時先部署 home-butler，再部署 Dashboard；回復先退 Dashboard，再退後端。新增 Sheet 欄位可保留，回復程式不代表回復業務資料。
- theater-agent 的可選 `health` 欄位保持向後相容；只有劇院內部變更時，不必為此修改其餘兩個 repo 的功能。
- home-butler 後端維持單程序／單 worker；目前的記憶體鎖與 Sheets 操作不能保障多實例交易。部署前檢查相依資料與設定，不能只增加 worker。
- PC agent 是 pull 後編譯並自我重啟。theater-agent 才有獨立候選驗證、兩程序健康守護及失敗回復；不要把後者的保護套用到所有服務。
- 一般程式回復以 `git revert` 建立新 commit，依正常部署流程發布；保留 runtime 設定、配對、旗標與未提交工作。Git push、CI 成功、磁碟 SHA 和程序正在執行的 SHA 分開核對。

## 新 session 的閱讀與驗證

1. 先讀目標 repo 的 AGENTS 與 README，再依本頁找到相依專案；不依賴舊聊天或仍在運行的本機服務。
2. UI 工作使用 Dashboard 的獨立 demo；後端使用 `tests/` 假外部 I/O。不要把 legacy 硬體診斷當作離線測試。
3. 修改資料契約同步 routes、型別、fixtures／simulator 與相關測試；修改行為同步 README 原段落、註解及驗證紀錄。
4. 在報告中分開寫靜態檢查、離線測試、瀏覽器 demo、部署觀察與實機功能驗證；只有實際做過的項目才能列為通過。

驗證入口：[home-butler](verification.md)、[Dashboard](https://github.com/CZLin-TW/Dashboard/blob/main/docs/verification.md)、[theater-agent](https://github.com/CZLin-TW/theater-agent/blob/main/docs/verification.md)（私人）。
