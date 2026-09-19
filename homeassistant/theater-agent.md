# HA 本地劇院功能開關

Theater Agent 1.0.0 是獨立 HA 整合；Home Butler 1.7.0 可選擇透過它控制劇院。
2026-09-19 已於家庭 HA Core 2026.9.2 安裝並完成唯讀路徑核對，範圍見 [驗證紀錄](../docs/verification.md)。

## 分工與狀態

```text
HA 三個 switch ──────────────┐
Dashboard → HB → HA Home Butler ─┤
                              ▼
                    HA Theater Agent 控制器
                              │ GET /summary、POST /flags
                              ▼
                    原 theater-agent → 設備連動
```

| 旗標 | HA 顯示用途 |
| --- | --- |
| `kef_link` | KEF 電源連動 |
| `tv_screen_auto` | 電視畫面自動關閉（Apple TV 播音樂時） |
| `tv_avr_sync` | AVR 自動隨電視開啟（電視從待機開啟時） |

這些開關表示「啟用該連動規則」，不表示電視／喇叭現在的電源，也不是啟停 Agent 程序。
HA 不保存另一份期望旗標，不從 HA restore state 覆寫 Agent。啟動、設定與重載只讀取。
Agent 的旗標檔案仍是持久化來源，原 Windows 服務與 theater-agent 程式碼不需變更。

HA 每 15 秒讀取 summary；切換只送使用者指定的旗標，寫入後再 GET 確認才更新狀態。
HB summary 亦透過相同控制器即時讀取。共享互斥鎖避免輪詢與寫入交錯；忙碌時寫入直接拒絕，
不排隊、不重送。寫入逾時或確認失敗顯示 unavailable／未知，後續輪詢只讀取、不補寫。
若讀回不同值，顯示實際讀值並回報未知，不用反向操作假裝復原。
成功只表示設定已讀回，不能推定設備已完成連動。

## 部署順序

1. 先備份 HA，記錄目前 `THEATER_VIA_HA`、舊中繼設定與三個旗標值。不要把金鑰寫進 repo。
2. 使用通過 CI 的完整 commit SHA，將同一版的 `theater_agent` 與 `home_butler`
   資料夾安裝到 `/config/custom_components/`。可用 repo 的安裝腳本：

   ```sh
   # 在 HA Terminal & SSH app 執行；SHA 替換成已核對的完整提交碼
   revision=完整40字元提交碼
   curl --fail --silent --show-error --location \
     "https://raw.githubusercontent.com/CZLin-TW/home-butler/$revision/homeassistant/install.sh" \
     -o /tmp/install-home-butler.sh
   sh /tmp/install-home-butler.sh "$revision" theater_agent
   sh /tmp/install-home-butler.sh "$revision" home_butler
   ```

   腳本會保留舊資料夾備份；兩個組件裝好後再重啟一次 HA。
3. HA「設定 → 裝置與服務 → 新增整合 → Theater Agent」，輸入
   `http://192.168.68.55:8080` 與既有 `THEATER_AGENT_KEY`。
   虛擬機內的 `127.0.0.1` 是虛擬機自己，不能拿來連 Windows／Mac 主機。
4. 確認出現三個 switch，值與原設定一致。此步只讀取，不測試真實設備切換。
5. Home Butler「設定」選取 **本地 Theater Agent 整合**。
   儲存後舊直連網址／金鑰會清除，HB 只使用此整合；重新載入後宣告原有 `theater_relay` 能力。
6. 確認 HB 的 HA 連線在線、`theater_available=true`，再切換 Render 的 `THEATER_VIA_HA=true`。
   若原本已為 true，不需改動。能力旗標只表示中繼已宣告，仍需讀取劇院 summary 驗證實際可用。
7. Dashboard 唯讀確認三個旗標與 HA 相符、`agent_id=home_assistant`、劇院區塊可見。
   正式功能切換與 Render 斷線時 HA 控制驗收，安排使用者方便觀察設備的時段。

本功能上線時依專案版本規則更新 Dashboard 顯示版本；部署前分支不先宣告正式版本已上線。
無需增加 HA 自動化、Sheet 欄位、Render 金鑰或 Dashboard API。

## 搬到 Mac mini

先搬 theater-agent 及原旗標檔案，再於本地 Theater Agent 的「設定」更新位址／金鑰。
不要刪除後重新加入：保留 config entry 才會保留三個 switch 的 unique ID、使用者命名與引用。
HB 記住的是此 entry 的 ID，不是舊 IP，也不需要另外改一次 HB 劇院位址。
HA VM 必須能存取主機 API；使用橋接網路或可達主機的位址與防火牆設定。
本次不處理 macOS launchd、Windows 排程器、Apple TV monitor 或 Mac mini 的休眠策略。

## 退回與限制

- 尚未切換 HB 時，可保留本地整合而繼續原路徑；不要同時從兩個入口修改旗標。
- 要明確回 PC agent：將 `THEATER_VIA_HA=false`，確認該 PC 中繼仍可用；HA 本地開關不需移除。
- 要回舊 HA 直連：Home Butler 選「不使用本地劇院整合」，再填回原網址／金鑰。
  不能只刪除本地整合期待自動回退。避免讓新本地入口與旧中繼長期成為兩個寫入主體。
- HA 整合停用不會關閉 Agent 的連動旗標。結果未知時先讀取或查看 Agent，不自動重送。
- Render 斷線不影響本地整合，但 HA VM／主機停機時無法操作，Agent 自身的既有連動則取決於其程序是否仍運作。

## 離線驗證

`homeassistant/tests/test_theater_agent.py` 使用真實 HA framework、模擬 Agent I/O，
涵蓋 switch 與 HB 共用狀態、初始只讀、輪詢失聯恢復、部分寫入、未知不重送、
併發拒絕、設定改址保留 entity、重新驗證、白名單及移除整合不 fallback。
`pytest -q` 於 `homeassistant/` 執行；不使用家庭家電測試。
