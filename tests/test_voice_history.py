"""Inspect real parser payloads with fake SDK; no paid calls or household I/O."""

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from request_timing import timing_stage, timed_model_call
from test_callback_concurrency import endpoint


class VoiceHistoryTests(unittest.TestCase):
    def parser(self, fallback=False):
        class BadRequestError(Exception):
            pass
        response = object()
        create = Mock(side_effect=[BadRequestError('fake'), response] if fallback else [response])
        history = Mock(return_value=[{'role': 'user', 'content': 'old request'},
                                    {'role': 'assistant', 'content': 'old response'}])
        env = {
            'timing_stage': timing_stage, 'timed_model_call': timed_model_call,
            'now_taipei': lambda: datetime(2026, 9, 6), 'weekday_zh': lambda _: '日',
            'SYSTEM_PROMPT': '{current_user} {device_info}', 'ACTION_SCHEMA': {'type': 'object'},
            'get_app_version': lambda: 'test', 'get_recent_conversation': history,
            'claude': SimpleNamespace(messages=SimpleNamespace(create=create)),
            'anthropic': SimpleNamespace(BadRequestError=BadRequestError),
            '_response_text': lambda _: '{}', 'print': Mock(),
        }
        for name in ('get_style_instruction', 'get_family_members_info', 'get_current_food',
                     'get_current_todo', 'get_device_info', 'get_lighting_area_info', 'get_schedule_info'):
            env[name] = Mock(return_value='live context')
        return endpoint('conversation.py', 'ask_claude', env), create, history

    def test_siri_sends_only_current_message_even_on_schema_fallback(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                parse, create, history = self.parser(fallback)
                self.assertEqual(parse('member-id', '主臥冷氣調到26度', 'member', object(),
                                       include_history=False), '{}')
                history.assert_not_called()
                self.assertEqual(create.call_count, 2 if fallback else 1)
                for call in create.call_args_list:
                    self.assertEqual(call.kwargs['messages'],
                                     [{'role': 'user', 'content': '主臥冷氣調到26度'}])
                    self.assertEqual(call.kwargs['system'], 'member live context')

    def test_default_line_behavior_retains_history_in_normal_and_fallback_calls(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                parse, create, history = self.parser(fallback)
                ctx = object()
                parse('member-id', '再低一度', 'member', ctx)
                history.assert_called_once_with('member-id', ctx)
                for call in create.call_args_list:
                    self.assertEqual(call.kwargs['messages'], history.return_value +
                                     [{'role': 'user', 'content': '再低一度'}])


if __name__ == '__main__':
    unittest.main()
