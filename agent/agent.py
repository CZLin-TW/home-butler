"""home-butler PC monitoring agent.

每 60 秒讀本機指標 → POST 到 home-butler /api/computers/heartbeat。

Setup：
    cp agent_config.example.py agent_config.py
    pip install -r requirements.txt
    python agent.py

詳細見同目錄 README.md。
"""

import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import psutil

# 強制 stdout/stderr line-buffered。給 task scheduler / cron / nohup 之類 stdout
# 被 redirect 到檔案的部署情境（雖然新版 agent 自己管 file log，已不再依賴外部
# redirect，但保留這一層避免使用者 ad-hoc 跑時看不到輸出）。
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except (AttributeError, OSError):
    pass

try:
    import agent_config
except ImportError as e:
    raise SystemExit(
        "找不到 agent_config.py。請先 `cp agent_config.example.py agent_config.py` "
        "並填入 API key 跟 CPU/GPU model。"
    ) from e


HOME_BUTLER_API_KEY = agent_config.HOME_BUTLER_API_KEY
CPU_MODEL = agent_config.CPU_MODEL
GPU_MODEL = agent_config.GPU_MODEL
HOME_BUTLER_URL = getattr(agent_config, "HOME_BUTLER_URL", "https://home-butler.onrender.com")
LHM_URL = getattr(agent_config, "LHM_URL", "http://localhost:8085/data.json")
TICK_SECONDS = getattr(agent_config, "TICK_SECONDS", 60)
AUTO_UPDATE = getattr(agent_config, "AUTO_UPDATE", True)
LOG_PATH = getattr(
    agent_config,
    "LOG_PATH",
    os.path.join(os.path.expanduser("~"), "butler-agent.log"),
)

if not HOME_BUTLER_API_KEY:
    raise SystemExit("agent_config.HOME_BUTLER_API_KEY 是空的，請填上 home-butler 的 API key。")
if not CPU_MODEL or not GPU_MODEL:
    raise SystemExit("agent_config.CPU_MODEL / GPU_MODEL 必填（顯示用簡化型號，例如 'Xeon-1230v2'）。")


# ── Logging ──────────────────────────────────────────
# 兩個 handler：Rotating file（保 ~15MB 上限）+ stdout（前台跑時看得到）。
# 不再依賴外部 bat redirect，bat 維持單純 `python agent.py` 即可。
log = logging.getLogger("agent")
log.setLevel(logging.INFO)
log.propagate = False  # 避免被 root logger 重複輸出

_fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_file_handler = logging.handlers.RotatingFileHandler(
    LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_file_handler.setFormatter(_fmt)
log.addHandler(_file_handler)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(logging.Formatter("%(message)s"))  # terminal 不重複秀時間
log.addHandler(_stream_handler)


# ── Watchdog ─────────────────────────────────────────
# 主迴圈 5 分鐘沒新 tick = 認定某個 blocking call hang 住（pynvml 沒 timeout
# 機制、httpx 邊角情境、subprocess 孤兒孫程序等都可能）。
_WATCHDOG_STALE_THRESHOLD_S = 5 * 60
_last_tick_at = time.monotonic()
_watchdog_lock = threading.Lock()


def _watchdog():
    while True:
        time.sleep(30)
        with _watchdog_lock:
            stale = time.monotonic() - _last_tick_at
        if stale > _WATCHDOG_STALE_THRESHOLD_S:
            _restart_self(f"watchdog: no tick for {stale:.0f}s")


# ── Self-restart ─────────────────────────────────────
# 之前是 `os._exit(1)` 出去靠 Task Scheduler restart-on-failure 把 agent 拉
# 回來，但實測：（1）若使用者沒勾 restart-on-failure 或 3 次 attempt 用完，
# agent 永久死到下次 OnStart trigger（=重開機）；（2）即使勾了，每次 git
# auto-update 都在賭一次 Task Scheduler 設定還活著 — 太脆。
#
# 改成 agent 自己 spawn 一個 detached 新 process 再乾淨 exit(0)。Task
# Scheduler 看到原本 task 正常完成，新 process 以 orphan 身份活下去。
# Trade-off: 新 process 之後若意外死，Task Scheduler 就 catch 不到了 —— 但
# 反正之前那條路本來就壞，現況嚴格更好。
def _restart_self(reason: str) -> None:
    log.info(f"[restart] {reason}; spawning detached new process")
    for h in log.handlers:
        try:
            h.flush()
        except Exception:
            pass

    DETACHED_PROCESS = 0x00000008
    creationflags = 0
    if sys.platform == "win32":
        creationflags = DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        os._exit(0)
    except Exception as e:
        # spawn 失敗（極少見：路徑壞、權限等）退回 _exit(1) 給 Task Scheduler
        # 最後一搏。比靜默死好。
        log.error(f"[restart] spawn failed: {e}, fall back to _exit(1)")
        os._exit(1)


def detect_local_ip() -> str:
    """拿到 PC 在區網的 IP（OS 路由表會走的那條 NIC）。
    用 UDP connect trick：不真的送 packet，只讀 OS 會用哪個 source IP。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


HOSTNAME = socket.gethostname()
# 注意：IP 改成每 tick 重抓（見 collect_payload），不再 cache 在 module scope。
# DHCP lease renewal / router 重啟 / 切 NIC 都會換 IP，cache 住 startup 時抓到
# 的舊 IP 會讓 server 端把同一台 PC 認成「舊 PC 失聯、新 PC 上線無歷史」。

# ── Auto-update ──────────────────────────────────────
# 每 UPDATE_CHECK_TICKS 個 tick（預設 60 ticks ≈ 1 小時）跑一次：
#   git fetch → 比對 HEAD vs origin/main → 不同就 git pull → py_compile 驗 →
#   _restart_self()（自己 spawn detached 新 process + exit(0)）
# 不靠 Task Scheduler restart-on-fail，因為實測那條路太脆（restart attempt
# 用完／使用者沒勾／設定漂移都會讓 agent 永久死，要等下次重開機）。
#
# AUTO_UPDATE=False 可關（agent_config.py），用來在你 push 壞 code 時暫停
# 推送到所有 PC 拉新版。
UPDATE_CHECK_TICKS = 60
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args, timeout=30) -> str:
    """Run git command in repo, return stdout. Raises CalledProcessError on failure."""
    return subprocess.check_output(
        ["git", *args], cwd=_REPO_DIR, timeout=timeout,
    ).decode().strip()


def get_current_sha() -> str:
    try:
        return _git("rev-parse", "HEAD", timeout=5)[:7]
    except Exception:
        return "unknown"


def _new_agent_py_compiles() -> bool:
    """pull 完先 py_compile 驗 syntax。新 code SyntaxError 直接 abort restart，
    舊 process 繼續跑 in-memory 舊版，比 spawn 一個瞬間就死的新 process 好。
    抓不到 ImportError／runtime bug，但 syntax error 是最大宗 breakage。"""
    try:
        import py_compile
        py_compile.compile(os.path.abspath(__file__), doraise=True)
        return True
    except Exception as e:
        log.error(f"[update] new agent.py compile failed: {e}")
        return False


def check_for_updates() -> bool:
    """origin/main 有新 commit 就 git pull、回 True（caller 應呼叫 _restart_self）。
    失敗（網路掛、merge conflict、新 code syntax error 等）log 後回 False，agent 繼續跑舊版。"""
    if not AUTO_UPDATE:
        return False
    try:
        _git("fetch", "origin", "main")
        current = _git("rev-parse", "HEAD", timeout=5)
        remote = _git("rev-parse", "origin/main", timeout=5)
        if current == remote:
            return False
        _git("pull", "origin", "main")
        if not _new_agent_py_compiles():
            log.error(f"[update] {current[:7]} → {remote[:7]} pulled but new agent.py won't compile; staying on old code")
            return False
        log.info(f"[update] {current[:7]} → {remote[:7]}, restarting")
        return True
    except Exception as e:
        log.info(f"[update] check failed: {e}")
        return False


def read_cpu_temp_from_lhm() -> float | None:
    """從 LibreHardwareMonitor web server 拿 CPU 溫度。
    優先 `CPU Package`，沒有 fallback `Core Max`，再沒有回 None。"""
    try:
        r = httpx.get(LHM_URL, timeout=2.0)
        data = r.json()
    except Exception as e:
        log.info(f"[lhm] {e}")
        return None

    def find_cpu_temps_node(root):
        """DFS 找 CPU 節點（Text 含 Intel/AMD 型號），回它的 Temperatures 子樹。"""
        stack = [root]
        while stack:
            node = stack.pop()
            text = str(node.get("Text", ""))
            if ("Intel" in text or "AMD" in text) and (
                "Xeon" in text or "Core" in text or "Ryzen" in text or "Threadripper" in text
            ):
                for sub in node.get("Children", []):
                    if sub.get("Text") == "Temperatures":
                        return sub
            for c in node.get("Children", []):
                stack.append(c)
        return None

    cpu_temps = find_cpu_temps_node(data)
    if cpu_temps is None:
        return None

    # Build leaf name → value dict, then pick by priority.
    # 不同 CPU 廠牌的 sensor 命名差很多：Intel Xeon/Core 用 "CPU Package"、
    # AMD Ryzen Zen 2+ 用 "Core (Tctl/Tdie)"、舊版 Zen 用 "Core (Tdie)" 等。
    leaves: dict[str, float] = {}
    for leaf in cpu_temps.get("Children", []):
        v = leaf.get("Value", "")
        if "°C" not in v:
            continue
        try:
            leaves[leaf.get("Text", "")] = float(v.replace("°C", "").strip())
        except ValueError:
            continue

    for key in (
        "CPU Package",          # Intel modern
        "Core (Tctl/Tdie)",     # AMD Ryzen Zen 2+ (含 7000 系列)
        "Core (Tdie)",          # AMD Ryzen 較舊版
        "CPU Cores",            # 某些主機板 generic
        "Core Max",             # Intel fallback
    ):
        if key in leaves:
            return leaves[key]
    return None


def read_gpu_via_pynvml() -> dict:
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            return {"gpu_pct": float(util.gpu), "gpu_temp_c": float(temp)}
        finally:
            pynvml.nvmlShutdown()
    except Exception as e:
        log.info(f"[gpu] {e}")
        return {}


def read_fah_via_lufah() -> dict | None:
    try:
        result = subprocess.run(
            ["lufah", "state"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.info(f"[fah] lufah rc={result.returncode}: {result.stderr.strip()}")
            return None
        out = result.stdout
        i = out.find("{")  # 跳過開頭的 DeprecationWarning
        if i < 0:
            return None
        data = json.loads(out[i:])
    except subprocess.TimeoutExpired:
        log.info("[fah] lufah timeout")
        return None
    except FileNotFoundError:
        log.info("[fah] lufah not installed")
        return None
    except Exception as e:
        log.info(f"[fah] {e}")
        return None

    group = data.get("groups", {}).get("", {})
    cfg = group.get("config", {})
    units = data.get("units", []) or []
    progress = None
    if units:
        u = units[0]
        for key in ("progress", "percentdone", "percent_done"):
            if key in u:
                try:
                    progress = float(u[key])
                except (TypeError, ValueError):
                    pass
                break

    return {
        "paused": bool(cfg.get("paused", False)),
        "finish": bool(cfg.get("finish", False)),
        "units_count": len(units),
        "progress_pct": progress,
    }


def collect_payload() -> dict:
    # 逐步計時，任一 collector > 3s 才印（平常各 < 1s）。等下次 hang 前留下
    # 哪個 collector 開始拖慢的證據，方便鎖定真兇（pynvml/lhm/lufah 都嫌疑）。
    timings: dict[str, float] = {}

    t = time.monotonic()
    cpu_pct = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory()
    timings["psutil"] = time.monotonic() - t

    t = time.monotonic()
    cpu_temp = read_cpu_temp_from_lhm()
    timings["lhm"] = time.monotonic() - t

    t = time.monotonic()
    gpu = read_gpu_via_pynvml()
    timings["gpu"] = time.monotonic() - t

    t = time.monotonic()
    fah = read_fah_via_lufah()
    timings["fah"] = time.monotonic() - t

    if max(timings.values()) > 3.0:
        log.warning(
            "[collect slow] " + " ".join(f"{k}={v:.1f}s" for k, v in timings.items())
        )

    return {
        "ip": detect_local_ip(),  # 每 tick 重抓，DHCP 換 IP 才不會卡舊值
        "hostname": HOSTNAME,
        "cpu_model": CPU_MODEL,
        "gpu_model": GPU_MODEL,
        "cpu_pct": float(cpu_pct),
        "ram_pct": float(mem.percent),
        "cpu_temp_c": cpu_temp,
        "fah": fah,
        **gpu,
    }


def push(payload: dict) -> None:
    """POST 一筆 heartbeat。網路錯誤／timeout 最多 retry 2 次（backoff 2s/4s）。
    HTTP 4xx/5xx 視為終局回應不 retry——讓 caller 看 log 自己決定處理。

    Timeout 從 15s 改 30s：home-butler 部署在 Render free，閒置 15 分鐘會 spin-down，
    下次 request 觸發 cold start 約 30-60s 才回。原本 15s timeout 必失敗、整個 tick
    丟掉，連續 2-3 tick 失敗就跨過 server 端 OFFLINE_THRESHOLD（300s）變失聯。
    現在 30s × 3 attempts 期望接得住絕大部分 cold-start 情境。"""
    url = f"{HOME_BUTLER_URL.rstrip('/')}/api/computers/heartbeat"
    headers = {"X-API-Key": HOME_BUTLER_API_KEY}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=30.0)
            if r.status_code >= 300:
                log.info(f"[push] HTTP {r.status_code}: {r.text[:200]}")
            else:
                fah = payload.get("fah") or {}
                log.info(
                    f"[push] ok cpu={payload['cpu_pct']}% gpu={payload.get('gpu_pct')}% "
                    f"cpu_t={payload.get('cpu_temp_c')}C gpu_t={payload.get('gpu_temp_c')}C "
                    f"fah_paused={fah.get('paused', 'n/a')}"
                )
            return  # HTTP 回應拿到了（含 4xx/5xx）就算結束，不 retry
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s
    log.info(f"[push] failed after 3 attempts: {last_err}")


def main():
    global _last_tick_at
    log.info(
        f"agent start: {HOSTNAME} ({detect_local_ip()}) sha={get_current_sha()} "
        f"auto_update={AUTO_UPDATE} → {HOME_BUTLER_URL}  log={LOG_PATH}"
    )
    threading.Thread(target=_watchdog, daemon=True).start()
    tick_count = 0
    while True:
        t0 = time.monotonic()
        try:
            push(collect_payload())
        except Exception as e:
            log.info(f"[tick] {e}")
        with _watchdog_lock:
            _last_tick_at = time.monotonic()
        # 每 UPDATE_CHECK_TICKS 個 tick check 一次 origin/main，有新 commit 就 _exit(1)
        # 讓 Task Scheduler 重啟 process 拉新 code
        tick_count += 1
        if tick_count % UPDATE_CHECK_TICKS == 0:
            if check_for_updates():
                _restart_self("auto-update pulled new code")
        elapsed = time.monotonic() - t0
        time.sleep(max(1.0, TICK_SECONDS - elapsed))


if __name__ == "__main__":
    main()
