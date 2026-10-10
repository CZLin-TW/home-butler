"""Sheet round trip for the PC history, including the Mac-only columns. Fake Sheets only."""
import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

OLD_HEADERS = ["timestamp", "ip", "cpu_pct", "ram_pct", "gpu_pct", "cpu_temp_c", "gpu_temp_c"]


class Worksheet:
    def __init__(self, headers, rows=()):
        self.headers, self.rows = list(headers), [list(r) for r in rows]

    def append_row(self, row, value_input_option=None):
        self.rows.append(["" if v is None else v for v in row])

    def get_all_records(self):
        if any(len(r) > len(self.headers) for r in self.rows):
            raise ValueError("row wider than header")
        return [dict(zip(self.headers, r + [""] * len(self.headers))) for r in self.rows]


def ensure_columns(sheet, columns):
    sheet.headers += [c for c in columns if c not in sheet.headers]


class HistoryRoundTripTests(unittest.TestCase):
    def load(self, sheet, ensure=ensure_columns):
        spec = importlib.util.spec_from_file_location("pc_state_under_test", Path(__file__).parents[1] / "pc_state.py")
        state = importlib.util.module_from_spec(spec)
        book = types.SimpleNamespace(worksheet=lambda _: sheet)
        with patch.dict(sys.modules, {"gspread": types.SimpleNamespace(),
                                     "sheets": types.SimpleNamespace(_get_spreadsheet=lambda: book, ensure_columns=ensure)}):
            spec.loader.exec_module(state)
        return state

    def beat(self, state, **extra):
        with patch.object(state.threading, "Thread") as thread:
            state.record_heartbeat({"ip": "192.0.2.20", "cpu_pct": 12, "ram_pct": 40, **extra})
        target, args = thread.call_args.kwargs["target"], thread.call_args.kwargs["args"]
        target(*args)

    def test_mac_fields_survive_restart_next_to_old_rows(self):
        old = time.time() - 600
        sheet = Worksheet(OLD_HEADERS, [[old, "192.0.2.20", 5, 30, "", "", ""]])
        writer = self.load(sheet)
        self.beat(writer, smc_temperature={"tcmb_c": 44.5, "tcmz_c": None},
                  memory_pressure={"level": "warning", "pct": 37})
        self.beat(writer, ip="192.0.2.30", cpu_temp_c=61, gpu_temp_c=55)
        self.assertEqual(sheet.headers, writer.HISTORY_HEADERS)

        restarted = self.load(sheet)
        restarted.backfill_from_sheet()
        mac = restarted.snapshot()["192.0.2.20"]
        self.assertEqual([p["cpu_pct"] for p in mac["history"]], [5, 12])
        self.assertIsNone(mac["history"][0]["smc_temperature"])
        self.assertIsNone(mac["history"][0]["memory_pressure"])
        self.assertEqual(mac["history"][1]["smc_temperature"], {"tcmb_c": 44.5, "tcmz_c": None})
        self.assertEqual(mac["history"][1]["memory_pressure"], {"level": "warning", "pct": 37})
        self.assertEqual(mac["current"]["memory_pressure"], {"level": "warning", "pct": 37})
        self.assertTrue(mac["online"])
        windows = restarted.snapshot()["192.0.2.30"]["history"][0]
        self.assertEqual((windows["cpu_temp_c"], windows["smc_temperature"], windows["memory_pressure"]), (61, None, None))

    def test_out_of_range_cells_read_as_missing(self):
        state = self.load(Worksheet(OLD_HEADERS))
        for row, expected in [
            ({"soc_temp_c": 0, "mem_pressure_pct": 101, "mem_pressure_level": "green"}, (None, None)),
            ({"soc_temp_c": "x", "mem_pressure_pct": 37.5, "mem_pressure_level": ""}, (None, None)),
            ({"soc_temp_c": 151, "mem_pressure_pct": "", "mem_pressure_level": "critical"},
             (None, {"level": "critical", "pct": None})),
            ({"soc_temp_c": "50.25", "mem_pressure_pct": "0", "mem_pressure_level": None},
             ({"tcmb_c": 50.25, "tcmz_c": None}, {"level": None, "pct": 0})),
        ]:
            got = state._mac_fields(row)
            self.assertEqual((got["smc_temperature"], got["memory_pressure"]), expected)

    def test_rows_are_not_widened_until_the_header_is(self):
        sheet = Worksheet(OLD_HEADERS)
        state = self.load(sheet, ensure=lambda *_: (_ for _ in ()).throw(RuntimeError("503")))
        self.beat(state, smc_temperature={"tcmb_c": 44.5, "tcmz_c": None})
        self.assertEqual(sheet.rows, [])
        state.ensure_columns = ensure_columns
        self.beat(state, smc_temperature={"tcmb_c": 44.5, "tcmz_c": None})
        self.assertEqual(len(sheet.rows), 1)
        self.assertEqual(len(sheet.get_all_records()), 1)


if __name__ == "__main__":
    unittest.main()
