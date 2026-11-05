import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

# No host telemetry or optional psutil installation needed in backend CI.
spec = importlib.util.spec_from_file_location('macos_metrics', Path(__file__).parents[1] / 'macos_metrics.py')
metrics = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'psutil': types.SimpleNamespace()}):
    spec.loader.exec_module(metrics)


class MetricsTests(unittest.TestCase):
    def test_driver_counter_and_unavailable(self):
        for value, expected in [(0, 0), (42, 42), (100, 100), (-1, None), (101, None),
                                (True, None), ('42', None), (float('nan'), None)]:
            self.assertEqual(metrics.gpu_percentage([{'PerformanceStatistics': {'Device Utilization %': value}}]), expected)
        self.assertIsNone(metrics.gpu_percentage([{'PerformanceStatistics': {'Renderer Utilization %': 50}}]))
        self.assertEqual(metrics.gpu_percentage({'IORegistryEntryChildren': [{'PerformanceStatistics': {'Device Utilization %': 0}}]}), 0)

    def test_payload_keeps_temperature_unknown_and_zero_gpu(self):
        fake = types.SimpleNamespace(cpu_percent=lambda interval: 12, virtual_memory=lambda: types.SimpleNamespace(percent=40))
        with patch.object(metrics, 'psutil', fake), patch.object(metrics.platform, 'system', return_value='Darwin'), patch.object(metrics, 'cpu_model', return_value='Apple M6'), patch.object(metrics, 'read_smc', return_value=({'tcmb_c': 44.5, 'tcmz_c': None}, 6.25)), patch.object(metrics, 'read_memory_pressure', return_value={'level': 'normal', 'pct': 37}), patch.object(metrics, 'read_gpu', return_value=0):
            result = metrics.collect('192.0.2.20', 'Mac mini')
        self.assertEqual(result['gpu_pct'], 0)
        self.assertIsNone(result['cpu_temp_c'])
        self.assertIsNone(result['gpu_temp_c'])
        self.assertNotIn('load_average_1_5_15', result)
        self.assertEqual(result['cpu_pct'], 12)
        self.assertEqual(result['system_power_w'], 6.25)

    @patch.object(metrics, "read_memory_pressure_pct", return_value=37)
    def test_memory_pressure_flags_and_unavailable(self, _pct):
        for raw, expected in [('1\n', 'normal'), ('2', 'warning'), ('4', 'critical'),
                              ('0', None), ('3', None), ('8', None), ('50%', None), ('', None)]:
            with patch.object(metrics.subprocess, 'run', return_value=types.SimpleNamespace(stdout=raw)) as command:
                self.assertEqual(metrics.read_memory_pressure(), {'level': expected, 'pct': 37})
                self.assertEqual(command.call_args.args[0], ['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'])
        for error in [OSError('denied'), metrics.subprocess.TimeoutExpired('sysctl', 5)]:
            with patch.object(metrics.subprocess, 'run', side_effect=error):
                self.assertEqual(metrics.read_memory_pressure(), {'level': None, 'pct': 37})

    def test_continuous_pressure_uses_kernel_value_and_fails_unknown(self):
        for free, code, expected in [(100, 0, 0), (63, 0, 37), (0, 0, 100),
                                     (101, 0, None), (2**32-1, 0, None), (63, -1, None)]:
            def query(pointer):
                pointer._obj.value = free
                return code
            with patch.object(metrics.ctypes, 'CDLL', return_value=types.SimpleNamespace(memorystatus_get_level=query)):
                self.assertEqual(metrics.read_memory_pressure_pct(), expected)
        for error in [OSError('unavailable'), AttributeError('unsupported')]:
            with patch.object(metrics.ctypes, 'CDLL', side_effect=error):
                self.assertIsNone(metrics.read_memory_pressure_pct())
        def unwritten(pointer):
            return 0
        with patch.object(metrics.ctypes, 'CDLL', return_value=types.SimpleNamespace(memorystatus_get_level=unwritten)):
            self.assertIsNone(metrics.read_memory_pressure_pct())

    def test_smc_child_invalid_values_and_timeout_fail_closed(self):
        good = types.SimpleNamespace(stdout=json.dumps({'sensors': {'TCMb': {'temperature_c': 44.5}, 'TCMz': {'temperature_c': None}}}))
        with patch.object(metrics.subprocess, 'run', return_value=good):
            self.assertEqual(metrics.read_smc_temperature(), {'tcmb_c': 44.5, 'tcmz_c': None})
        for invalid in [True, '44', 0, 151]:
            bad = types.SimpleNamespace(stdout=json.dumps({'sensors': {'TCMb': {'temperature_c': invalid}, 'TCMz': {'temperature_c': None}}}))
            with patch.object(metrics.subprocess, 'run', return_value=bad):
                self.assertEqual(metrics.read_smc_temperature(), {'tcmb_c': None, 'tcmz_c': None})
        with patch.object(metrics.subprocess, 'run', side_effect=metrics.subprocess.TimeoutExpired('reader', 5)):
            self.assertEqual(metrics.read_smc_temperature(), {'tcmb_c': None, 'tcmz_c': None})

    def test_system_power_is_read_with_the_temperatures_and_fails_to_none(self):
        def child(power):
            return types.SimpleNamespace(stdout=json.dumps({'sensors': {'TCMb': {'temperature_c': 44.5}, 'TCMz': {'temperature_c': None}}, **power}))
        with patch.object(metrics.subprocess, 'run', return_value=child({'power': {'PSTR': {'watts': 3.24}}})):
            self.assertEqual(metrics.read_smc(), ({'tcmb_c': 44.5, 'tcmz_c': None}, 3.24))
        # An older probe without power, or an impossible reading, keeps the temperature and drops only the power.
        for power in [{}, {'power': None}, {'power': {'PSTR': {'watts': None}}}, {'power': {'PSTR': {'watts': 0}}},
                      {'power': {'PSTR': {'watts': 501}}}, {'power': {'PSTR': {'watts': True}}}, {'power': {'PSTR': {'watts': '3'}}}]:
            with patch.object(metrics.subprocess, 'run', return_value=child(power)):
                self.assertEqual(metrics.read_smc(), ({'tcmb_c': 44.5, 'tcmz_c': None}, None), power)

    def test_endpoint_and_credential_boundaries(self):
        for url in ['http://example.test', 'https://user:secret@example.test', 'https://example.test/path',
                    'https://example.test?key=x', 'https://example.test#x', 'http://localhost:1234']:
            with self.assertRaises(ValueError):
                metrics.endpoint(url)
        with self.assertRaises(ValueError):
            metrics.send({}, 'https://example.test', '')
        with self.assertRaises(ValueError):
            metrics.send({}, 'http://127.0.0.1:1234', 'secret')

    def test_default_has_no_network(self):
        payload = {'cpu_pct': 1, 'cpu_temp_c': None}
        with patch.object(metrics, 'collect', return_value=payload), patch.object(metrics, 'send') as send, patch.object(metrics, 'psutil', types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(total=24))), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(metrics.main(['--ip', '192.0.2.20']), 0)
        send.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['heartbeat'], payload)

    def test_local_receiver_and_redirect_rejected(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            redirect = False
            def do_POST(self):
                seen.append((self.path, self.headers.get('X-API-Key'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(302 if self.redirect else 200)
                if self.redirect:
                    self.send_header('Location', '/should-not-follow')
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            metrics.send({'gpu_pct': None}, url, '')
            self.assertEqual(seen, [('/v2/telemetry/heartbeat', None, {'gpu_pct': None})])  # the rebuilt backend is tried first
            Handler.redirect = True
            with self.assertRaises(metrics.urllib.error.HTTPError):
                metrics.send({}, url, '')
            self.assertEqual(len(seen), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
