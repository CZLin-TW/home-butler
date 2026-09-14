# 選擇架構、下載舊版與固定部署

## 三種選擇

1. **目前 main ＋ HA：** 現行家庭中樞配置，HA 管設備與本地自動化，HB 管家庭資料、AI 與提醒。
2. **目前 main、不啟用 HA：** 程式仍保留直接 API／PC Agent；不必為「沒有 HA」而退回舊版；但 main 已移除 HB 自動夜燈，需要該舊功能請選固定快照。
3. **HA 導入前的固定快照：** v1.44.0，適合要保留當時完整架構及相容前端的人。

以下「目前」以 2026-09-14 的程式核對為準；選項 2 是保留的程式路徑，
不代表所有舊硬體／外部 API 在最新 main 或舊快照上已重新實測，也不是獨立長期維護版承諾。

v1.54.0 新增的二維色盤／白光色溫需要 HA home_butler 1.5.0；不使用 HA 的 main
仍可使用原有 Hue 電源、亮度、場景與特效，前端不顯示未支援的新光色控制。
部署與驗收的固定 SHA 見 [驗證紀錄](verification.md)。

## 目前 main 不使用 HA

後端不設定 `HOME_ASSISTANT_API_KEY`；HA 控制設定保持預設，或明確設為：

```text
HOME_ASSISTANT_AC_NAMES=[]
HOME_ASSISTANT_IR_NAMES=[]
HOME_ASSISTANT_SENSOR_NAMES=[]
HOME_ASSISTANT_HUE_ENABLED=false
```

三組名稱必須是合法 JSON 陣列，不能填空字串來代替 `[]`。
如曾在平台填過變數，要檢查實際部署設定；只從檔案刪除名稱不一定會移除平台上的舊值。
不安裝 `homeassistant/` 套件；需要的 SwitchBot／Panasonic／LG 等設定仍依
[後端指南](backend-guide.md) 填寫。Hue 改由 [PC Agent](../agent/README.md) 執行；
Apple Home 可選用 [Homebridge 舊路徑](../homebridge/README.md)，FP2 不會因此自動接進 HB。

若這是已使用 HA 的家庭要退回直連，這些設定會真正轉移設備控制權：
先備份設定與資料、停用重複規則、核對直接 API 憑證及 IR 最後狀態，
再轉移；不能只關掉 HA 主機。HA 空調沒有把最新設定寫回舊 Sheet 狀態，
舊值不可直接當作當前設備狀態。需保留既有家庭服務時先用隔離副本測試。

## HA 前最後快照：v1.44.0

| Repo | 完整 commit SHA | 原始碼與同版說明 |
| --- | --- | --- |
| home-butler | `5c0b681f149741236d16d100bf96b734d1b1f767` | [程式](https://github.com/CZLin-TW/home-butler/tree/5c0b681f149741236d16d100bf96b734d1b1f767) · [ZIP](https://github.com/CZLin-TW/home-butler/archive/5c0b681f149741236d16d100bf96b734d1b1f767.zip) · [當時 README](https://github.com/CZLin-TW/home-butler/blob/5c0b681f149741236d16d100bf96b734d1b1f767/Readme.md) |
| Dashboard | `2c31431d0588e4cfbe8323384234f64d092dfd9d` | [程式](https://github.com/CZLin-TW/Dashboard/tree/2c31431d0588e4cfbe8323384234f64d092dfd9d) · [ZIP](https://github.com/CZLin-TW/Dashboard/archive/2c31431d0588e4cfbe8323384234f64d092dfd9d.zip) · [當時 README](https://github.com/CZLin-TW/Dashboard/blob/2c31431d0588e4cfbe8323384234f64d092dfd9d/README.md) |

核對依據：後端首次引入 HA 的 `b86c6d7` 的父 commit 是上表 `5c0b681`；
Dashboard 首次加入 HA 的 `6316c03` 的父 commit 是 `2c31431`，其 `package.json` 顯示 `1.44.0`。
這是 Git 歷史邊界核對，並非此次重新部署舊版或驗收外部服務。

此快照包含 HB 直接空調／IR／感測讀取、PC Hue、Homebridge，以及當時的半度目標／空調回饋。
`agent/` 與 `homebridge/` 隨後端固定；Homebridge 插件當時為 1.3.0，不是 1.44.0。
私人 theater-agent 是獨立選配 repo，不能由這兩個 SHA 推定其版本或存取權。
舊快照不會自動得到後續修正；今日第三方 API、模型、套件與帳號條件仍需自行驗證。

## 下載 ZIP 或使用 Git

只看程式／手動部署可用上表 ZIP。ZIP 不含 `.git` 歷史，不能直接 `git pull`；
需要版本切換與後續開發請 clone。GitHub 支援以 commit 下載固定內容，不需要先建立 Release。
[GitHub 官方說明](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives)

以下在**新的工作目錄**執行，避免覆蓋現有部署與未提交修改：

```sh
git clone https://github.com/CZLin-TW/home-butler.git home-butler-v1.44.0
cd home-butler-v1.44.0
git switch --detach 5c0b681f149741236d16d100bf96b734d1b1f767
git rev-parse HEAD
cd ..

git clone https://github.com/CZLin-TW/Dashboard.git Dashboard-v1.44.0
cd Dashboard-v1.44.0
git switch --detach 2c31431d0588e4cfbe8323384234f64d092dfd9d
git rev-parse HEAD
```

`--detach` 代表停在固定 commit，不跟著分支前進；不是刪掉歷史，也不影響 GitHub 的 main。
`git pull` 用來取得並整合分支更新，不是「選擇任意舊版本」的命令。
若要在此基礎修改，可在各 repo 使用 `git switch -c my-standalone` 建立自己的分支。
[Git switch 官方說明](https://git-scm.com/docs/git-switch)

要回目前 main，先保存自己的修改，再於兩個 repo 各執行：

```sh
git switch main
git pull --ff-only origin main
```

如有未提交修改或分支分歧，先處理差異，不使用 `reset --hard` 強行覆蓋。
切版後後端重建虛擬環境並按同版 `requirements.lock` 安裝，Dashboard 執行 `npm ci` 重建相依套件。

## 固定部署與更新

下載／checkout 只改原始碼，不會自動改 Render、Vercel 或家中 Agent。

- **Render／Vercel：** 若追蹤 main 並開自動部署，下次 main 更新仍可能部署新版。
  固定版應在自己的 fork 建立專用部署分支（從上表 SHA 建立、不要合併 main），
  讓平台追蹤該分支，並核對平台實際 deployed SHA。平台若可指定 commit，也要確認後续自動部署策略。
- **PC Agent：** 當時版本預設檢查 `origin/main`。固定舊版前，在私有 `agent_config.py` 設 `AUTO_UPDATE = False`，
  再啟動／重啟並核對程序版本；不要只改 checkout，卻讓背景更新把它推進 main。
- **Homebridge：** 若採舊路徑，從同一份後端快照的 `homebridge/` 安裝，不混用之後的來源。
- **資料與設定：** commit 不包含自己的 Sheets、金鑰、HA／Homebridge 配對或平台環境變數。
  回復程式不會回復這些資料；先備份，再核對欄位、控制來源及自動化狀態。
- **跨 repo 相依：** 一般升級先後端、再 Dashboard；回復先 Dashboard、再後端。
  不在公開專案強制推回 main 來達成個人部署回復；自己部署可固定分支，正式修復可用新 commit／revert。

本專案維持「Dashboard 顯示版本＋每個 repo 的 Git SHA」方式，不另外建立 tag／Release。
這份文件提供固定快照索引，不承諾所有任意跨版本組合相容。
