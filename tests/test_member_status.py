"""Session renewal check: only an explicit roster mismatch reports inactive. Fake Sheets only."""
from types import SimpleNamespace
import unittest

from endpoint_helper import endpoint
from todo_access import actor_from_members


class HttpError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code


class MemberStatusTests(unittest.TestCase):
    def call(self, user_id, members=None, fail=False):
        rows = members if members is not None else [
            {"Line User ID": "U-on", "名稱": "家人", "狀態": "啟用"},
            {"Line User ID": "U-off", "名稱": "停用家人", "狀態": "停用"}]
        loaded = []
        def load(names):
            loaded.append(names)
            if fail:
                raise RuntimeError("sheets down")
        fn = endpoint("web_api.py", "api_member_status", {
            "RequestContext": lambda: SimpleNamespace(load=load, get=lambda name: rows),
            "actor_from_members": actor_from_members, "HTTPException": HttpError})
        result = fn(SimpleNamespace(headers={} if user_id is None else {"X-Dashboard-User": user_id}))
        self.assertEqual(loaded, [["家庭成員"]])
        return result

    def test_active_disabled_unknown_and_duplicate_members(self):
        self.assertEqual(self.call("U-on"), {"active": True, "name": "家人"})
        self.assertEqual(self.call("U-off"), {"active": False})
        self.assertEqual(self.call("U-gone"), {"active": False})
        twice = [{"Line User ID": "U-on", "名稱": "家人", "狀態": "啟用"}] * 2
        self.assertEqual(self.call("U-on", twice), {"active": False})

    def test_missing_identity_and_unreadable_roster_are_errors_not_inactive(self):
        for user_id in (None, ""):
            with self.assertRaises(HttpError) as error:
                endpoint("web_api.py", "api_member_status", {"HTTPException": HttpError})(
                    SimpleNamespace(headers={} if user_id is None else {"X-Dashboard-User": user_id}))
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(RuntimeError):
            self.call("U-on", fail=True)


if __name__ == "__main__":
    unittest.main()
