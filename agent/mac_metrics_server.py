"""Read-only Mac metrics for Home Assistant to fetch (the one way between home and the cloud is HA's link
to HomeButler; see docs/local-hub-architecture.md).

The Mac used to send its health straight to HomeButler with a key of its own, which needed a signed
sender, a System Keychain item and a root auto-updater. Now Home Assistant asks for it on the home
network and passes it on over its existing link. This answers only `GET /metrics`, only to the addresses
given with --allow, holds no key and controls nothing.

  GET /metrics -> {"heartbeat": <the same report as before>, "extras": {name: <JSON from a command>}, "sampled_at": <epoch>}

A sample is reused for up to CACHE_SECONDS so a burst of requests does not sample again each time.
Usage (stdlib plus psutil, which macos_metrics needs):
  python mac_metrics_server.py --listen 192.168.x.y:8792 --allow 192.168.x.z --ip 192.168.x.y \
      --hostname "Mac mini" [--extra watchdogs="/path/to/status --json"]
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CACHE_SECONDS = 30
EXTRA_TIMEOUT = 10
log = logging.getLogger("mac-metrics")


class Sampler:
    def __init__(self, collect, extras: dict[str, list[str]], clock=time.time, run=subprocess.run):
        self.collect, self.extras, self.clock, self.run = collect, extras, clock, run
        self.lock = threading.Lock()
        self.cached: tuple[float, dict] | None = None

    def _extra(self, command: list[str]):
        try:
            result = self.run(command, capture_output=True, text=True, timeout=EXTRA_TIMEOUT, stdin=subprocess.DEVNULL)
            return json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def sample(self) -> dict:
        with self.lock:
            now = self.clock()
            if self.cached and now - self.cached[0] < CACHE_SECONDS:
                return self.cached[1]
            report = {"heartbeat": self.collect(), "extras": {name: self._extra(command) for name, command in self.extras.items()},
                      "sampled_at": int(now)}
            self.cached = (now, report)
            return report


def make_handler(sampler: Sampler, allowed: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mac-metrics"
        sys_version = ""

        def log_message(self, *args):
            pass

        def _reply(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            if self.client_address[0] not in allowed:
                return self._reply(403)
            if self.path != "/metrics":
                return self._reply(404)
            try:
                body = json.dumps(sampler.sample(), ensure_ascii=False).encode()
            except Exception as error:          # a failed sample is said, not hidden behind old data
                log.warning("sample failed: %s", type(error).__name__)
                return self._reply(503)
            self._reply(200, body, "application/json")

        def _refuse(self):
            self._reply(403 if self.client_address[0] not in allowed else 405)

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse

    return Handler


def parse_extra(text: str) -> tuple[str, list[str]]:
    name, _, command = text.partition("=")
    if not name.isidentifier() or not command.strip():
        raise argparse.ArgumentTypeError("--extra NAME=COMMAND")
    return name, shlex.split(command)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", required=True, help="IP:PORT on the home network (never 0.0.0.0)")
    parser.add_argument("--allow", action="append", required=True, help="address allowed to ask (Home Assistant)")
    parser.add_argument("--ip", required=True, help="this Mac's stable LAN address, the computer card's identity")
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--extra", action="append", default=[], type=parse_extra)
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    address = ipaddress.ip_address(host)
    if address.is_unspecified or not (address.is_private or address.is_loopback):
        parser.error("--listen must be a private or loopback address")
    allowed = {str(ipaddress.ip_address(a)) for a in args.allow}
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import macos_metrics                        # needs psutil; imported here so tests can run without it
    sampler = Sampler(lambda: macos_metrics.collect(args.ip, args.hostname), dict(args.extra))
    server = ThreadingHTTPServer((host, int(port)), make_handler(sampler, allowed))
    log.info("serving /metrics on %s to %s", args.listen, ", ".join(sorted(allowed)))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
