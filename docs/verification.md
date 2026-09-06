# 驗證方式與範圍

以下是維護入口及已留存的驗證範圍，不是每次部署自動續期的保證。

## 離線檢查

```sh
pip install -c requirements.lock fastapi httpx
python -m compileall -q .
python -m unittest discover -s tests -v
```

測試用假 Sheets／設備 SDK／模型，覆蓋排程執行結果、未知結果不重送、列位移、控制結果、LINE callback 並行、首頁輕量查詢、工作隔離、天氣預算、待辦權限與 Notion 協調。家電語音測試另使用真實 FastAPI／TestClient，驗證獨立金鑰、停用／重複設定、拒絕身分覆寫及回覆契約；只需從既有 lock 安裝 HTTP 測試依賴，不需正式憑證。CI 設定見 [ci.yml](../.github/workflows/ci.yml)，目前使用 Ubuntu／Python 3.11。

測試不啟動正式服務、不發 LINE 或家電指令；不能證明 Google Sheets、認證憑證、外部雲端與家中設備當下可用。

付費的真實模型效能／辨識評估另見 [Sonnet effort benchmark](../evals/README.md)。它使用固定合成資料，只跑解析，不執行業務 handler；必須先取得 API 呼叫額度授權，且不加入 CI 自動執行。既有的 fake 模型測試不能當作降低 effort 後辨識率的證據。

## 已有紀錄與未涵蓋範圍

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
