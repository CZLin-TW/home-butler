from homebutler.domain import tokens
from homebutler.settings import Settings, SettingsError
from homebutler.store.models import ApiToken
from homebutler.tests.helpers import StoreTest

MAC, OLD_HA, DASH, NEW_DASH = "m" * 30, "h" * 40, "d" * 40, "n" * 40


class EnvironmentKeyTests(StoreTest):
    def test_environment_keys_work_and_a_replaced_one_stops_working(self):
        with self.store.transaction() as s:
            self.assertEqual(tokens.from_environment(s, [("telemetry", "Mac mini", MAC), ("home_assistant", "Home Assistant", OLD_HA),
                                                         ("dashboard", "Dashboard", DASH)]), {"added": 3, "revoked": 0})
            _, issued = tokens.issue(s, "dashboard", "made here")
        with self.store.transaction() as s:
            self.assertEqual(tokens.verify(s, MAC, "telemetry").label, "env:Mac mini")
            self.assertIsNone(tokens.verify(s, MAC, "dashboard"))             # the old all-purpose key is now telemetry only
            self.assertIsNotNone(tokens.verify(s, OLD_HA, "home_assistant"))
            self.assertEqual(tokens.from_environment(s, [("telemetry", "Mac mini", MAC), ("home_assistant", "Home Assistant", OLD_HA),
                                                         ("dashboard", "Dashboard", NEW_DASH)]), {"added": 1, "revoked": 1})
        with self.store.transaction() as s:
            self.assertIsNone(tokens.verify(s, DASH, "dashboard"))
            self.assertIsNotNone(tokens.verify(s, NEW_DASH, "dashboard"))
            self.assertIsNotNone(tokens.verify(s, issued, "dashboard"))        # keys made another way are left alone
            self.assertIsNone(tokens.verify(s, "short", "dashboard"))
            self.assertEqual(s.query(ApiToken).count(), 5)

    def test_settings_read_and_check_the_keys(self):
        settings = Settings.from_env({"DASHBOARD_API_KEY": DASH, "TELEMETRY_KEYS": '{"Mac mini": "%s"}' % MAC})
        self.assertEqual(settings.keys(), [("dashboard", "Dashboard", DASH), ("telemetry", "Mac mini", MAC)])
        self.assertNotIn(DASH, repr(settings))
        one_each = Settings.from_env({"TELEMETRY_KEY_MAC_MINI": MAC, "TELEMETRY_KEY_PC": OLD_HA})
        self.assertEqual(sorted(one_each.keys()), [("telemetry", "Mac Mini", MAC), ("telemetry", "Pc", OLD_HA)])
        for env in ({"DASHBOARD_API_KEY": "short"}, {"DASHBOARD_API_KEY": DASH, "ADMIN_API_KEY": DASH},
                    {"TELEMETRY_KEYS": "not json"}, {"TELEMETRY_KEYS": '["x"]'}):
            with self.assertRaises(SettingsError):
                Settings.from_env(env)
