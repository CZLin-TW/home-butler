"""每台 PC 自己一份，填本機特定資訊跟 secret。

複製成 agent_config.py、編輯後別 commit（已在 .gitignore）：
    cp agent_config.example.py agent_config.py
"""

# 必填 ─────────────────────────────────────────────

# 跟 home-butler Render 上的 env var HOME_BUTLER_API_KEY 一樣的值。
HOME_BUTLER_API_KEY = ""

# 顯示用簡化型號（agent 不自動偵測——psutil 拿到的是
# 「Intel(R) Xeon(R) CPU E3-1230 V2 @ 3.30GHz」這種長字串，這裡填短的）
CPU_MODEL = ""    # 例如 "Xeon-1230v2"、"R5-7600X"
GPU_MODEL = ""    # 例如 "GTX-1650S"、"RTX-4070Ti"


# 選填（不寫就用 default） ─────────────────────────

# HOME_BUTLER_URL = "https://home-butler.onrender.com"
# LHM_URL = "http://localhost:8085/data.json"
# TICK_SECONDS = 60

# Philips Hue local Bridge（選填；先給 hue_probe.py 測試用，後續可接燈光通知）。
# HUE_BRIDGE_IP = "192.168.1.10"
# HUE_APPLICATION_KEY = ""  # hue_probe.py auth 回傳的 username
# HUE_CLIENT_KEY = ""       # auth 回傳的 clientkey；一般通知不一定會用到
# HUE_NOTIFY_GROUPED_LIGHT_ID = ""  # hue_probe.py list 找到的 grouped_light id
# HUE_LIGHT_REMINDERS_ENABLED = True

# Realtime command channel（選填）：agent 主動用 WebSocket 連到 home-butler/Render。
# 第一階段只回報在線 + heartbeat，後續會承接 Hue 即時控制 command。
# AGENT_WEBSOCKET_ENABLED = True
# AGENT_WEBSOCKET_HEARTBEAT_SECONDS = 25
# AGENT_WEBSOCKET_RECONNECT_SECONDS = 10

# Auto-update：每小時 check 一次 origin/main，有新 commit 就 git pull
# + os._exit(1) 讓 Task Scheduler 重啟 process 拉新 code。預設啟用。
# 緊急時改 False 暫停這台 PC 接收新版（例如 main 推了壞 code 你要鎖版本 debug）。
# AUTO_UPDATE = True

# Log 檔位置（會 rotate：5MB × 3 份輪替）。預設 ~/butler-agent.log，
# Windows 上會解到 C:\Users\<你>\butler-agent.log。要改別處例如 C:\butler-agent\agent.log
# 取消下面註解：
# LOG_PATH = r"C:\butler-agent\agent.log"
