import time
import unittest

from fastapi import Depends
from fastapi.testclient import TestClient

from homebutler.api.auth import AssertionError_, DashboardUser, dashboard_user, read_assertion, sign_assertion
from homebutler.app import create_app
from homebutler.domain import households, tokens
from homebutler.domain.passwords import hash_password
from homebutler.jobs.runner import Jobs
from homebutler.settings import Settings
from homebutler.store.models import Member, Remote
from homebutler.tests.helpers import StoreTest

KEY = "d" * 40


def claims(**change):
    now = int(time.time())
    return {"aud": "homebutler", "sub": "U1", "role": "member", "iat": now, "exp": now + 120, **change}


class AssertionTests(unittest.TestCase):
    def test_valid_signed_and_fresh_only(self):
        self.assertEqual(read_assertion(KEY, sign_assertion(KEY, claims()))["sub"], "U1")
        now = int(time.time())
        for bad in [sign_assertion("x" * 40, claims()), sign_assertion(KEY, claims(aud="other")),
                    sign_assertion(KEY, claims(exp=now - 1, iat=now - 60)), sign_assertion(KEY, claims(exp=now + 3600)),
                    sign_assertion(KEY, claims(role="admin")), sign_assertion(KEY, claims(sub="")), "a.b", "", "x.y.z"]:
            with self.assertRaises(AssertionError_):
                read_assertion(KEY, bad)
        token = sign_assertion(KEY, claims())
        head, body, mac = token.split(".")
        with self.assertRaises(AssertionError_):
            read_assertion(KEY, f"{head}.{sign_assertion(KEY, claims(sub='U2')).split('.')[1]}.{mac}")   # body swapped


class AppTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(dashboard_assertion_key=KEY, database_schema=self.schema), store=self.store, start_jobs=False)

        @self.app.get("/v2/test/me")
        def me(user: DashboardUser = Depends(dashboard_user)):
            return {"role": user.role, "name": user.member.name if user.member else None, "remote": user.remote_id}

        self.client = TestClient(self.app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with self.store.transaction() as session:
            home = households.ensure(session, "A")
            session.add(Member(household_id=home.id, name="CZ", line_user_id="U1", active=True))
            session.add(Member(household_id=home.id, name="停", line_user_id="U2", active=False))
            remote = Remote(household_id=home.id, name="平板", password_hash=hash_password("1"), enabled=True)
            session.add(remote); session.flush(); self.remote_id = remote.id
            _, self.dashboard = tokens.issue(session, "dashboard", "Dashboard")
            _, self.admin = tokens.issue(session, "admin", "admin")
            _, self.ha = tokens.issue(session, "home_assistant", "HA")

    def get(self, path, key=None, assertion=None):
        headers = {}
        if key: headers["Authorization"] = "Bearer " + key
        if assertion: headers["X-Dashboard-Assertion"] = assertion
        return self.client.get(path, headers=headers)

    def test_health_is_public_and_says_nothing(self):
        self.assertEqual(self.get("/v2/health").json(), {"ok": True})

    def test_status_needs_the_admin_key(self):
        self.assertEqual(self.get("/v2/system/status").status_code, 401)
        self.assertEqual(self.get("/v2/system/status", self.dashboard).status_code, 401)
        body = self.get("/v2/system/status", self.admin).json()
        from homebutler.store import migrate
        self.assertEqual(body["revision"], migrate.head())

    def test_dashboard_requests_need_both_the_key_and_a_fresh_assertion_of_an_active_member(self):
        good = sign_assertion(KEY, claims())
        self.assertEqual(self.get("/v2/test/me", self.dashboard, good).json(), {"role": "member", "name": "CZ", "remote": None})
        self.assertEqual(self.get("/v2/test/me", None, good).status_code, 401)
        self.assertEqual(self.get("/v2/test/me", self.ha, good).status_code, 401)            # another caller's key
        self.assertEqual(self.get("/v2/test/me", self.dashboard).status_code, 401)
        self.assertEqual(self.get("/v2/test/me", self.dashboard, sign_assertion(KEY, claims(sub="U2"))).status_code, 403)
        self.assertEqual(self.get("/v2/test/me", self.dashboard, sign_assertion(KEY, claims(sub="U9"))).status_code, 403)
        remote = sign_assertion(KEY, claims(sub=str(self.remote_id), role="remote"))
        self.assertEqual(self.get("/v2/test/me", self.dashboard, remote).json()["role"], "remote")
        with self.store.transaction() as session:
            session.get(Remote, self.remote_id).enabled = False
        self.assertEqual(self.get("/v2/test/me", self.dashboard, remote).status_code, 403)


class JobTests(unittest.TestCase):
    def test_a_failing_job_is_recorded_and_others_keep_running(self):
        jobs, calls = Jobs(clock=lambda: 100.0), []
        jobs.add("bad", 60, lambda: 1 / 0)
        jobs.add("good", 60, lambda: calls.append(1))
        jobs.run_once("bad"); jobs.run_once("good")
        status = {j["name"]: j for j in jobs.status()}
        self.assertEqual((status["bad"]["error"], status["bad"]["succeeded_at"]), ("ZeroDivisionError", None))
        self.assertEqual((status["good"]["error"], status["good"]["succeeded_at"], calls), (None, 100.0, [1]))
        with self.assertRaises(ValueError):
            jobs.add("good", 1, lambda: None)


if __name__ == "__main__":
    unittest.main()
