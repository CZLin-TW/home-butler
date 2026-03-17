"""
Theater Agent API 封裝模組
- 透過家裡 PC 上的 Theater Agent 控制劇院設備
- Marantz Cinema 50 / KEF LS60 / KEF LSX II
"""

import httpx
import os

THEATER_AGENT_URL = os.environ.get("THEATER_AGENT_URL", "")
THEATER_AGENT_KEY = os.environ.get("THEATER_AGENT_KEY", "")
REQUEST_TIMEOUT = 30  # 劇院開關涉及多台設備，給足時間


def _headers():
    return {"X-Api-Key": THEATER_AGENT_KEY}


def _get(path: str) -> dict:
    """呼叫 Theater Agent API"""
    if not THEATER_AGENT_URL:
        return {"error": "THEATER_AGENT_URL 未設定"}
    try:
        url = f"{THEATER_AGENT_URL}{path}"
        resp = httpx.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
        return resp.json()
    except httpx.TimeoutException:
        return {"error": "Theater Agent 連線逾時，請確認家裡 PC 是否在線"}
    except httpx.ConnectError:
        return {"error": "無法連線到 Theater Agent，請確認家裡 PC 和網路是否正常"}
    except Exception as e:
        return {"error": str(e)}


# ── 劇院模式 ──

def theater_on() -> dict:
    """開啟劇院模式（三台設備全開）"""
    return _get("/theater/on")


def theater_off() -> dict:
    """關閉劇院模式（三台設備全關）"""
    return _get("/theater/off")


def theater_status() -> dict:
    """查詢劇院所有設備狀態"""
    return _get("/theater/status")


# ── 個別設備控制 ──

def marantz_power(action: str) -> dict:
    return _get(f"/marantz/power/{action}")


def marantz_input(source: str) -> dict:
    return _get(f"/marantz/input/{source}")


def marantz_status() -> dict:
    return _get("/marantz/status")


def ls60_power(action: str, source: str = None) -> dict:
    path = f"/ls60/power/{action}"
    if source:
        path += f"?source={source}"
    return _get(path)


def ls60_source(source: str) -> dict:
    return _get(f"/ls60/source/{source}")


def ls60_status() -> dict:
    return _get("/ls60/status")


def lsx2_power(action: str, source: str = None) -> dict:
    path = f"/lsx2/power/{action}"
    if source:
        path += f"?source={source}"
    return _get(path)


def lsx2_source(source: str) -> dict:
    return _get(f"/lsx2/source/{source}")


def lsx2_status() -> dict:
    return _get("/lsx2/status")


# ── 格式化狀態 ──

def format_theater_status(data: dict) -> str:
    """將劇院狀態格式化為人類可讀文字"""
    if "error" in data:
        return f"❌ 無法取得劇院狀態：{data['error']}"

    lines = ["🎬 劇院設備狀態"]

    # Marantz
    m = data.get("marantz", {})
    if m.get("power") == "on":
        lines.append(f"  🔊 Marantz Cinema 50：開啟｜輸入：{m.get('source', '?')}｜音量：{m.get('volume', '?')}")
    elif m.get("power") == "off":
        lines.append(f"  🔇 Marantz Cinema 50：關閉")
    else:
        lines.append(f"  ⚠️ Marantz Cinema 50：{m.get('error', '無法取得')}")

    # LS60
    ls = data.get("ls60", {})
    if ls.get("power") == "on":
        lines.append(f"  🔊 KEF LS60：開啟｜輸入：{ls.get('source', '?')}｜音量：{ls.get('volume', '?')}")
    elif ls.get("power") == "off":
        lines.append(f"  🔇 KEF LS60：關閉")
    else:
        lines.append(f"  ⚠️ KEF LS60：{ls.get('error', '無法取得')}")

    # LSX II
    lx = data.get("lsx2", {})
    if lx.get("power") == "on":
        lines.append(f"  🔊 KEF LSX II：開啟｜輸入：{lx.get('source', '?')}｜音量：{lx.get('volume', '?')}")
    elif lx.get("power") == "off":
        lines.append(f"  🔇 KEF LSX II：關閉")
    else:
        lines.append(f"  ⚠️ KEF LSX II：{lx.get('error', '無法取得')}")

    return "\n".join(lines)
