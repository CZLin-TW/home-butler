# macOS telemetry daemon 原始碼

獨立於 Windows agent、vision、theater 及家電控制。Swift sender 在採樣完成後才讀自己的
System Keychain item，直接送既有 `/api/computers/heartbeat`；Python collector 不接觸 key。
LaunchDaemon 使用明確指定的非 root 使用者，每 60 秒採樣一次，成功後退出；不做 auto-update。

這是從已驗證的本機部署整理出的**下一版原始碼**，不是現役 binary 的替換包。
重新編譯會改變 ad-hoc cdhash，不能覆蓋現役 sender 或直接沿用其 Keychain 信任。
本次整理未部署；先前部署已確認 system job 自然回報與 GUI job 停用，
**重開機後未登入、FileVault 解鎖前與睡眠期間的運作均未驗收**。

## 內容

| 檔案 | 用途 |
| --- | --- |
| `sender.swift` | 固定建置設定、採樣白名單、HTTPS、隱藏 key 輸入、精確 app ACL、metadata/probe |
| `settings.py` / `config.example.json` | 僅非秘密設定；未知欄位、非 HTTPS、root UID、路徑越界拒絕 |
| `build.py` | 離線編譯／ad-hoc 簽署、複製自備 runtime、產生雜湊 manifest 與操作入口 |
| `install.py` | root-owned 安裝、公用非秘密 probe、使用者操作的 setup、切換與 rollback |
| `resume.py` | 重用唯一既有 item；真 system-domain 單次驗證、持久續接紀錄、兩次自然回報 |
| `diagnose.py` | 唯讀 hash/ACL/格式與 allowlist 狀態；不讀 key、不手動送出、不 dump env |
| `operator.command.in` | 本機產生入口；sudo 前核對檔案雜湊、清理環境、結束保留視窗 |

## 開發與測試（不需憑證）

macOS、Xcode Command Line Tools、Python 3.11+。從 repo 根目錄：

```sh
python3 -m unittest discover -s tests -p 'test_macos_daemon.py' -v
python3 agent/macos_daemon/build.py \
  --config agent/macos_daemon/config.example.json \
  --output /tmp/homebutler-telemetry-fake-tests --tests-only
```

輸出目錄必須不存在。Swift tests 使用文件 IP、`.invalid` 網域與 FAKE key；
只驗證 payload、request、控制流程、partition policy，沒有 Keychain／網路存取。
macOS CI 只跑上述假測試，不跑安裝器／採樣／setup。Linux CI 執行 Python 假 OS 回歸。

## 準備私人部署包（不會安裝）

1. 將範例複製到 gitignored `.local/config.json`，填入核准的 HTTPS origin、卡片 IP、
   顯示名稱、非 root user/UID、group/GID、home、獨立 install 目錄、label、service。
   `account` 是穩定 IP 的卡片主鍵，不自動發現；hostname 是指定名稱，不讀本機帳號。
   設定不得包含 API key。建置設定目前限定 ASCII／無反斜線。
2. `previous: null` 表示全新安裝，**不會停用任何已有 agent**。
   遷移時明確填入 `previous: {"label":"org.example.old", "sender":"/absolute/path/to/old-sender", "sender_sha256":"<reviewed 64 hex>"}`。
   只接受既有 `[sender, "run"]`、60 秒 GUI agent；其他舊版需另行審查，不猜測／刪除。
3. 自備 standalone Python runtime，包含 `psutil`（版本依 `../requirements-macos.txt`）。
   禁止指向使用者 venv／外部 runtime 的 symlink；builder 拒絕越界連結。
   發佈前另核對 Mach-O 的實際載入依賴與 rpath，並驗證 interpreter/psutil 可離線使用；
   路徑 inventory 不等於原生動態庫完整稽核。不要把 runtime 或輸出包加入 Git。
4. 本機建置：

```sh
python3 agent/macos_daemon/build.py \
  --config agent/macos_daemon/.local/config.json \
  --runtime /path/to/reviewed-standalone-runtime \
  --output agent/macos_daemon/.build/reviewed-package
```

原始碼／依賴／設定及生成 manifest 要先 review。SHA256 可偵測變動，並不是第三方公證；
此工具不下載 runtime、不使用開發者私鑰，也不提供自動更新或原地信任迁移。

## 安裝、續接及回滾（需使用者另行授權）

確認這份**新建包**的目的地、資料、獨立目錄、label/service 與舊 agent 範圍後，
由使用者在 Terminal 親自執行產生的 `operator.command`：

- `INSTALL`：核對使用者身分，複製 root-owned runtime／collector／sender，建立 UID-owned state。
  使用公用 sentinel 做真正 system-domain probe，再刪除本工具建立的**測試 item**。
  私密 API key 只在原生 sender 的隱藏 TTY 輸入；`SEND` 明確允許一次送出並驗證回覆，
  `CREATE` 才建立全新正式 System item。既有正式 item 絕不覆寫。
- `RESUME`：不再輸入 key／CREATE。核對唯一 item、實際所屬 Keychain、資料庫版本、
  精確 sender 簽章／application ACL，才以原 sender 在 system domain 執行一次。
  只認 HTTP 200 與 `{"ok":true}`；核對正常 exit 及前後 metadata 完全相同。
  `SWITCH` 才停舊 GUI agent、備份／移除舊 plist、載入新 daemon，等兩次自然回報。
  明確失敗、逾時或可捕捉中斷會恢復原 agent；新安裝則停用新 job。
- `ROLLBACK`：只停本套件的精確 job／plist，恢復備份 GUI agent（若有）；兩份正式 key
  及部署檔案保留。不是憑證刪除／卸載工具。
- `STATUS`：不需 sudo 的唯讀診斷；若權限不足就停止，不自動提權或放寬權限。

變更操作持有非阻塞本機鎖，拒絕同時執行兩個安裝／續接／回滾入口。
續接 marker 保留「已嘗試／已 acknowledged」；視窗關閉後重開入口不盲目重送。
marker 與 job 不完整時停止，先診斷，不刪紀錄來強行重跑。
SIGKILL／斷電仍可能留下中間狀態，不能保證自動恢復；備份保留，需核對後使用 rollback。
若正式 job 已載入，入口只報告該狀態，不把「已載入」當成健康或 pre-login 驗證。
原始 setup 在 CREATE 後中斷時使用 RESUME，不重輸 key。

## Keychain 故障與版本規則

- 傳統 application ACL 僅信任固定 sender 路徑及其完整指定需求；不用 `security -A`、
  通用 Python/shell reader、解鎖 Keychain 或降低 OS 防護。
- metadata 必須從 item reference 取得真正 owning Keychain path 及 database version。
  只設定 search list 或看到 probe 成功不足以證明正式 item 的所在格式。
- `0x100`／`0x101` 是舊格式，要求無 PartitionID ACL；`0x200` 要求唯一
  `cdhash:<目前 sender>`。未知版本／額外 partition／錯誤 sender 一律拒絕。
- 曾發生 credential value 由另一 helper 改寫後，modern keychain 的 partition 改成
  helper，原 sender 因此被拒絕。不能只看 application ACL 就宣稱沒變。
  本套件沒有 `SecItemUpdate`、partition 修補或正式 item 刪除功能。
- 先前一次性 value 修復、partition 修復、舊登入版安裝工具不納入一般操作入口；
  保留回歸與說明，避免把事故處理變成常態的越權 fallback。

Apple 原始碼依據：
[格式版本](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurityd/lib/ssblob.h)、
[資料庫格式選擇](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurityd/lib/ssblob.cpp)、
[partition 驗證](https://github.com/apple-oss-distributions/Security/blob/main/securityd/src/acls.cpp)。
這些舊 Security API 在 SDK 中已 deprecated；編譯警告保留，OS 更新需重新驗證，不自動繞過。

## 資料與安全邊界

只外送指定 IP／hostname、CPU/GPU 型號、CPU/RAM/GPU 百分比及可用 TCMb／TCMz。
`local_only` load average 不外送；CPU/GPU 攝氏欄位仍 null，thermal pressure 不當作溫度。
沒有 process list、檔案、帳號、序號、影像或控制命令。API 仍使用既有家庭級 key，
並非新的 telemetry-only 權限；禁止混用 HA／語音憑證。秘密不進 argv/env/log/plist/config。

使用 ephemeral HTTPS、禁止 redirect/proxy/cookie/cache，明確失敗不立即重送；
下個 60 秒週期是新樣本。停止可能觸發既有離線告警。
無 DNS／VPN／家電／相機操作；不自動重開機、登出、啟用 FileVault 或修改安全設定。

Git 只收 source、fake tests、範例與文件。忽略私人設定、簽章產物、runtime、Keychain、
憑證、log 與採樣資料。靜態掃描與人工 staged review 只能降低風險，不能保證所有秘密均可辨識；
不為掃描讀取真正 API key，也不改寫既有上游歷史。
