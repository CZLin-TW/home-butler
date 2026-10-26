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

    def test_a_sender_not_yet_updated_reports_at_the_old_address(self):
        old = lambda key: self.client.post("/api/computers/heartbeat", json=BEAT, headers={"X-API-Key": key})
        self.assertEqual(old(self.mac).status_code, 200)
        self.assertEqual(old(self.ha).status_code, 401)
        self.assertEqual(old("").status_code, 401)
        with self.store.transaction() as s:
            self.assertEqual([c.hostname for c in s.query(Computer)], ["Mac mini"])

    def test_other_keys_and_bad_reports_are_refused(self):
        self.assertEqual(self.post(None).status_code, 401)
        self.assertEqual(self.post(self.ha).status_code, 401)
        for bad in ({**BEAT, "system_power_w": 0}, {**BEAT, "cpu_pct": 120}, {**BEAT, "memory_pressure": {"pct": 37.5}},
                    {**BEAT, "smc_temperature": {"tcmb_c": 44, "other": 1}}, {k: v for k, v in BEAT.items() if k != "ip"}):
            self.assertEqual(self.post(self.mac, bad).status_code, 422, bad)


class ThroughHomeAssistantTests(StoreTest):
    """Machines HA reads on the home network and passes on with its snapshots."""

    def report(self, sampled_at, **beat):
        return computers.Report(source="mac-mini", sampled_at=sampled_at, heartbeat={**BEAT, **beat},
                                watchdogs=[{"name": "funnel", "healthy": True, "last_line": "ok", "extra": "ignored"}])

    def test_the_first_report_takes_over_the_keyed_card_and_each_sample_counts_once(self):
        with self.store.transaction() as s:
            token, _ = tokens.issue(s, "telemetry", "Mac mini")
            computers.record(s, token, computers.Heartbeat.model_validate(BEAT), utcnow())
        with self.store.transaction() as s:
            self.assertIsNotNone(computers.record_report(s, self.report(100), utcnow()))
            self.assertIsNone(computers.record_report(s, self.report(100), utcnow()))      # same sample again
            self.assertIsNotNone(computers.record_report(s, self.report(160, cpu_pct=30), utcnow()))
        with self.store.transaction() as s:
            mac = s.query(Computer).one()                                                   # one card, history goes on
            self.assertEqual((mac.source, mac.token_id is not None), ("mac-mini", True))
            self.assertEqual(s.query(ComputerMetric).filter_by(computer_id=mac.id).count(), 3)
            self.assertEqual(mac.current["cpu_pct"], 30)
            self.assertEqual(mac.current["watchdogs"], [{"name": "funnel", "healthy": True, "last_line": "ok",
                                                         "last_action": None, "paused": False}])

    def test_a_new_machine_through_ha_gets_its_own_card(self):
        with self.store.transaction() as s:
            computers.record_report(s, self.report(100, ip="10.0.0.77"), utcnow())
            self.assertEqual(s.query(Computer).one().token_id, None)

    def test_a_report_must_name_its_source_plainly(self):
        for bad in ("", "Mac Mini", "../x", "a" * 41):
            with self.assertRaises(ValueError):
                computers.Report(source=bad, sampled_at=1, heartbeat=BEAT)

    def test_a_snapshot_carries_reports_to_the_recording_job(self):
        from homebutler.integrations.ha.link import Link
        from homebutler.integrations.ha.messages import Snapshot
        from homebutler.jobs import devices as device_jobs

        class Socket:
            async def send_json(self, frame): pass
        link, socket = Link(), Socket()
        link.connect(socket, {"observations", "computer_reports"})
        link.receive(socket, Snapshot.model_validate({"type": "snapshot", "sequence": 1, "observations": [],
                                                      "computers": [self.report(100).model_dump()]}))
        self.assertEqual(device_jobs.record_computers(self.store, link), 1)
        self.assertEqual(device_jobs.record_computers(self.store, link), 0)       # the next snapshot repeats it
        link.disconnect(socket)
        self.assertEqual(link.computer_reports(), [])                                # HA away: nothing to record
