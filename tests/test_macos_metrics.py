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
spec = importlib.util.spec_from_file_location('macos_metrics', Path(__file__).parents[1] / 'agent/macos_metrics.py')
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
        with patch.object(metrics, 'psutil', fake), patch.object(metrics.platform, 'system', return_value='Darwin'), patch.object(metrics, 'cpu_model', return_value='Apple M6'), patch.object(metrics, 'read_smc_temperature', return_value={'tcmb_c': 44.5, 'tcmz_c': None}), patch.object(metrics, 'read_memory_pressure', return_value={'level': 'normal'}), patch.object(metrics, 'read_gpu', return_value=0):
            result = metrics.collect('192.0.2.20', 'Mac mini')
        self.assertEqual(result['gpu_pct'], 0)
        self.assertIsNone(result['cpu_temp_c'])
        self.assertIsNone(result['gpu_temp_c'])
        self.assertNotIn('load_average_1_5_15', result)
        self.assertEqual(result['cpu_pct'], 12)

    def test_memory_pressure_flags_and_unavailable(self):
        for raw, expected in [('1\n', 'normal'), ('2', 'warning'), ('4', 'critical'),
                              ('0', None), ('3', None), ('8', None), ('50%', None), ('', None)]:
            with patch.object(metrics.subprocess, 'run', return_value=types.SimpleNamespace(stdout=raw)) as command:
                self.assertEqual(metrics.read_memory_pressure(), {'level': expected})
                self.assertEqual(command.call_args.args[0], ['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'])
        for error in [OSError('denied'), metrics.subprocess.TimeoutExpired('sysctl', 5)]:
            with patch.object(metrics.subprocess, 'run', side_effect=error):
                self.assertEqual(metrics.read_memory_pressure(), {'level': None})

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
            self.assertEqual(seen, [('/api/computers/heartbeat', None, {'gpu_pct': None})])
            Handler.redirect = True
            with self.assertRaises(metrics.urllib.error.HTTPError):
                metrics.send({}, url, '')
            self.assertEqual(len(seen), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class ExistingContractTests(unittest.TestCase):
    def test_collector_fits_real_request_and_state_without_sheets(self):
        import ast
        from typing import Optional, Literal
        from pydantic import BaseModel, Field
        root = Path(__file__).parents[1]
        tree = ast.parse((root / 'web_api.py').read_text())
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name in ('FAHStatus', 'SMCTemperature', 'MemoryPressure', 'PCHeartbeatRequest')]
        namespace = {'BaseModel': BaseModel, 'Optional': Optional, 'Literal': Literal, 'Field': Field}
        exec(compile(ast.Module(body=classes, type_ignores=[]), 'web_api.py', 'exec'), namespace)
        state_spec = importlib.util.spec_from_file_location('isolated_pc_state', root / 'pc_state.py')
        state = importlib.util.module_from_spec(state_spec)
        with patch.dict(sys.modules, {'gspread': types.SimpleNamespace(),
                                     'sheets': types.SimpleNamespace(_get_spreadsheet=lambda: None)}):
            state_spec.loader.exec_module(state)
        fake = types.SimpleNamespace(cpu_percent=lambda interval: 12, virtual_memory=lambda: types.SimpleNamespace(percent=40))
        with patch.object(metrics, 'psutil', fake), patch.object(metrics.platform, 'system', return_value='Darwin'), patch.object(metrics, 'cpu_model', return_value='Apple M6'), patch.object(metrics, 'read_smc_temperature', return_value={'tcmb_c': 44.5, 'tcmz_c': None}), patch.object(metrics, 'read_memory_pressure', return_value={'level': 'normal'}), patch.object(metrics, 'read_gpu', return_value=None):
            payload = metrics.collect('192.0.2.20', 'Mac mini')
        request = namespace['PCHeartbeatRequest'](**payload)
        # Real record/snapshot; prevent spawning the Sheet writer entirely.
        with patch.object(state.threading, 'Thread') as thread:
            state.record_heartbeat(request.model_dump())
            self.assertTrue(thread.called)
        pc = state.snapshot()['192.0.2.20']
        self.assertTrue(pc['online'])
        self.assertEqual(pc['hostname'], 'Mac mini')
        self.assertEqual(pc['current']['memory_pressure'], {'level': 'normal'})
        self.assertEqual(pc['history'][0]['memory_pressure'], {'level': 'normal'})
        self.assertIsNone(namespace['PCHeartbeatRequest'](**{k: v for k, v in payload.items() if k != 'memory_pressure'}).memory_pressure)
        for invalid in [True, 0, 1, 2, 4, 'green', '50', 'unknown']:
            with self.assertRaises(ValueError):
                namespace['MemoryPressure'](level=invalid)
        with self.assertRaises(ValueError):
            namespace['MemoryPressure'](level='normal', percent=50)
        self.assertEqual(pc['current']['cpu_pct'], 12)
        self.assertIsNone(pc['current']['gpu_pct'])
        self.assertIsNone(pc['history'][0]['cpu_temp_c'])
        self.assertEqual(pc['history'][0]['smc_temperature'], {'tcmb_c': 44.5, 'tcmz_c': None})
        for invalid in [0, -1, 151, True, '44', float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                namespace['SMCTemperature'](tcmb_c=invalid)
        with self.assertRaises(ValueError):
            namespace['SMCTemperature'](other=40)


if __name__ == '__main__':
    unittest.main()
