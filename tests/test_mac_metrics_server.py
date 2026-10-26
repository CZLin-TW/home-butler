"""The Mac metrics service for Home Assistant: answers GET /metrics to allowed addresses only.
No psutil, no real sampling: the collector is faked."""
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import unittest
import urllib.error
import urllib.request

spec = importlib.util.spec_from_file_location("mac_metrics_server", Path(__file__).parents[1] / "agent/mac_metrics_server.py")
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class MetricsServerTests(unittest.TestCase):
    def start(self, allowed, extras=None, run=None):
        self.samples = 0

        def collect():
            self.samples += 1
            return {"ip": "192.0.2.10", "hostname": "電腦一", "cpu_pct": 12.5, "ram_pct": 40.0}
        self.clock = Clock()
        self.sampler = server_module.Sampler(collect, extras or {}, clock=self.clock, run=run or subprocess.run)
        server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), server_module.make_handler(self.sampler, allowed))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def get(self, path="/metrics", method="GET"):
        request = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as answer:
                return answer.status, json.loads(answer.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, None

    def test_the_report_and_its_reuse_for_30_seconds(self):
        self.start({"127.0.0.1"})
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body["heartbeat"]["hostname"], "電腦一")
        self.assertEqual((body["extras"], body["sampled_at"]), ({}, 1000))
        self.get()
        self.assertEqual(self.samples, 1)
        self.clock.now += 31
        self.get()
        self.assertEqual(self.samples, 2)

    def test_only_allowed_addresses_and_only_get_metrics(self):
        self.start({"192.0.2.99"})
        self.assertEqual(self.get()[0], 403)
        self.assertEqual(self.samples, 0)
        self.start({"127.0.0.1"})
        for path, method in (("/", "GET"), ("/metrics/x", "GET"), ("/metrics?x=1", "GET"), ("/metrics", "POST"), ("/metrics", "DELETE")):
            self.assertIn(self.get(path, method)[0], (404, 405), (path, method))
        self.assertEqual(self.samples, 0)

    def test_extras_are_passed_on_and_a_broken_one_is_null(self):
        def run(command, **kwargs):
            if command[0] == "ok":
                return subprocess.CompletedProcess(command, 0, '[{"name": "funnel", "healthy": true}]', "")
            return subprocess.CompletedProcess(command, 1, "", "boom")
        self.start({"127.0.0.1"}, {"watchdogs": ["ok"], "broken": ["fail"]}, run)
        body = self.get()[1]
        self.assertEqual(body["extras"], {"watchdogs": [{"name": "funnel", "healthy": True}], "broken": None})

    def test_a_failed_sample_is_503_not_old_data(self):
        self.start({"127.0.0.1"})
        self.assertEqual(self.get()[0], 200)
        def broken():
            raise RuntimeError("no sample")
        self.sampler.collect = broken
        self.clock.now += 31
        self.assertEqual(self.get()[0], 503)


if __name__ == "__main__":
    unittest.main()
