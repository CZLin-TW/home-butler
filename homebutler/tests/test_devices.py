import unittest
from datetime import datetime, timezone

from homebutler.domain import devices, households
from homebutler.integrations.ha.link import Link
from homebutler.integrations.ha.messages import Snapshot
from homebutler.store.models import AcReading, Device, DeviceEntity, DeviceSetting, SensorReading
from homebutler.tests.helpers import StoreTest


def hid(n):
    return f"{n:032x}"


SNAPSHOT = {
    "type": "snapshot", "sequence": 1,
    "observations": [{"id": hid(1), "entity_id": "binary_sensor.zone", "name": "區一", "area": "一", "floor_id": "f-a",
                      "kind": "occupancy", "value": True, "available": True}],
    "climates": [{"id": hid(2), "entity_id": "climate.one", "name": "冷氣一", "area": "一", "floor_id": "f-a", "available": True,
                  "hvac_mode": "cool", "temperature": 26, "fan_mode": "auto", "source_updated_at": 10.0},
                 {"id": hid(3), "entity_id": "climate.two", "name": "冷氣二", "area": "二", "floor_id": "f-b", "available": True,
                  "hvac_mode": "off", "temperature": 27, "fan_mode": "low", "source_updated_at": 10.0}],
    "ir_buttons": [{"id": hid(4), "entity_id": "button.fan_power", "name": "電扇", "button": "電源", "available": True},
                   {"id": hid(5), "entity_id": "button.fan_up", "name": "電扇", "button": "風速+", "available": True}],
    "environment": [{"id": hid(6), "entity_id": "sensor.t", "name": "感測一", "kind": "temperature", "value": 28.0, "available": True},
                    {"id": hid(7), "entity_id": "sensor.h", "name": "感測一", "kind": "humidity", "value": 99.0, "available": True}],
}


class Socket:
    async def send_json(self, frame): pass


def link_with(snapshot=SNAPSHOT):
    link, socket = Link(), Socket()
    link.connect(socket, {"climate_control", "ir_control"})
    link.receive(socket, Snapshot.model_validate(snapshot))
    return link, socket


class PatchTests(unittest.TestCase):
    def test_ac_commands_are_checked_and_normalized(self):
        self.assertEqual(devices.ac_patch({"power": "off", "temperature": 20}), {"power": "off"})
        self.assertEqual(devices.ac_patch({"temperature": 25.5, "mode": "冷氣", "fan_speed": "強"}),
                         {"power": "on", "temperature": 26, "mode": "cool", "fan_speed": "high"})
        for bad in ({"temperature": 31}, {"temperature": True}, {"temperature": "x"}, {"mode": "颱風"},
                    {"fan_speed": "超強"}, {"power": "maybe"}, {"temperature": float("nan")}):
            with self.assertRaises(devices.Invalid, msg=str(bad)):
                devices.ac_patch(bad)

    def test_an_uncertain_ac_shows_no_power_or_mode(self):
        view = devices.ac_view({"available": True, "uncertain": True, "hvac_mode": "cool", "temperature": 26, "fan_mode": "auto"})
        self.assertEqual((view["available"], view["uncertain"], view["power"], view["mode"]), (True, True, None, None))
        view = devices.ac_view({"available": True, "uncertain": False, "hvac_mode": "dry", "temperature": 26, "fan_mode": "low"})
        self.assertEqual((view["power"], view["mode"], view["fan"]), ("on", "除濕", "低"))


class RegistryTests(StoreTest):
    def setUp(self):
        super().setUp()
        with self.store.transaction() as s:
            households.ensure(s, "A").ha_floor_id = "f-a"
            households.ensure(s, "B").ha_floor_id = "f-b"

    def test_devices_follow_what_ha_shares_and_floors_decide_the_household(self):
        link, socket = link_with()
        with self.store.transaction() as s:
            self.assertEqual(devices.sync_registry(s, link.state(), "A")["added"], 5)
            found = {(d.kind, d.name): d for d in s.query(Device)}
            a, b = households.by_key(s, "A").id, households.by_key(s, "B").id
            self.assertEqual(found[("ac", "冷氣二")].household_id, b)
            self.assertEqual(found[("ac", "冷氣一")].household_id, a)
            self.assertEqual(found[("ir", "電扇")].household_id, a)            # no floor: the default
            self.assertEqual(sorted(e.label for e in found[("ir", "電扇")].entities), ["電源", "風速+"])
            self.assertEqual(s.query(DeviceEntity).count(), 7)
        smaller = {**SNAPSHOT, "sequence": 2, "climates": SNAPSHOT["climates"][:1]}
        link.receive(socket, Snapshot.model_validate(smaller))
        with self.store.transaction() as s:
            self.assertEqual(devices.sync_registry(s, link.state(), "A")["removed"], 1)
            self.assertIsNotNone(s.query(Device).filter_by(name="冷氣二").one().removed_at)

    def test_nothing_is_removed_while_ha_is_offline(self):
        link, socket = link_with()
        with self.store.transaction() as s:
            devices.sync_registry(s, link.state(), "A")
        link.disconnect(socket)
        with self.store.transaction() as s:
            self.assertTrue(devices.sync_registry(s, link.state(), "A")["skipped"])
            self.assertEqual(s.query(Device).filter(Device.removed_at.is_not(None)).count(), 0)

    def test_history_samples_use_offsets_and_skip_uncertain_or_unknown(self):
        link, _ = link_with()
        with self.store.transaction() as s:
            devices.sync_registry(s, link.state(), "A")
            s.add(DeviceSetting(name="感測一", kind="sensor", temperature_offset=-0.5, humidity_offset=3))
        link.uncertain.add(hid(3))
        with self.store.transaction() as s:
            self.assertEqual(devices.sample(s, link.state(), datetime(2026, 10, 21, tzinfo=timezone.utc)), {"sensors": 1, "acs": 1})
            reading = s.query(SensorReading).one()
            self.assertEqual((reading.temperature, reading.humidity), (27.5, 100.0))      # offset, and clamped
            self.assertEqual(s.query(AcReading).one().power, "on")                       # 冷氣二 is uncertain: skipped


if __name__ == "__main__":
    unittest.main()
