"""Real IR handler with fake device rows/transport; no cloud or hardware access."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from command_result import CommandResult
from device_name_resolution import resolve_ir_device
from test_callback_concurrency import endpoint


def device(name="主臥電扇", device_id="bedroom", **fields):
    return {"名稱": name, "Device ID": device_id, "類型": "IR", "狀態": "啟用", **fields}


class IrNameTests(unittest.TestCase):
    def setUp(self):
        self.send = Mock(return_value={"success": True})
        self.control = endpoint("handlers/device.py", "control_ir_result", {
            "CommandResult": CommandResult, "resolve_ir_device": resolve_ir_device,
            "switchbot_api": SimpleNamespace(ir_control=self.send),
        })

    def run_command(self, name, rows, button="開"):
        return self.control({"device_name": name, "button": button}, SimpleNamespace(get=lambda _: rows))

    def test_aliases_use_configured_id_and_reply_name_in_both_directions(self):
        for stored, spoken in [("主臥電扇", "主臥電風扇"), ("主臥電風扇", "主臥電扇")]:
            for button in ["開", "關", "風速"]:
                with self.subTest(stored=stored, spoken=spoken, button=button):
                    self.send.reset_mock()
                    result = self.run_command(spoken, [device(stored), device("客廳電扇", "living")], button)
                    self.assertEqual(result.status, "success")
                    self.send.assert_called_once_with("bedroom", button)
                    self.assertIn(stored, result.message)

    def test_exact_name_wins_over_another_devices_alias(self):
        result = self.run_command("主臥電扇", [device("主臥電風扇", "other"), device()])
        self.assertEqual(result.status, "success")
        self.send.assert_called_once_with("bedroom", "開")

    def test_whitespace_and_unicode_normalization_preserve_exact_names(self):
        result = self.run_command(" 主臥電風扇 ", [device("主臥電扇 ")])
        self.assertEqual(result.status, "success")
        self.send.assert_called_once_with("bedroom", "開")

    def test_wrong_room_never_falls_back_to_only_device(self):
        for name in ["客廳電風扇", "電風扇", "主臥電視", "主臥電扇打開"]:
            with self.subTest(name=name):
                self.assertEqual(self.run_command(name, [device()]).status, "failed")
        self.send.assert_not_called()

    def test_omitted_name_only_works_for_a_single_device(self):
        self.assertEqual(self.run_command("", [device()]).status, "success")
        self.send.assert_called_once_with("bedroom", "開")
        self.send.reset_mock()
        self.assertEqual(self.run_command("", [device(), device("客廳電扇", "living")]).status, "failed")
        self.assertEqual(self.run_command("", []).status, "failed")
        self.send.assert_not_called()

    def test_ambiguous_exact_or_alias_names_do_not_send(self):
        rows = [device(), device(device_id="other")]
        for name in ["主臥電扇", "主臥電風扇"]:
            with self.subTest(name=name):
                self.assertEqual(self.run_command(name, rows).status, "failed")
        self.send.assert_not_called()

    def test_disabled_wrong_type_and_missing_ids_do_not_send(self):
        for row in [device(狀態="停用"), device(類型="空調"), device(device_id=""), device(device_id=" ")]:
            for name in ["主臥電扇", "主臥電風扇"]:
                with self.subTest(row=row, name=name):
                    self.assertEqual(self.run_command(name, [row]).status, "failed")
        self.send.assert_not_called()

    def test_missing_action_is_not_inferred_from_alias(self):
        self.assertEqual(self.run_command("主臥電風扇", [device()], button="").status, "failed")
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
