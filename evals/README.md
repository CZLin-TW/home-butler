# Sonnet 5 thinking／effort 比較

這是獨立的付費 API 評估程式，不是正式路由，不會控制家電、讀写 Sheets 或發 LINE。
`prepare` 和 `report` 不連網。`run` 會直接呼叫 Claude Messages API，開始前需取得使用者對付費呼叫數的授權。

2026-09-06 本次授權上限：**120 次 HTTP 請求**，包含失敗或中斷；不是 120 次成功後還可補跑失敗。

此輪已完成 120 次：[結果與判斷](results/2026-09-06.md)。原授權額度已用完，不能直接重新執行付費測試；正式模型設定維持原狀。

## 固定比較

| 組別 | thinking | effort |
| --- | --- | --- |
| A（目前基準） | adaptive | high（目前程式省略 effort，官方預設 high） |
| B | adaptive | medium |
| C | disabled | medium |

20 個案例 × 3 組 × 2 次 = 120 次；完整管家與家電專用入口各 10 例。
完整入口使用目前 `SYSTEM_PROMPT`／`ACTION_SCHEMA`，僅以 AST 抽取常數，避免 import 正式服務。
家電入口使用原有 `SYSTEM`／`SCHEMA`／目錄投影及參數驗證器。
日期固定為台北 2026-09-06 19:00，設備／成員／待辦／食品／排程均為模擬資料，沒有歷史對話。
資料文字是固定測試快照，不代表正式家庭資料的長度與分布。

案例與預期結果先定義在 `sonnet_cases.py`。比較動作、目標、參數、額外動作及是否正確追問；
允許「電風扇／電扇」已支援的名稱別名及明確列出的預設參數，動作順序在本組獨立操作案例中不計分。
家電指令必須也通過現有後端驗證；未知房間可由模型追問或保留未知名稱後由驗證器拒絕，不能改控其他房間。
JSON 格式正確不等於意圖正確；`stop_reason` 不是 `end_turn`（包括 max_tokens 截斷）一律不通過。
純文案整理／推播不在本次 120 次中，不能把結果推論為這兩種呼叫也測過。

## 執行方式

使用有 `httpx` 的獨立 Python 環境；`pip install -c requirements.lock httpx` 即可，不需要正式依賴或服務。
金鑰由 `ANTHROPIC_API_KEY` 讀取；Windows 另支援直接讀取使用者環境變數，方便在設定後免重啟開發工具。
建議另建測試用 Key，測完撤銷；不要把金鑰放在程式、命令列參數、報告或 Git。

```sh
python evals/sonnet_effort.py prepare --out ../sonnet-effort-run-20260906
python evals/sonnet_effort.py run --out ../sonnet-effort-run-20260906
python evals/sonnet_effort.py report --out ../sonnet-effort-run-20260906
```

一次性測試可免設環境變數，改執行 `python evals/sonnet_effort.py run --prompt-key --out ../sonnet-effort-run-20260906`。
本機會開啟遮蔽字元的輸入視窗（需要 Python tkinter），貼上測試 Key 後按「開始測試」。
Key 只交給目前程序，不写入環境變數或檔案；取消視窗不送請求，也不改用其他已設定金鑰。
程序結束後，再到 Claude Console 撤銷測試 Key。此模式與原模式共用同一份 manifest／ledger，額度不會重置。

第一次 prepare 寫出不可變的 `manifest.json`：所有請求、預期結果、程式摘要及價格來源。
執行會先將請求占額紀錄 fsync 至 `attempts.jsonl`，才送出 HTTP。每筆僅送一次，無 SDK、自動重試、轉址、schema fallback 或額外暖機呼叫。
相同資料夾續跑時跳過所有已占額的請求（含中斷），總數仍 ≤120。不得另開輸出資料夾繞過原授權上限。
同時只能一個 runner；崩潰留下的 `runner.lock` 需確認程序已結束後才可移除，保留 manifest 與 ledger。
來源或 manifest 改變、ledger 損壞時停止，不自動清除或重建紀錄。400／401／402／403／404 或連續三次傳輸／服務錯誤也會提前停止。

每次 HTTP 完成會輸出進度，並更新 `summary.json` 與 `report.md`。輸出僅保存模型文字、用量、request ID、耗時及評分，不保存 thinking 或 Key。
準備步驟的 0/120 報告不是實測結果；尚未完成的組別不得寫成已完成的性能比較。

## 解讀限制

- A vs B 比較 effort，B vs C 比較 adaptive thinking；模型、schema、輸入及同入口輸出上限相同。
- 逐筆序列執行、固定 seed 打散案例並交錯組別。耗時是完整 HTTP 回覆時間，包含網路與服務排隊；不是純推理耗時，也不包含 Siri、Sheets 或設備。
- 不增加 prompt cache；初次 schema 編譯可能影響少數結果，應同時查看逐筆／重複輪次與中位數，不只看最慢一次。
- 4000／2000 token 上限沿用正式解析入口；因此比較的是現有預算下能否完成，不是模型無限輸出時的最高準確率。
- 次數小、每例只有兩次，不能宣稱穩定性或統計顯著性；必須人工核對模型文字，避免把等價表達誤評失敗或把不合適追問評為成功。
- 費用按 [官方價格](https://platform.claude.com/docs/en/about-claude/pricing) 2026-09-06 的 Sonnet 5 標準全球 API USD 2/10 每百萬輸入／輸出 tokens 估算；包含 usage 的快取項目，未含稅／折扣。未知用量的失敗／中斷請求不能算免費，帳单以 Console 為準。

离線護欄測試見 `tests/test_sonnet_eval.py`，隨一般 unittest 執行，全部 HTTP 為 fake；CI 不會跑付費 benchmark。
