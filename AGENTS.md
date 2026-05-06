# 版本管理

系統版本不在 home-butler 管。Source of truth 是 **Dashboard 的 `package.json:version`**，本 repo 透過 `config.py:get_app_version()` 在 runtime 撈 Dashboard `/api/version`（1 小時 cache，失敗 fallback「未知」），由 `prompt.py` 注入 `SYSTEM_PROMPT`，讓 LINE bot 能回答「目前版本是？」之類的問題。

需要設定環境變數 `DASHBOARD_URL`（Dashboard 部署網址）才撈得到；沒設或撈不到就會回「未知」，但 bot 仍能正常運作。

**bump 時機**：使用者**體感得到**的變化才 bump（新功能、UI/行為改動、會被察覺的 bug fix）。純 refactor、註解、文件、type 整理**不 bump**。

**bump 流程**：只動 Dashboard `package.json:version` + push 一次。**home-butler 完全不用 push 也不用改任何檔案**。最壞情況是 cache 還沒過期、LINE 回答的版本舊一陣子，1 小時內自動同步。

本專案不使用 git tag / GitHub Releases；版本以 Dashboard `package.json` 為準，git history 自己就是版本軌跡。

# Git push 環境差異

這個 repo 會被多種 harness 操作（本機 VS Code、claude.ai/code web UI 等）。
如果 `git push` 失敗、錯誤是認證相關（no credentials / permission denied / could not read Username），**立刻停下來，不要繞路**：

- 不要設 git credential helper、token、或改寫 remote URL
- 不要用 curl 打 GitHub API 繞過
- 不要改 SSH

如果當下環境有 GitHub MCP 工具（`mcp__github__*`），直接切過去用；沒有就回報「這個環境沒有 push 權限」由 User 處理。
