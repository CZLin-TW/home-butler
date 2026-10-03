"""Execute the real LINE handler with fake I/O; never import/start the app."""
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call

from test_callback_concurrency import endpoint


class LineMemberAuthTests(unittest.TestCase):
    def setUp(self):
        self.members = [
            {"Line User ID": "U-enabled", "名稱": "家人", "狀態": "啟用", "管家風格": "簡潔"},
            {"Line User ID": "U-disabled", "名稱": "停用家人", "狀態": "停用"},
            {"Line User ID": "U-other", "名稱": "另一位", "狀態": "啟用"},
            {"Line User ID": "", "名稱": "未綁定", "狀態": "啟用"},
        ]
        self.records = Mock(return_value=self.members)
        self.sheet = Mock(return_value=SimpleNamespace(get_all_records=self.records))
        self.line = Mock()
        self.line.get_profile.return_value = SimpleNamespace(picture_url="fake-picture")
        self.ctx = SimpleNamespace(load=Mock(), get=Mock(return_value=self.members))
        self.context = Mock(return_value=self.ctx)
        self.process = Mock(return_value="已處理")
        self.approve = Mock(return_value="member")
        self.save, self.cleanup = Mock(), Mock()
        self.loading = Mock(return_value=SimpleNamespace(status_code=200, text=""))
        self.thread = Mock(side_effect=lambda target, daemon: SimpleNamespace(start=target))
        self.log = Mock()
        self.handler = endpoint("main.py", "handle_message", {
            "get_sheet": self.sheet, "line_bot_api": self.line,
            "TextSendMessage": SimpleNamespace, "RequestContext": self.context,
            "get_user_name": Mock(return_value="家人"), "process_message": self.process,
            "device_auth": SimpleNamespace(approve=self.approve),
            "save_conversation": self.save, "cleanup_conversation": self.cleanup,
            "httpx": SimpleNamespace(post=self.loading), "LINE_CHANNEL_ACCESS_TOKEN": "fake",
            "threading": SimpleNamespace(Thread=self.thread), "re": re,
            "traceback": SimpleNamespace(format_exc=lambda: "fake-error"),
            "is_transient_error": lambda error: False, "print": self.log,
        })

    def event(self, text="打開客廳冷氣", user_id="U-enabled", kind="user"):
        return SimpleNamespace(source=SimpleNamespace(type=kind, user_id=user_id,
                               group_id="U-enabled", room_id="U-enabled"),
                               message=SimpleNamespace(text=text), reply_token="fake-reply")

    def assert_denied(self):
        self.process.assert_not_called()
        self.context.assert_not_called()
        self.line.push_message.assert_not_called()
        self.line.get_profile.assert_not_called()
        self.approve.assert_not_called()
        self.loading.assert_not_called()
        self.save.assert_not_called()
        self.cleanup.assert_not_called()
        self.thread.assert_not_called()
        self.line.reply_message.assert_called_once()
        self.assertIn("家庭成員", self.line.reply_message.call_args.args[1].text)

    def test_enabled_member_reaches_assistant_and_saves_existing_history(self):
        self.handler(self.event())
        self.process.assert_called_once_with("U-enabled", "打開客廳冷氣", "家人", self.ctx)
        self.ctx.load.assert_called_once_with()
        self.save.assert_has_calls([call("U-enabled", "user", "打開客廳冷氣"),
                                   call("U-enabled", "assistant", "已處理")])
        self.cleanup.assert_called_once_with("U-enabled")

    def test_unknown_and_disabled_are_denied_for_every_branch(self):
        for uid in ("U-unknown", "U-disabled", "家人", "U-enabled "):
            for text in ("打開客廳冷氣", "@all 全體通知", "@all", "查看風格", "我的風格",
                         "目前風格", "登入 123456", "配對兒童 123456", "登入很麻煩"):
                with self.subTest(uid=uid, text=text):
                    self.setUp()
                    self.handler(self.event(text, uid))
                    self.assert_denied()

    def test_missing_user_id_never_uses_group_or_room_id(self):
        for kind in ("user", "group", "room"):
            for uid in (None, "", "absent"):
                with self.subTest(kind=kind, uid=uid):
                    self.setUp()
                    event = self.event("@all 通知", uid, kind)
                    if uid == "absent":
                        del event.source.user_id
                    self.handler(event)
                    self.assert_denied()
                    self.sheet.assert_not_called()

    def test_group_and_room_require_the_speaker_to_be_enabled(self):
        for kind in ("group", "room"):
            for uid in ("U-enabled", "U-unknown", "U-disabled"):
                with self.subTest(kind=kind, uid=uid):
                    self.setUp()
                    self.handler(self.event(user_id=uid, kind=kind))
                    if uid == "U-enabled":
                        self.process.assert_called_once()
                    else:
                        self.assert_denied()

    def test_lookup_failure_fails_closed_for_every_branch(self):
        for failure in ("worksheet", "records", "malformed"):
            for text in ("打開客廳冷氣", "@all 通知", "查看風格", "登入 123456", "配對兒童 123456"):
                with self.subTest(failure=failure, text=text):
                    self.setUp()
                    if failure == "worksheet":
                        self.sheet.side_effect = RuntimeError("unavailable")
                    elif failure == "records":
                        self.records.side_effect = RuntimeError("unavailable")
                    else:
                        self.records.return_value = [None]
                    self.handler(self.event(text))
                    self.assert_denied()
                    self.assertIn("暫時無法驗證", self.line.reply_message.call_args.args[1].text)

    def test_empty_or_missing_member_fields_are_denied(self):
        for rows in ([], [{"Line User ID": "U-enabled"}], [{"狀態": "啟用"}],
                     [{"Line User ID": "U-enabled", "狀態": "停用"}]):
            with self.subTest(rows=rows):
                self.setUp()
                self.records.return_value = rows
                self.handler(self.event())
                self.assert_denied()

    def test_enabled_broadcast_only_sends_to_enabled_linked_members(self):
        self.handler(self.event("@all 測試"))
        self.assertEqual([c.args[0] for c in self.line.push_message.call_args_list],
                         ["U-enabled", "U-other"])
        self.assertTrue(all(c.args[1].text == "📢 家人：測試"
                            for c in self.line.push_message.call_args_list))
        self.records.assert_called_once_with()
        self.process.assert_not_called()
        self.approve.assert_not_called()
        self.assertEqual(self.save.call_count, 2)

    def test_enabled_empty_broadcast_keeps_help(self):
        self.handler(self.event("@all"))
        self.line.push_message.assert_not_called()
        self.assertIn("輸入廣播內容", self.line.reply_message.call_args.args[1].text)

    def test_enabled_style_keeps_response(self):
        for text in ("查看風格", "我的風格", "目前風格"):
            with self.subTest(text=text):
                self.setUp()
                self.handler(self.event(text))
                self.assertIn("簡潔", self.line.reply_message.call_args.args[1].text)
                self.process.assert_not_called()

    def test_pairing_preserves_roles_and_invalid_code_response(self):
        for text, requested in (("登入 123 456", "member"), ("配對兒童 123456", "kid")):
            for result in ("member", "kid", None):
                with self.subTest(text=text, result=result):
                    self.setUp()
                    self.approve.return_value = result
                    self.handler(self.event(text))
                    self.approve.assert_called_once_with("123456", "U-enabled", "家人",
                                                         "fake-picture", requested_role=requested)
                    reply = self.line.reply_message.call_args.args[1].text
                    self.assertIn("兒童遙控器" if result == "kid" else "授權登入" if result else "已過期", reply)
                    self.process.assert_not_called()
                    self.save.assert_not_called()

    def test_new_member_id_remains_available_to_admin_without_saving_conversation(self):
        self.handler(self.event(user_id="U-new"))
        self.assert_denied()
        self.log.assert_called_once_with("[LINE AUTH] denied user_id=U-new")

    def test_failed_denial_reply_does_not_fall_through(self):
        self.line.reply_message.side_effect = RuntimeError("LINE unavailable")
        with self.assertRaises(RuntimeError):
            self.handler(self.event(user_id="U-unknown"))
        self.assert_denied()


if __name__ == "__main__":
    unittest.main()
