"""Siri presentation tests; model, persistence and hardware calls are fakes."""

import json
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_callback_concurrency import endpoint
from voice_reply import format_voice_reply


class VoiceReplyTests(unittest.TestCase):
    def test_ir_acknowledgement_does_not_claim_observed_power_state(self):
        for button in ['開', '關', '電源']:
            self.assertEqual(format_voice_reply(f'✅ 主臥電扇「{button}」指令已送出'),
                             '已送出主臥電扇的電源指令。')
        self.assertEqual(format_voice_reply('✅ 主臥電扇「風速」指令已送出'),
                         '已送出主臥電扇的風速指令。')

    def test_emoji_and_markdown_are_removed_without_losing_choices(self):
        self.assertEqual(format_voice_reply('🌀 **主臥電扇**要開啟/關閉/調整風速？'),
                         '主臥電扇要開啟、關閉、調整風速？')
        self.assertEqual(format_voice_reply('## 狀態\n- 👨‍👩‍👧‍👦 家人都在\n- 👍🏽 好的 🇹🇼'),
                         '狀態。家人都在。好的。')

    def test_measurements_dates_and_negative_values_keep_their_meaning(self):
        self.assertEqual(format_voice_reply('🌡️ -3.5°C，濕度55%，2026/09/06 22:30'),
                         '-3.5度，濕度百分之55，2026年09月06日 22:30。')
        self.assertEqual(format_voice_reply('溫度≤26℃；比例3/4'), '溫度小於等於26度；比例3/4。')
        self.assertEqual(format_voice_reply('溫度26~28°'), '溫度26至28度。')

    def test_failure_unknown_and_clarifications_are_not_truncated(self):
        for text in ['❌ 指令結果未確認，請檢查設備狀態。系統不會自動重送。',
                     '❌ 開機成功，但設定風速失敗。', '⚠️ 要永久停止提醒嗎？']:
            self.assertEqual(format_voice_reply(text), text[2:].strip())

    def test_links_and_empty_decorations_have_speakable_output(self):
        self.assertEqual(format_voice_reply('請看[說明](https://example.com/a/b)。'), '請看說明。')
        self.assertEqual(format_voice_reply('https://example.com/a/b。指令失敗，請重查。'),
                         '已省略網址。指令失敗，請重查。')
        self.assertEqual(format_voice_reply('✅ 🌀'), '沒有可朗讀的回覆，請查看文字紀錄。')


class VoicePipelineTests(unittest.TestCase):
    def make_pipeline(self, actions, model_reply, handler_results):
        handlers = {action: Mock(return_value=result) for action, result in handler_results.items()}
        env = {
            'json': json, 're': re, 'print': Mock(),
            'ask_claude': Mock(return_value=json.dumps({'actions': actions, 'reply': model_reply})),
            '_flatten_action': lambda a: a,
            'ACTION_HANDLERS': handlers,
            'TRUTHFUL_ACTIONS': {'delete_todo', 'modify_todo'},
            'REALTIME_ACTIONS': {'query_devices', 'set_dehumidifier_auto'},
            'SEMANTIC_ACTIONS': {'query_sensor'},
            'ask_claude_semantic': Mock(return_value='濕度55% 🌀'),
        }
        return endpoint('assistant.py', 'process_message', env), handlers, env

    def test_voice_uses_actual_device_result_while_line_retains_model_reply(self):
        verbose = '已再次觸發主臥電扇電源鍵，若原本是開啟狀態，現在會變成關閉 🌀'
        actual = '✅ 主臥電扇「開」指令已送出'
        run, handlers, env = self.make_pipeline([{'action': 'control_ir'}], verbose, {'control_ir': actual})
        self.assertEqual(run('user', '打開主臥電扇', 'name', SimpleNamespace()), verbose)
        self.assertEqual(format_voice_reply(run('user', '打開主臥電扇', 'name', SimpleNamespace(), voice=True)),
                         '已送出主臥電扇的電源指令。')
        self.assertEqual(handlers['control_ir'].call_count, 2)
        self.assertEqual(env['ask_claude'].call_count, 2)
        env['ask_claude_semantic'].assert_not_called()

    def test_multiple_device_results_and_partial_failure_are_all_preserved(self):
        results = {'control_ir': '✅ 主臥電扇「開」指令已送出',
                   'control_ac': '❌ 冷氣開機成功，但溫度設定失敗。'}
        run, handlers, _ = self.make_pipeline([{'action': a} for a in results], '全部完成！', results)
        reply = format_voice_reply(run('user', '兩台都開', 'name', SimpleNamespace(), voice=True))
        self.assertEqual(reply, '已送出主臥電扇的電源指令。冷氣開機成功，但溫度設定失敗。')
        for handler in handlers.values(): handler.assert_called_once()

    def test_unclear_input_does_not_become_a_success_or_execute_a_device_action(self):
        run, handlers, _ = self.make_pipeline([{'action': 'unclear'}], '要開啟/關閉主臥電扇？ 🌀', {'unclear': None})
        reply = format_voice_reply(run('user', '主臥電扇', 'name', SimpleNamespace(), voice=True))
        self.assertEqual(reply, '要開啟、關閉主臥電扇？')
        self.assertEqual(list(handlers), ['unclear'])

    def test_mixed_command_and_clarification_keeps_the_question(self):
        run, _, _ = self.make_pipeline([{'action': 'control_ir'}, {'action': 'unclear'}],
                                      '已送出風扇指令。冷氣要調幾度？',
                                      {'control_ir': '✅ 主臥電扇「開」指令已送出', 'unclear': None})
        self.assertIn('冷氣要調幾度？', run('user', '測試', 'name', SimpleNamespace(), voice=True))

    def test_sensor_query_keeps_the_existing_semantic_query_path(self):
        run, _, env = self.make_pipeline([{'action': 'query_sensor'}], '', {'query_sensor': 'sensor data'})
        self.assertEqual(format_voice_reply(run('user', '濕度', 'name', SimpleNamespace(), voice=True)), '濕度百分之55。')
        env['ask_claude_semantic'].assert_called_once()

    def test_api_keeps_contract_and_saves_the_spoken_reply_without_changing_input(self):
        ctx = SimpleNamespace(load=Mock())
        process = Mock(return_value='✅ 主臥電扇「開」指令已送出')
        save = Mock()
        env = {
            'SIRI_USER_ID': 'siri', 'RequestContext': lambda: ctx,
            'get_user_name': lambda *args: 'name', 'process_message': process,
            'format_voice_reply': format_voice_reply, 'save_conversation': save,
            'cleanup_conversation': Mock(),
            'threading': SimpleNamespace(Thread=lambda target, daemon: SimpleNamespace(start=target)),
        }
        run = endpoint('web_api.py', 'api_assistant', env)
        response = run(SimpleNamespace(text=' 打開主臥電風扇 ', user_id='user'))
        self.assertEqual(response, {'reply': '已送出主臥電扇的電源指令。'})
        process.assert_called_once_with('user', '打開主臥電風扇', 'name', ctx, voice=True)
        self.assertEqual(save.call_args_list[0].args, ('user', 'user', '打開主臥電風扇'))
        self.assertEqual(save.call_args_list[1].args, ('user', 'assistant', response['reply']))


if __name__ == '__main__':
    unittest.main()
