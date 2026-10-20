import unittest

from homebutler.domain import households, members, remotes, tokens
from homebutler.domain.passwords import hash_password, verify_password
from homebutler.store.models import Member, Remote
from homebutler.tests.helpers import StoreTest


class KeyTests(StoreTest):
    def test_a_key_works_once_issued_only_for_its_kind_and_stops_when_revoked(self):
        with self.store.transaction() as session:
            token, plain = tokens.issue(session, "home_assistant", "HA")
            token_id = token.id
        self.assertTrue(plain.startswith("hb_home_assistant_"))
        with self.store.transaction() as session:
            self.assertNotIn(plain, [t.token_hash for t in session.query(type(token))])   # only the hash is kept
            self.assertIsNotNone(tokens.verify(session, plain, "home_assistant"))
            self.assertIsNone(tokens.verify(session, plain, "dashboard"))
            self.assertIsNone(tokens.verify(session, plain[:-1] + "x", "home_assistant"))
            self.assertIsNone(tokens.verify(session, "", "home_assistant"))
            self.assertTrue(tokens.revoke(session, token_id))
        with self.store.transaction() as session:
            self.assertIsNone(tokens.verify(session, plain, "home_assistant"))
        with self.assertRaises(ValueError):
            with self.store.transaction() as session:
                tokens.issue(session, "superuser", "x")


class MemberTests(StoreTest):
    def add(self, session, **values):
        home = households.ensure(session, "A")
        member = Member(household_id=home.id, **values); session.add(member); session.flush(); return member

    def test_only_active_members_count_and_alerts_fall_back_to_everyone(self):
        with self.store.transaction() as session:
            self.add(session, name="一", line_user_id="U1", active=True)
            self.add(session, name="二", line_user_id="U2", active=False)
            self.add(session, name="三", line_user_id="U3", active=True)
        with self.store.transaction() as session:
            self.assertEqual(members.active_by_line_id(session, "U1").name, "一")
            self.assertIsNone(members.active_by_line_id(session, "U2"))
            self.assertIsNone(members.active_by_line_id(session, "nobody"))
            self.assertEqual([m.name for m in members.alert_recipients(session)], ["一", "三"])   # nobody chose: everyone
            members.active_by_line_id(session, "U3").receives_alerts = True
        with self.store.transaction() as session:
            self.assertEqual([m.name for m in members.alert_recipients(session)], ["三"])


class RemoteTests(StoreTest):
    def test_passwords_are_hashed_and_checked(self):
        stored = hash_password("1234")
        self.assertNotIn("1234", stored)
        self.assertTrue(verify_password("1234", stored)); self.assertFalse(verify_password("1235", stored))
        self.assertFalse(verify_password("1234", "garbage"))

    def test_sign_in_finds_enabled_remotes_and_locks_after_repeated_failures(self):
        with self.store.transaction() as session:
            home = households.ensure(session, "A")
            session.add_all([Remote(name="客", household_id=home.id, password_hash=hash_password("1111"), enabled=True),
                             Remote(name="停", household_id=home.id, password_hash=hash_password("2222"), enabled=False)])
        now = [0.0]
        guard = remotes.Guard(clock=lambda: now[0])
        with self.store.transaction() as session:
            self.assertEqual(remotes.sign_in(session, "1111", guard).name, "客")
            self.assertIsNone(remotes.sign_in(session, "2222", guard))     # disabled
            for _ in range(remotes.MAX_FAILS - 1):
                remotes.sign_in(session, "wrong", guard)
            with self.assertRaises(remotes.LockedOut):
                remotes.sign_in(session, "1111", guard)
            now[0] += remotes.LOCKOUT_SECONDS + 1
            self.assertIsNotNone(remotes.sign_in(session, "1111", guard))


if __name__ == "__main__":
    unittest.main()
