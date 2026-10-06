# 獨立 media authority fixture

這是只有假憑證的單 process 實驗，沒有註冊到 main，沒有連線現役 mini。
`vision_media_api.create_fixture_app` 僅供 fixture；provider 預設空白。獨立
`vision-media.v1` 不更動 vision.v1 控制協定，也不共用家庭 API key。

Dashboard 兩個以上無狀態 BFF 可以共用同一 HB authority。HTTP POST
`/api/vision-media/v1/{offer,heartbeat,stop,state}` 以 service Bearer 認證；
actor `{id,expires_at}` 必須通過 mock provider 的 preview grant、到期與撤銷检查。
只有 synthetic-alice/synthetic-bob 有假 grant。actor 是受信任 BFF 宣告，
不是正式身分供應商；正式驗證、enrollment、持久化均未完成。

專用 device outbound WebSocket `/api/vision-media/v1/device` 用另一把假 Bearer。
hello `{protocol:'vision-media.v1',type:'hello',device_id:'synthetic-mini'}`；
welcome `{protocol,type:'welcome',epoch,expires_at}`。命令
`{protocol,type:'request',id,epoch,action,deadline,payload}`，action 只允許 offer、
heartbeat、stop；回應 `{protocol,type:'result',id,epoch,status,body}`，status 為 HTTP 整數。
HB 命令最多 4 秒；JSON 最多 40 KiB，SDP 最多 30 KiB。只由 mini adapter 呼叫
其本機 synthetic native source；HB 沒有 native HTTP URL 或通用 proxy 能力。

HB 持有全裝置唯一 pending/active lease，瀏覽器取得不透明 actor-owned ID；
native session ID 不直接暴露。state 只回自己狀態，不回別人的 SDP、影像或 lease。
回應投影禁止未知欄位；不記錄 SDP、token 或網路地址。active/pending 競爭回
409 media_viewer_busy；未知隔離回 503 media_result_unknown。隔離期間即使記憶體租約已清除，state 對所有 actor 均回 active:false、reason:unknown，不宣稱 native 已停止。已確認 stop 可本機重複成功，
未知結果不重送，heartbeat 不延長固定期限。撤銷、到期會嘗試有界 stop，失聯依 native TTL。

重啟預設以單調時鐘計算 67 秒隔離（系統校時不會提前解除），避免遗忘的 native session 與新 viewer 重疊；斷線及未知也同樣隔離。
CLI `scripts/vision_media_loopback_fixture.py --ready-file NEW --stop-file MARKER`
只綁定 127.0.0.1 ephemeral port。`--fresh-native` 只用於首次啟動且 native 是全新程序，
不可用來跳過重啟隔離。`--port` 支援重用測試自己先前的 port；`--revoke-file` 出現時撤銷
固定 synthetic-alice；`--lifetime` 上限 300 秒。所有明文 token 都是程式固定的 public-only
測試值，不接受真實 credential。沒有部署、公開入口、相機或 HA 存取。

驗證：`../phase2-venv/bin/python3 -m unittest discover -s tests -p test_vision_media.py -v`。
