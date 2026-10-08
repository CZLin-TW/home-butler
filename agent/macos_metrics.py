"""Read-only macOS telemetry. Default: one local JSON sample, no network.

Independent of agent.py, vision, theater, HA and auto-update. See macos.md.
"""
from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import math
import os
import platform
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import psutil


def percentage(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 100 else None


def gpu_percentage(registry):
    """Only the driver's aggregate device counter, never renderer/tiler or temperature."""
    if isinstance(registry, dict):
        stats = registry.get("PerformanceStatistics", {})
        if isinstance(stats, dict):
            value = percentage(stats.get("Device Utilization %"))
            if value is not None:
                return value
        children = registry.get("IORegistryEntryChildren", [])
        return gpu_percentage(children)
    if isinstance(registry, list):
        for child in registry:
            value = gpu_percentage(child)
            if value is not None:
                return value
    return None


def read_gpu():
    try:
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-a", "-r", "-c", "AGXAccelerator"],
            capture_output=True, check=True, timeout=5,
        )
        return gpu_percentage(plistlib.loads(result.stdout))
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
        return None


def cpu_model():
    result = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
        capture_output=True, check=True, timeout=5, text=True,
    )
    return result.stdout.strip()


def read_memory_pressure_pct():
    """Activity Monitor's continuous value: 100 - memorystatus_get_level().

    Read-only libSystem call, separate from severity and psutil RAM utilization.
    This private macOS interface can disappear; missing/error/out-of-range is null.
    Initialize outside the valid range to reject an unwritten success result.
    """
    try:
        query = ctypes.CDLL('/usr/lib/libSystem.B.dylib').memorystatus_get_level
        query.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        query.restype = ctypes.c_int
        free_pct = ctypes.c_uint(101)
        if query(ctypes.byref(free_pct)) == 0 and free_pct.value <= 100:
            return 100 - free_pct.value
    except (OSError, AttributeError):
        pass
    return None


def read_memory_pressure():
    """Read the kernel's dispatch pressure level, not a RAM-use percentage.

    XNU's sysctl converts internal levels to NOTE_MEMORYSTATUS flags (1/2/4).
    Missing/unsupported readings remain unknown; never manufacture 'normal'.
    """
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            capture_output=True, check=True, timeout=5, text=True,
        )
        level = {"1": "normal", "2": "warning", "4": "critical"}.get(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        level = None
    return {"level": level, "pct": read_memory_pressure_pct()}


def read_smc_temperature():
    # Separate bounded child works under the installed sender's Python -I mode.
    missing = {"tcmb_c": None, "tcmz_c": None}
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("macos_temperature.py"))],
            capture_output=True, check=True, timeout=5, text=True,
        )
        sensors = json.loads(result.stdout)["sensors"]
        values = {name: sensors[key]["temperature_c"] for name, key in
                  (("tcmb_c", "TCMb"), ("tcmz_c", "TCMz"))}
        return {name: value if type(value) in (int, float) and math.isfinite(value)
                and 0 < value <= 150 else None for name, value in values.items()}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return missing


def collect(ip, hostname):
    if platform.system() != "Darwin":
        raise RuntimeError("macOS is required")
    cpu = percentage(psutil.cpu_percent(interval=1))
    ram = percentage(psutil.virtual_memory().percent)
    if cpu is None or ram is None:
        raise RuntimeError("CPU/RAM sample unavailable; heartbeat skipped")
    model = cpu_model()
    return {
        "ip": ip, "hostname": hostname, "cpu_model": model,
        "gpu_model": f"{model} integrated GPU" if model.startswith("Apple ") else "",
        "cpu_pct": cpu, "ram_pct": ram, "gpu_pct": read_gpu(),
        "cpu_temp_c": None, "gpu_temp_c": None, "fah": None,
        "smc_temperature": read_smc_temperature(),
        "memory_pressure": read_memory_pressure(),
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    # Explicit destination; no credentials in URLs, redirects, proxy discovery or fallback.
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("URL must be an origin without credentials, path, query or fragment")
    local = parsed.hostname in ("127.0.0.1", "::1")
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise ValueError("HTTPS required except literal loopback test addresses")
    # Validate malformed ports before opening a connection.
    _ = parsed.port
    return base_url.rstrip("/") + "/api/computers/heartbeat", local


def send(payload, base_url, key):
    url, local = endpoint(base_url)
    if not local and not key:
        raise ValueError("HOME_BUTLER_API_KEY required for remote send")
    if local and key:
        raise ValueError("Do not send a real API key to the local test receiver")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    request = urllib.request.Request(
        url, data=json.dumps(payload, allow_nan=False).encode(), headers=headers, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        if response.status != 200 or json.loads(response.read(4096)) != {"ok": True}:
            raise RuntimeError("Heartbeat acknowledgement invalid")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="Stable LAN IP used by existing backend as card identity")
    parser.add_argument("--hostname", default="Mac mini", help="Display name; no automatic hostname disclosure")
    parser.add_argument("--send", action="store_true", help="Explicitly send to --url; otherwise local JSON only")
    parser.add_argument("--url", help="Approved home-butler HTTPS origin, or literal HTTP loopback for tests")
    parser.add_argument("--watch", action="store_true", help="Foreground loop; no installation or auto-update")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        ipaddress.ip_address(args.ip)
        if args.interval < 60:
            raise ValueError("interval must be at least 60 seconds")
        if args.send != bool(args.url):
            raise ValueError("--send and --url must be supplied together")
        if args.send:
            _, local = endpoint(args.url)
            key = os.environ.get("HOME_BUTLER_API_KEY", "")
            if local and key or not local and not key:
                raise ValueError("Remote send requires a key; local tests require no key")
        else:
            key = ""
    except ValueError as error:
        parser.error(str(error))
    while True:
        started = time.monotonic()
        try:
            payload = collect(args.ip, args.hostname)
            if args.send:
                send(payload, args.url, key)
                print("heartbeat acknowledged", flush=True)
            else:
                print(json.dumps({"heartbeat": payload, "local_only": {
                    "load_average_1_5_15": os.getloadavg(),
                    "memory_total_bytes": psutil.virtual_memory().total,
                }}, allow_nan=False), flush=True)
        except Exception as error:
            # Never log request headers, credentials, response bodies or environment.
            print(f"sample/send failed ({type(error).__name__})", file=sys.stderr, flush=True)
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
