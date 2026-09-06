from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from command_result import CommandResult
from test_callback_concurrency import endpoint


class CommandResultTests(unittest.TestCase):
    def test_ir_provider_outcome_and_legacy_text(self):
        api = SimpleNamespace(ir_control=Mock())
        env = {"CommandResult": CommandResult, "get_device_id_by_name": lambda *a: "fake-id", "switchbot_api": api}
        typed = endpoint("handlers/device.py", "control_ir_result", env)
        legacy = endpoint("handlers/device.py", "handle_control_ir", env)
        for reply, expected in [({"success": True}, "success"), ({"success": False, "error": "rejected"}, "failed"),
                                ({"success": False, "uncertain": True}, "unknown")]:
            api.ir_control.return_value = reply
            result = typed({"device_name": "測試", "button": "電源"}, None)
            self.assertEqual(result.status, expected)
            if expected == "unknown": self.assertIn("❌", result.message)
            self.assertEqual(legacy({"device_name": "測試", "button": "電源"}, None), result.message)
        api.ir_control.reset_mock()
        self.assertEqual(typed({"device_name": "測試"}, None).status, "failed")
        api.ir_control.assert_not_called()

    def test_dehumidifier_partial_failure_stops_remaining_steps(self):
        for brand in ["panasonic", "lg"]:
            with self.subTest(brand=brand):
                api = SimpleNamespace(dehumidifier_turn_on=Mock(return_value={"success": True}),
                                      dehumidifier_set_mode=Mock(return_value={"success": False, "error": "refused"}),
                                      dehumidifier_set_humidity=Mock())
                fn = endpoint("handlers/device.py", "_control_dehumidifier_" + brand,
                              {"CommandResult": CommandResult, brand + "_api": api})
                row = {"Auth": "fake", "Device ID": "fake"}
                self.assertEqual(fn(row, "測試", "on", "auto", 50).status, "failed")
                api.dehumidifier_set_humidity.assert_not_called()
                api.dehumidifier_set_mode.return_value = {"success": False, "uncertain": True}
                self.assertEqual(fn(row, "測試", "on", "auto", 50).status, "unknown")
                api.dehumidifier_set_mode.return_value = {"success": True}
                api.dehumidifier_set_humidity.return_value = {"success": True}
                self.assertEqual(fn(row, "測試", "on", "auto", 50).status, "success")

    def test_locked_dehumidifier_does_not_dispatch(self):
        fn = endpoint("handlers/device.py", "control_dehumidifier_result", {
            "CommandResult": CommandResult, "_resolve_dehumidifier": lambda *a: ({"名稱": "測試"}, None),
            "dehumidifier_auto": SimpleNamespace(is_locked=lambda n: True)})
        self.assertEqual(fn({"power": "on"}, None).status, "failed")

    def test_ac_automatic_off_does_not_rearm_and_keeps_legacy_text(self):
        api = SimpleNamespace(ac_turn_off=Mock(return_value={"success": True}))
        maintain = Mock()
        env = {"CommandResult": CommandResult, "get_device_id_by_name": lambda *a: "fake", "switchbot_api": api,
               "now_taipei": lambda: None, "_save_ac_last_state": Mock(), "maintain_ac_auto_schedule": maintain}
        typed = endpoint("handlers/device.py", "control_ac_result", env)
        legacy = endpoint("handlers/device.py", "handle_control_ac", env)
        ctx = SimpleNamespace(get=lambda n: [{"Device ID": "fake", "狀態": "啟用", "最後電源": "on"}])
        params = {"device_name": "測試", "power": "off", "antimold_final": True, "restore_mode": "冷氣"}
        self.assertEqual(typed(params, ctx, from_auto_schedule=True).status, "success")
        maintain.assert_not_called()
        self.assertIsInstance(legacy(params, ctx, from_auto_schedule=True), str)
        api.ac_turn_off.return_value = {"success": False, "uncertain": True}
        self.assertEqual(typed(params, ctx).status, "unknown")

    def test_switchbot_transport_error_is_unknown(self):
        fn = endpoint("switchbot_api.py", "send_command", {
            "httpx": SimpleNamespace(post=Mock(side_effect=TimeoutError("lost reply"))),
            "BASE_URL": "fake", "_make_headers": lambda: {}})
        self.assertTrue(fn("fake", "turnOn")["uncertain"])

    def test_panasonic_command_does_not_retry_ambiguous_response(self):
        for response in [TimeoutError("lost reply"), SimpleNamespace(status_code=200, text="")]:
            request = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
            renew = Mock()
            env = {"_ensure_token": lambda: True, "_circuit_open": lambda: False, "_cp_token": "fake",
                   "_client": SimpleNamespace(request=request), "_renew_token": renew}
            fn = endpoint("panasonic_api.py", "_request_with_retry", env)
            self.assertIsNone(fn("GET", "DeviceSetCommand", retry_ambiguous=False))
            request.assert_called_once()
            renew.assert_not_called()


if __name__ == "__main__": unittest.main()
