# 驗證方式與範圍

以下是維護入口及已留存的驗證範圍，不是每次部署自動續期的保證。

## 離線檢查

```sh
python -m compileall -q .
python -m unittest discover -s tests -v
```

測試用假 Sheets／SDK，覆蓋排程執行結果、未知結果不重送、列位移、控制結果、LINE callback 並行、首頁輕量查詢、工作隔離、天氣預算、待辦權限与 Notion 協調。CI 設定見 [ci.yml](../.github/workflows/ci.yml)，目前使用 Ubuntu／Python 3.11。

測試不啟動正式服務、不發 LINE 或家電指令；不能證明 Google Sheets、認證憑證、外部雲端與家中設備當下可用。

## 已有紀錄與未涵蓋範圍

- 2026-09-06 的架構整理已包含上述離線回歸；具體執行結果以對應 [GitHub Actions](https://github.com/CZLin-TW/home-butler/actions) 的 commit 為準。
- 同日曾經從正式 Dashboard 讀取 PC 心跳與劇院兩程序版本，表示當時 Dashboard → home-butler → PC agent → theater-agent 的摘要路徑可讀。這不代表所有雲端控制與 LINE 推播已實機測試。
- 本次文件整理不新增真實設備、LINE、Notion 或 Sheets 寫入測試，不據此擴張先前測試結論。
- 多 worker／多實例、外部直接修改 Sheets 的競爭不在現有單程序鎖的保障範圍。
- `agent/` 的系統排程、硬體指標、Hue 控制與更新應在各台 PC 分別核對；後端 CI 不代表 Windows agent 已部署。

部署觀察請記下時間、目標程序、程式版本／SHA、最後成功時間與查到的結果。無法讀到程序版本時寫「未確認」，不能用 push 或 CI 代替。跨專案流程見 [系統導覽](system-overview.md)。
