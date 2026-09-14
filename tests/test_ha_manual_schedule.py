"""Schedule creation/editing cannot revive retired automation implicitly."""
from datetime import datetime
import json
import unittest
from unittest.mock import Mock, patch

from schedule_execution import HA_MANUAL_SOURCE, ATTENTION_STATES, ATTEMPT_COLUMN
from test_callback_concurrency import endpoint
from test_schedule_execution import Sheet, Context, schedule, update_fields


class HaManualScheduleTests(unittest.TestCase):
    def setUp(self):
        self.sheet = Sheet([schedule()])
        self.ctx = Context(self.sheet)
        self.ctx.data["排程指令"] = [dict(self.sheet.rows[0])]
        self.env = {"json": json, "datetime": datetime, "now_taipei": lambda: datetime(2026, 9, 6, 9),
                    "HA_MANUAL_SOURCE": HA_MANUAL_SOURCE, "ATTENTION_STATES": ATTENTION_STATES,
                    "ATTEMPT_COLUMN": ATTEMPT_COLUMN, "maintain_ac_auto_schedule": Mock(),
                    "update_row_fields": update_fields, "append_record": lambda sheet, row: sheet.rows.append(dict(row))}
        endpoint("handlers/schedule.py", "_norm_trigger", self.env)
        endpoint("handlers/schedule.py", "_locate_schedule_rows", self.env)
        self.add = endpoint("handlers/schedule.py", "handle_add_schedule", self.env)
        self.modify = endpoint("handlers/schedule.py", "handle_modify_schedule", self.env)

    def test_new_and_explicitly_edited_manual_rows_get_ha_marker(self):
        with patch("ha_climate.managed", return_value=True):
            result = self.add({"device_name": "測試冷氣", "target_action": "control_ac", "params": {"power": "off"}, "trigger_time": "2026-09-06 13:00"}, "測試", self.ctx)
            self.assertTrue(result.startswith("✅"))
            self.assertEqual(self.sheet.rows[-1]["來源"], HA_MANUAL_SOURCE)
            result = self.modify({"device_name": "測試冷氣", "trigger_time": "2026-09-06 12:00", "trigger_time_new": "2026-09-06 14:00"}, "測試", self.ctx)
            self.assertTrue(result.startswith("✅"))
            self.assertEqual(self.sheet.rows[0]["來源"], HA_MANUAL_SOURCE)
            self.assertEqual(self.sheet.rows[0]["觸發時間"], "2026-09-06 14:00")

    def test_editing_cannot_convert_retired_automatic_rows_to_manual(self):
        for source in ("自動", "防黴"):
            self.sheet.rows[0]["來源"] = source
            self.ctx.data["排程指令"][0]["來源"] = source
            with patch("ha_climate.managed", return_value=True):
                result = self.modify({"device_name": "測試冷氣", "trigger_time": "2026-09-06 12:00", "trigger_time_new": "2026-09-06 14:00"}, "測試", self.ctx)
            self.assertTrue(result.startswith("❌"))
            self.assertEqual(self.sheet.rows[0]["觸發時間"], "2026-09-06 12:00")
