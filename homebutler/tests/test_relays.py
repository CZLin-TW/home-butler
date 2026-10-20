import json
import time
import unittest

import httpx
from fastapi.testclient import TestClient

from homebutler.api.auth import sign_assertion
from homebutler.app import create_app
from homebutler.domain import households, tokens
from homebutler.domain.passwords import hash_password
from homebutler.integrations import switchbot_push
from homebutler.settings import Settings, SettingsError
from homebutler.store.models import Member, Remote
from homebutler.tests.helpers import StoreTest

KEY = "k" * 40


class RegisterTests(unittest.TestCase):
    def client(self, existing):
        calls = []
        def handle(request):
            body = json.loads(request.content)
            calls.append(body["action"])
            if body["action"] == "queryUrl":
                return httpx.Response(200, json={"statusCode": 100, "body": {"urls": existing}})
            return httpx.Response(200, json={"statusCode": 100, "body": {}})
        return httpx.Client(transport=httpx.MockTransport(handle)), calls

    def test_registers_updates_or_leaves_alone(self):
        url = "https://hb.example/v2/switchbot/webhook"
        for existing, result, actions in (([url], "already", ["queryUrl"]), (["https://old/x"], "updated", ["queryUrl", "updateWebhook"]),
                                          ([], "created", ["queryUrl", "setupWebhook"])):
            client, calls = self.client(existing)
            self.assertEqual(switchbot_push.register(url, "t", "s", client), result)
            self.assertEqual(calls, actions)

    def test_signature_is_the_documented_one(self):
        headers = switchbot_push.signed_headers("tok", "sec", now_ms=1000, nonce="n")
        import base64, hashlib, hmac
        self.assertEqual(headers["sign"], base64.b64encode(hmac.new(b"sec", b"tok1000n", hashlib.sha256).digest()).decode())

    def test_registration_must_be_asked_for_and_complete(self):
        self.assertFalse(Settings.from_env({}).switchbot_register_webhook)
        with self.assertRaises(SettingsError):
            Settings.from_env({"SWITCHBOT_REGISTER_WEBHOOK": "1", "SWITCHBOT_TOKEN": "t", "SWITCHBOT_SECRET": "s",
                               "PUBLIC_BASE_URL": "http://insecure"})


class RelayEndpointTests(StoreTest):
    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(database_schema=self.schema, dashboard_assertion_key=KEY), store=self.store, start_jobs=False)
        self.client = TestClient(self.app); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with self.store.transaction() as s:
            home = households.ensure(s, "A")
            s.add(Member(household_id=home.id, name="爸", line_user_id="U1", active=True))
            remote = Remote(household_id=home.id, name="平板", password_hash=hash_password("1"), enabled=True)
            s.add(remote); s.flush(); self.remote = remote.id
            _, self.dashboard = tokens.issue(s, "dashboard", "Dashboard")

    def headers(self, sub="U1", role="member"):
        now = int(time.time())
        return {"Authorization": "Bearer " + self.dashboard,
                "X-Dashboard-Assertion": sign_assertion(KEY, {"aud": "homebutler", "sub": sub, "role": role, "iat": now, "exp": now + 60})}

    def test_webhook_always_answers_the_same(self):
        for body in (b"{}", b"not json", b"x" * 20000):
            self.assertEqual(self.client.post("/v2/switchbot/webhook", content=body).json(), {"ok": True})

    def test_theater_needs_a_signed_in_person_and_only_members_change_settings(self):
        self.assertEqual(self.client.get("/v2/theater").status_code, 401)
        async def fake(action, payload):
            return {"status": "success", "result": {"flags": payload}} if action == "set_flags" else {"status": "unknown"}
        self.app.state.ha.theater_command = fake
        self.assertEqual(self.client.get("/v2/theater", headers=self.headers()).status_code, 504)    # unknown: never retried
        reply = self.client.post("/v2/theater/flags", json={"kef_link": True}, headers=self.headers())
        self.assertEqual(reply.json(), {"agent_id": "home_assistant", "flags": {"kef_link": True}})
        self.assertEqual(self.client.post("/v2/theater/flags", json={}, headers=self.headers()).status_code, 400)
        self.assertEqual(self.client.post("/v2/theater/flags", json={"other": 1}, headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.post("/v2/theater/flags", json={"kef_link": False},
                                          headers=self.headers(str(self.remote), "remote")).status_code, 403)


if __name__ == "__main__":
    unittest.main()
