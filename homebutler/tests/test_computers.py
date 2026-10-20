from datetime import timedelta

from fastapi.testclient import TestClient

from homebutler.app import create_app
from homebutler.domain import computers, tokens
from homebutler.settings import Settings
from homebutler.store.models import Computer, ComputerMetric, utcnow
from homebutler.tests.helpers import StoreTest

BEAT = {"ip": "10.0.0.5", "hostname": "Mac mini", "cpu_model": "Apple M6", "cpu_pct": 12, "ram_pct": 40, "gpu_pct": None,
        "cpu_temp_c": None, "gpu_temp_c": None, "fah": None, "smc_temperature": {"tcmb_c": 44.5, "tcmz_c": None},
        "system_power_w": 3.2, "memory_pressure": {"level": "normal", "pct": 37}}


class TelemetryTests(StoreTest):
    def setUp(self):
        super().setUp()
        app = create_app(Settings(database_schema=self.schema), store=self.store, start_jobs=False)
        self.client = TestClient(app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with self.store.transaction() as s:
            _, self.mac = tokens.issue(s, "telemetry", "Mac mini")
            _, self.pc = tokens.issue(s, "telemetry", "PC")
            _, self.ha = tokens.issue(s, "home_assistant", "HA")

    def post(self, key, body=BEAT):
        return self.client.post("/v2/telemetry/heartbeat", json=body, headers={"Authorization": "Bearer " + key} if key else {})

    def test_each_key_is_one_machine_whatever_address_it_reports(self):
        self.assertEqual(self.post(self.mac).status_code, 200)
        self.assertEqual(self.post(self.mac, {**BEAT, "ip": "10.0.0.99"}).status_code, 200)
        self.assertEqual(self.post(self.pc, {**BEAT, "hostname": "PC", "smc_temperature": None, "system_power_w": None}).status_code, 200)
        with self.store.transaction() as s:
            machines = {c.hostname: c for c in s.query(Computer)}
            self.assertEqual(sorted(machines), ["Mac mini", "PC"])
            self.assertEqual(machines["Mac mini"].ip, "10.0.0.99")
            self.assertEqual(s.query(ComputerMetric).filter_by(computer_id=machines["Mac mini"].id).count(), 2)
            metric = computers.history(s, machines["Mac mini"].id, utcnow() - timedelta(hours=1))[-1]
            self.assertEqual((metric.soc_temp, metric.power_w, metric.mem_pressure_pct), (44.5, 3.2, 37))
            self.assertTrue(computers.online(machines["PC"], utcnow()))
            self.assertFalse(computers.online(machines["PC"], utcnow() + timedelta(minutes=6)))

    def test_other_keys_and_bad_reports_are_refused(self):
        self.assertEqual(self.post(None).status_code, 401)
        self.assertEqual(self.post(self.ha).status_code, 401)
        for bad in ({**BEAT, "system_power_w": 0}, {**BEAT, "cpu_pct": 120}, {**BEAT, "memory_pressure": {"pct": 37.5}},
                    {**BEAT, "smc_temperature": {"tcmb_c": 44, "other": 1}}, {k: v for k, v in BEAT.items() if k != "ip"}):
            self.assertEqual(self.post(self.mac, bad).status_code, 422, bad)
