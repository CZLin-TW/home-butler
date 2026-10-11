"""Pairing yields members only; remotes can be switched off from the sheet. Fake Sheets only."""
import importlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class Sheet:
    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]
    def get_all_records(self):
        return [dict(r) for r in self.rows]
    def delete_rows(self, number):
        del self.rows[number - 2]


class AuthTests(unittest.TestCase):
    def load(self, name, sheet):
        fake = SimpleNamespace(get_or_create_sheet=lambda *_: sheet,
                               append_record=lambda ws, row: ws.rows.append(dict(row)),
                               update_row_fields=lambda ws, number, fields: ws.rows[number - 2].update(fields))
        with patch.dict(sys.modules, {"sheets": fake}):
            sys.modules.pop(name, None)
            module = importlib.import_module(name)
        self.addCleanup(sys.modules.pop, name, None)
        return module

    def test_pairing_approves_as_member_once_and_refuses_leftover_kid_rows(self):
        sheet = Sheet()
        auth = self.load("device_auth", sheet)
        made = auth.create_pairing()
        self.assertEqual(sheet.rows[0]["role"], "member")
        self.assertEqual(auth.get_status(made["device_token"]), {"status": "pending"})
        self.assertFalse(auth.approve("000000" if made["user_code"] != "000000" else "111111", "U1", "家人"))
        self.assertTrue(auth.approve(made["user_code"], "U1", "家人", "pic"))
        self.assertFalse(auth.approve(made["user_code"], "U2", "別人"))
        status = auth.get_status(made["device_token"])
        self.assertEqual(status["user"], {"lineUserId": "U1", "name": "家人", "picture": "pic", "role": "member"})
        self.assertEqual(auth.get_status(made["device_token"])["status"], "consumed")
        # A pairing an old client created as kid must not be upgraded to a member.
        kid = auth.create_pairing()
        sheet.rows[-1]["role"] = "kid"
        self.assertFalse(auth.approve(kid["user_code"], "U1", "家人"))
        self.assertEqual(sheet.rows[-1]["status"], "pending")
        late = auth.create_pairing()
        sheet.rows[-1]["expires"] = 1
        self.assertFalse(auth.approve(late["user_code"], "U1", "家人"))

    def test_remote_stays_active_only_while_its_row_is_enabled(self):
        sheet = Sheet([{"名稱": "客廳平板", "密碼": "pw-a", "啟用": "TRUE"},
                       {"名稱": "舊平板", "密碼": "pw-b", "啟用": "FALSE"},
                       {"名稱": "", "密碼": "pw-c", "啟用": True},
                       {"名稱": "沒密碼", "密碼": "", "啟用": "TRUE"}])
        remote = self.load("remote_auth", sheet)
        self.assertEqual(remote.verify("pw-a")["user"], {"name": "客廳平板", "role": "kid"})
        self.assertEqual(remote.verify("pw-c")["user"]["name"], "遙控器")
        self.assertEqual([remote.is_active(n) for n in ("客廳平板", "遙控器", "舊平板", "沒密碼", "不存在", "", None)],
                         [True, True, False, False, False, False, False])
        sheet.rows[0]["啟用"] = "FALSE"
        self.assertFalse(remote.is_active("客廳平板"))
        sheet.rows[0].update(啟用="TRUE", 密碼="changed")   # a new password alone does not sign anyone out
        self.assertTrue(remote.is_active("客廳平板"))
        sheet.rows[0]["名稱"] = "改名"
        self.assertFalse(remote.is_active("客廳平板"))


if __name__ == "__main__":
    unittest.main()
