# macOS 完整 agent 自動更新

每 300 秒檢查 `CZLin-TW/home-butler` 的 `main`。只有同一 commit 的 `.github/workflows/ci.yml`
**push** workflow 最新 run／attempt 成功，才下載該 SHA 的 sender、trust 與兩個 collector。
這是 Mac 主動查詢 GitHub，無需開放 inbound webhook、SSH 或安裝 GitHub runner。
PR 或 CI 尚未完成／失敗不部署；下載逾時、rate limit、簽署／編譯失敗保留現役版本。
已知 main 僅一個 REST request；agent 檔案沒變則不編譯、不重啟。

目前原始碼與安裝包已準備，**正式一次性啟用仍待完成**；驗證證據見
[verification](../../../docs/verification.md)。舊 `build.py` 安裝的 ad-hoc sender 不會自行採用此機制。

## 更新範圍與身分

- 自動部署 `sender.swift`、`auto_update/trust.swift`、`macos_metrics.py`、`macos_temperature.py`。
  `requirements-macos.txt` 也釘選雜湊；變更依賴時停止，需另行準備 runtime。
- 每台 Mac 初次安裝產生專用十年自簽 code-signing 憑證。DR 固定 identifier＋該憑證 SHA1；
  SHA1 是 Apple requirement 語法使用的憑證指紋，來源與檔案另以 SHA256 校驗。
  私鑰只保留在 root-only 專用 Keychain，原生 helper 存取解鎖值，不放入 argv、環境或 Python。
  匯入時的 p12 密碼刻意為公開常數；短暫 p12／PEM 的保密來自 root-only 目錄與 0600 權限，匯入後刪除。
  `/usr/bin/codesign` 是私鑰唯一 trusted app；不新增系統根憑證信任。
- 只支援已實測的 **System Keychain 0x100**：無 partition 或單一惰性 creator-cdhash 記錄。
  0x101／0x200、未知授權或廣泛 partition 都拒絕；不得推論其他 OS 格式也可跨版。
- 固定的 root updater 僅執行本機 Swift compiler／codesign 等已知工具；不執行 repo shell、pip、
  build plugin 或下載的程式作為 root。更新後 sender 仍以原非 root 使用者執行，只有它讀取 API key。
  updater、signer、設定與 runtime 本身不自我更新；它們有變動需人工審查及管理員更新。
  一次性 ACL 遷移才以 root 執行當次已審查的 native sender。

## 首次準備與啟用

先完成 [公開跨版本實驗](../signing_probe/README.md)，核對原 System daemon 的 exact binary／plist
SHA256、cdhash、唯一 item／database、使用者與 standalone runtime。把以下非秘密設定放在
gitignored `.local/update-template.json`；不要寫入 API key、私鑰或 Keychain 解鎖值。

```json
{
  "deployment": {
    "label": "org.homebutler.telemetry.auto",
    "service": "org.homebutler.telemetry.api-key.boot-v1",
    "account": "192.0.2.20",
    "hostname": "Mac mini",
    "origin": "https://butler.example.invalid",
    "user": "example", "group": "staff", "uid": 502, "gid": 20,
    "home": "/Users/example",
    "install": "/Library/Application Support/HomeButlerAuto",
    "python": "bin/python3.12", "previous": null
  },
  "original": {
    "label": "org.homebutler.telemetry.boot",
    "sender": "/Library/Application Support/HomeButlerBoot/bin/mini-telemetry",
    "sha256": "REPLACE_WITH_64_HEX",
    "cdhash": "REPLACE_WITH_40_HEX",
    "plist_sha256": "REPLACE_WITH_64_HEX"
  },
  "requirements_sha256": "REPLACE_WITH_64_HEX"
}
```

由乾淨、已 commit 的 source 準備，輸出目錄必須不存在：

```sh
python3 agent/macos_daemon/auto_update/prepare.py \
  --config agent/macos_daemon/.local/update-template.json \
  --output /absolute/local/output/mac-update-package \
  --launcher /absolute/local/output/enable-update.command
```

此步只編譯 helper／封裝 source，不建立簽署身分、不觸碰服務或 Keychain。
使用者在 Terminal 執行 `.command`，只在 sudo 原生提示輸入管理員密碼。
launcher 釘選 manifest 雜湊；root verifier 校驗每個檔案並複製到 root-owned 暫存目錄後才 import。
安裝器依序：

1. 建立全新 root-owned 安裝目錄、沿用既有 runtime、建立本機簽署身分。
2. 用唯一 PUBLIC System item 實測原 ad-hoc → 完整新 sender → 原 sender，非 root system job
   讀取公開 sentinel；檢查回復後完整 ACL metadata 相同，刪除測試 item／job／目錄。
3. 鎖住舊 telemetry 作業並卸載；只把既有正式 item 的 decrypt app ACL 改成固定簽署身分。
   保留 value、description、prompt、其他 ACL 與 partitions，不匯出或重輸 API key。
4. 非 root 採樣與 metadata 檢查，啟動新 job；155 秒內要看到兩次正常排程的新版 SHA 回報。
   不手動 POST 或重送不明結果。成功才移除舊 startup plist（備份保留）並啟用 updater。
5. 捕捉到切換失敗會卸載新 job、恢復原授權／原 plist／原 job。SIGKILL／斷電無法執行首次
   installer 的 finally；此時看 `installation.json` 並人工核對，不重跑安裝器或猜測刪除資源。

只有公開前置測試失敗、尚無正式切換 journal／current／新 jobs、原 binary／plist 完全一致時，
修正版才允許接續。先以 metadata 查詢確認上一個唯一 PUBLIC item 不存在，清除其暫存目錄；
沿用已建立的簽署身分與 runtime，再完整重做公開測試。任何正式切換跡象或查詢失敗皆拒絕。

## 自動切版、觀察與暫停

`releases/<sha>` 保存編譯、簽署與雜湊驗證後的完整版本，`current` 是原子切換的相對 symlink。
先寫 `pending.json`，再等待上一個自然回報退出後切版；只承認帶新 SHA 的兩次自然 ACK。
失敗回復前一版、保留診斷，`failed.json` 阻止同 SHA 不斷重試。下一次 updater 執行先處理
未完成 journal，不需要 GitHub 在線；電源中斷後須等系統可執行 job，並非不間斷運作保證。
版本目前保留供回復／查驗，不自動清理；部署較多後由管理員檢查容量。

以下路徑均相對 install 目錄，無 API key：

| 位置 | 用途 |
| --- | --- |
| `installation.json` | 初次切換 prepared／switching／active／rolled_back |
| `public-migration-test.json` | 公開 item 移交／復原驗證結果 |
| `update.log` | 固定事件名稱、來源 SHA；bounded rotation |
| `state/status.log` | 原非 root 使用者可讀的自然回報／新版本 SHA |
| `pending.json` / `failed.json` | 未完成切換與禁止重試 SHA |
| `build/compiler.log` / `build/signing.log` | root-only 的編譯／簽署診斷 |

在核對 install 目錄後，管理員建立其 `paused` 空檔即可停止後續 GitHub 更新；刪除空檔恢復。
暫停不停止現役 telemetry；已有 pending journal 仍先回復。不要直接修改 `current`、刪除 signer
或用舊版 `install.py` 覆蓋這個目錄。回到遷移前的整套安裝須先核對保留的原 sender／plist／ACL，
不能把一般逐版本回復當成解除安裝。

## 測試

```sh
python3 -m unittest discover -s tests -p 'test_macos*.py' -v
python3 agent/macos_daemon/auto_update/check_native.py --output /tmp/new-native-update-tests
```

Python regression 使用假 OS／Keychain／GitHub；native 假測試不碰 Keychain 或網路。
公開實機簽署／System probe、首次正式遷移與真實 GitHub 更新是不同層次的驗收。
重開機未登入、FileVault 解鎖前及睡眠行為尚未完成驗收；OS 升級需重新驗證 Security API。
