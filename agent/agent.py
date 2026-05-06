"""home-butler PC monitoring agent.

每 60 秒讀本機指標 → POST 到 home-butler /api/computers/heartbeat。

Setup：
    cp agent_config.example.py agent_config.py
    pip install -r requirements.txt
    python agent.py

詳細見同目錄 README.md。
"""

import json
import socket
import subprocess
import sys
import time

import httpx
import psutil

# 強制 stdout/stderr line-buffered。給 task scheduler / cron / nohup 之類 stdout
# 被 redirect 到檔案的部署情境：python 預設 block buffer (4KB)，每分鐘一行
# heartbeat 大概要 ~50 分鐘才 flush 一次，看 log 完全來不及。
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

if not HOME_BUTLER_API_KEY:
    raise SystemExit("agent_config.HOME_BUTLER_API_KEY 是空的，請填上 home-butler 的 API key。")
if not CPU_MODEL or not GPU_MODEL:
    raise SystemExit("agent_config.CPU_MODEL / GPU_MODEL 必填（顯示用簡化型號，例如 'Xeon-1230v2'）。")


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
THIS_PC_IP = detect_local_ip()


def read_cpu_temp_from_lhm() -> float | None:
    """從 LibreHardwareMonitor web server 拿 CPU 溫度。
    優先 `CPU Package`，沒有 fallback `Core Max`，再沒有回 None。"""
    try:
        r = httpx.get(LHM_URL, timeout=2.0)
        data = r.json()
    except Exception as e:
        print(f"[lhm] {e}")
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

    package = None
    core_max = None
    for leaf in cpu_temps.get("Children", []):
        v = leaf.get("Value", "")
        if "°C" not in v:
            continue
        try:
            num = float(v.replace("°C", "").strip())
        except ValueError:
            continue
        if leaf.get("Text") == "CPU Package":
            package = num
        elif leaf.get("Text") == "Core Max":
            core_max = num

    return package if package is not None else core_max


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
        print(f"[gpu] {e}")
        return {}


def read_fah_via_lufah() -> dict | None:
    try:
        result = subprocess.run(
            ["lufah", "state"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"[fah] lufah rc={result.returncode}: {result.stderr.strip()}")
            return None
        out = result.stdout
        i = out.find("{")  # 跳過開頭的 DeprecationWarning
        if i < 0:
            return None
        data = json.loads(out[i:])
    except subprocess.TimeoutExpired:
        print("[fah] lufah timeout")
        return None
    except FileNotFoundError:
        print("[fah] lufah not installed")
        return None
    except Exception as e:
        print(f"[fah] {e}")
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
    cpu_pct = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory()
    return {
        "ip": THIS_PC_IP,
        "hostname": HOSTNAME,
        "cpu_model": CPU_MODEL,
        "gpu_model": GPU_MODEL,
        "cpu_pct": float(cpu_pct),
        "ram_pct": float(mem.percent),
        "cpu_temp_c": read_cpu_temp_from_lhm(),
        "fah": read_fah_via_lufah(),
        **read_gpu_via_pynvml(),
    }


def push(payload: dict) -> None:
    try:
        r = httpx.post(
            f"{HOME_BUTLER_URL.rstrip('/')}/api/computers/heartbeat",
            json=payload,
            headers={"X-API-Key": HOME_BUTLER_API_KEY},
            timeout=15.0,
        )
        if r.status_code >= 300:
            print(f"[push] HTTP {r.status_code}: {r.text[:200]}")
        else:
            fah = payload.get("fah") or {}
            print(f"[push] ok cpu={payload['cpu_pct']}% gpu={payload.get('gpu_pct')}% "
                  f"cpu_t={payload.get('cpu_temp_c')}C gpu_t={payload.get('gpu_temp_c')}C "
                  f"fah_paused={fah.get('paused', 'n/a')}")
    except Exception as e:
        print(f"[push] {e}")


def main():
    print(f"agent start: {HOSTNAME} ({THIS_PC_IP}) → {HOME_BUTLER_URL}")
    while True:
        t0 = time.time()
        try:
            push(collect_payload())
        except Exception as e:
            print(f"[tick] {e}")
        elapsed = time.time() - t0
        time.sleep(max(1.0, TICK_SECONDS - elapsed))


if __name__ == "__main__":
    main()
