# Siri 請求分段耗時

完整 `/api/assistant` 與家電專用 `/api/assistant/devices` 會自動輸出 `[TIMING]` JSON 至標準輸出，也就是 Render Logs。不需新環境變數、不改捷徑，回應維持 `{reply}`。只增加觀測，不調整 prompt、模型設定、權限或重試，不會為了量測額外呼叫 AI。

## 查閱方式

1. 部署包含此功能的 home-butler 後，照常使用 Siri，記下操作時間與使用的入口。
2. 在 Render Logs 搜尋 `[TIMING]`，找出該時間附近的 `request_start`；`source` 為 `siri_full` 或 `siri_devices`。
3. 複製其 `request_id` 搜尋，即可串起開始、各段與結束紀錄。並行請求各有自己的識別碼；沒有記錄輸入內容，若同時多人使用，建議分開測試以便對照。
4. 比較 `stage_end.duration_ms`，再看 `request_end.duration_ms`。單位是毫秒，除以 1000 為秒。每種指令比較數次，保留第一次和後續結果，避免把快取或偶發延遲當成固定瓶頸。

以下是格式示意，數字不是正式量測結果：

```text
[TIMING] {"request_id":"abc123","source":"siri_full","event":"stage_end","stage":"ai_parse","span_id":"def456","status":"completed","duration_ms":2800.0}
[TIMING] {"request_id":"abc123","source":"siri_full","event":"request_end","status":"completed","duration_ms":5550.0}
```

每一段有 `stage_start`／`stage_end`，以 `span_id` 配對；開始紀錄能辨認目前卡在哪一段。發生例外仍記錄結束、`status=error` 及錯誤類別，不附例外內容。程序遭強制終止時可能只有開始而沒有結束，不能將缺少結束視為零耗時。

## 階段意義

| stage／event | 範圍 |
| --- | --- |
| `sheets_load` | 初始 Sheets 讀取及轉換；完整入口五張分頁（略過對話暫存）、家電入口只有智能居家，包含這次載入觸發的認證／metadata／既有重試 |
| `identity` | 完整入口的使用者名稱解析 |
| `context_prepare` | prompt、設備目錄及當句文字組裝，Siri 不帶歷史對話；完整入口也含版本查詢、照明資料等可能觸發的額外 I/O，不能一律當純 CPU 處理 |
| `ai_parse` | 第一次意圖解析的 SDK 呼叫，包含網路、供應商處理及 SDK 原有重試 |
| `ai_parse_fallback` | 完整入口原有 BadRequest 降級呼叫，僅在觸發時出現，與第一次分開計時 |
| `validate_actions` | 家電專用入口整批動作驗證；拒絕或需要釐清時不執行設備 |
| `action.<動作名稱>` | 單個 handler 的完整執行，包括設備服務、額外 Sheets 讀寫及原有副作用；多動作分別記錄，尚未再細分 handler 內的各種 I/O |
| `ai_reply` | 完整入口特定查詢的第二次模型呼叫；沒有發生就沒有此階段 |
| `model_usage` | 已返回的模型回應 token 用量及 `stop_reason`，不是額外計時；缺少的欄位為 null，不能當成 0 |
| `request_end` | 從同步路由函式進入到回傳／拋錯的總時間；包含其間的解析、格式化、log 等未單列工作，不能與子階段再加總 |

`completed` 只表示該程式區段沒有向外拋出例外，**不是家電已成功執行**。handler 回傳錯誤文字、模型回應截斷、權限釐清或被捕捉的部分失敗，都可能正常回傳；請合併原有結果紀錄與 `stop_reason` 判讀。SDK 的內部重試沒有逐次拆開；此功能不修改原有錯誤處理。

總時間不含手機收音、手機到 Render 的網路、Render 冷啟動／代理等待、進入路由前的驗證／threadpool 排隊、回應傳输或 Siri 朗讀。對話存檔在另一條背景 thread，不等待其完成，未列入分段。若捷徑 URL 步驟明顯比後端總時間長，應再查上述未涵蓋區段，不能把差值直接歸因於 Sheets。

## 維護與測試

實作是 `request_timing.py` 的 request-local ContextVar 與單調時鐘。只在兩個 Siri 路由啟用；LINE、每日推播及獨立 benchmark 不會產生這組請求紀錄。新背景 thread 不繼承這個計時範圍。日志寫入失敗不改變業務回傳，也不重送設備或模型請求。

新增計時只能使用程式定義的階段名稱，不傳文字、使用者 ID／姓名、設備參數、金鑰或完整例外。此限制適用於新增 `[TIMING]`；既有 DEBUG／業務 log 並未因此去識別化。

離線測試見 `tests/test_request_timing.py`，使用假時鐘、模型、Sheets 及 handler，覆蓋並行隔離、錯誤收尾、記錄失敗、原有模型降級、完整入口二次 AI 與家電入口的部分失敗。它驗證計時串接與契約，不代表已量到 Render 或實體家電耗時。部署後仍需以實際 Siri 請求的 log 分析瓶頸。
