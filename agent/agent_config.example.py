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

# Log 檔位置（會 rotate：5MB × 3 份輪替）。預設 ~/butler-agent.log，
# Windows 上會解到 C:\Users\<你>\butler-agent.log。要改別處例如 C:\butler-agent\agent.log
# 取消下面註解：
# LOG_PATH = r"C:\butler-agent\agent.log"
