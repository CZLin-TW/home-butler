from datetime import datetime, timedelta, timezone

from homebutler.domain import history, households
from homebutler.store.models import AcReading, ApiToken, Computer, ComputerMetric, Device, SensorReading
from homebutler.tests.helpers import StoreTest

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


class PruneTests(StoreTest):
    def test_rows_older_than_400_days_go_and_the_rest_stay(self):
        old, kept = NOW - timedelta(days=401), NOW - timedelta(days=399)
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            sensor = Device(kind="sensor", name="感測一", household_id=home.id)
            ac = Device(kind="ac", name="冷氣一", household_id=home.id)
            gone = Device(kind="sensor", name="感測二", household_id=home.id, removed_at=old)
            token = ApiToken(kind="telemetry", label="電腦一", token_hash="h" * 64)
            s.add_all([sensor, ac, gone, token])
            s.flush()
            computer = Computer(token_id=token.id)
            s.add(computer)
            s.flush()
            for t in (old, kept):
                s.add_all([SensorReading(device_id=sensor.id, t=t, temperature=25), SensorReading(device_id=gone.id, t=t),
                           AcReading(device_id=ac.id, t=t, power="on"), ComputerMetric(computer_id=computer.id, t=t)])
        with self.store.transaction() as s:
            self.assertEqual(history.prune(s, NOW), 4)   # the removed device's old rows go too
        with self.store.transaction() as s:
            for model, left in ((SensorReading, 2), (AcReading, 1), (ComputerMetric, 1)):
                self.assertEqual(s.query(model).count(), left, model.__name__)
            self.assertEqual(history.prune(s, NOW), 0)
