"""Real timing scopes with fake clocks, models and route dependencies; no I/O."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_callback_concurrency import endpoint
import test_voice_reply
from request_timing import request_timing, timing_stage, timed_model_call


def records(output):
    return [json.loads(c.args[0].removeprefix('[TIMING] ')) for c in output.call_args_list
            if c.args and isinstance(c.args[0], str) and c.args[0].startswith('[TIMING] ')]


class RequestTimingTests(unittest.TestCase):
    def test_duration_error_and_context_reset_without_logging_secrets(self):
        with patch('request_timing.print') as output, patch(
                'request_timing.perf_counter', side_effect=[0, 1, 3, 4]):
            with self.assertRaisesRegex(ValueError, 'secret'):
                with request_timing('siri_full'):
                    with timing_stage('sheets_load'):
                        raise ValueError('secret request text')
            with timing_stage('outside_request'):
                pass
        rows = records(output)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({r['request_id'] for r in rows}), 1)
        self.assertEqual(rows[2]['duration_ms'], 2000)
        self.assertEqual(rows[2]['error_type'], 'ValueError')
        self.assertEqual(rows[-1]['duration_ms'], 4000)
        self.assertEqual(rows[-1]['status'], 'error')
        self.assertNotIn('secret', json.dumps(rows))

    def test_concurrent_requests_have_isolated_ids_and_background_has_no_trace(self):
        barrier = Barrier(2)
        def run(source):
            with request_timing(source):
                barrier.wait(timeout=5)
                with timing_stage(source + '_stage'):
                    pass
        with patch('request_timing.print') as output, ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, s) for s in ('siri_full', 'siri_devices')]
            for future in futures:
                future.result(timeout=10)
            pool.submit(lambda: timed_model_call('background', lambda: object())).result()
        rows = records(output)
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({r['request_id'] for r in rows}), 2)
        for source in ('siri_full', 'siri_devices'):
            group = [r for r in rows if r['source'] == source]
            self.assertEqual(len({r['request_id'] for r in group}), 1)
            self.assertEqual(group[1]['stage'], source + '_stage')

    def test_model_metadata_and_logging_failure_preserve_single_call(self):
        response = SimpleNamespace(stop_reason='max_tokens', usage=SimpleNamespace(
            input_tokens=123, output_tokens=456), content='private response')
        create = Mock(return_value=response)
        with patch('request_timing.print') as output:
            with request_timing('siri_full'):
                self.assertIs(timed_model_call('ai_parse', create, system='private prompt'), response)
        create.assert_called_once_with(system='private prompt')
        usage = next(r for r in records(output) if r['event'] == 'model_usage')
        self.assertEqual(usage['stop_reason'], 'max_tokens')
        self.assertEqual(usage['input_tokens'], 123)
        self.assertNotIn('private', json.dumps(records(output)))
        with patch('request_timing.print', side_effect=OSError('log unavailable')):
            with request_timing('siri_full'):
                self.assertIs(timed_model_call('ai_parse', create), response)
        self.assertEqual(create.call_count, 2)

    def test_model_failure_is_not_retried_by_instrumentation(self):
        create = Mock(side_effect=TimeoutError('secret provider detail'))
        with patch('request_timing.print') as output:
            with self.assertRaises(TimeoutError):
                with request_timing('siri_devices'):
                    timed_model_call('ai_parse', create)
        create.assert_called_once_with()
        self.assertEqual(records(output)[-2]['status'], 'error')
        self.assertNotIn('secret', json.dumps(records(output)))

    def test_full_route_traces_model_actions_and_semantic_reply_under_one_id(self):
        run, handlers, _ = test_voice_reply.VoicePipelineTests().make_pipeline(
            [{'action': 'query_sensor'}], '', {'query_sensor': '濕度55%'})
        model = Mock(return_value=SimpleNamespace())
        def parse(*args):
            with timing_stage('context_prepare'):
                pass
            timed_model_call('ai_parse', model)
            return json.dumps({'actions': [{'action': 'query_sensor'}], 'reply': ''})
        def semantic(*args):
            timed_model_call('ai_reply', model)
            return '濕度55%'
        run.__globals__.update(ask_claude=parse, ask_claude_semantic=semantic)
        route = endpoint('web_api.py', 'api_assistant', {
            'SIRI_USER_ID': 'siri', 'RequestContext': lambda: SimpleNamespace(load=Mock()),
            'get_user_name': lambda *args: 'private name', 'process_message': run,
            'format_voice_reply': lambda text: text,
            'threading': SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: None)),
        })
        with patch('request_timing.print') as output:
            self.assertEqual(route(SimpleNamespace(text='private input', user_id=None)), {'reply': '濕度55%'})
        rows = records(output)
        self.assertEqual(len({r['request_id'] for r in rows}), 1)
        self.assertEqual([r['stage'] for r in rows if r['event'] == 'stage_end'],
                         ['sheets_load', 'identity', 'context_prepare', 'ai_parse', 'action.query_sensor', 'ai_reply'])
        self.assertEqual(rows[-1]['event'], 'request_end')
        self.assertEqual(model.call_count, 2)
        handlers['query_sensor'].assert_called_once()
        self.assertNotIn('private', json.dumps(rows))

    def test_actual_parser_fallback_is_timed_and_keeps_original_model_arguments(self):
        class BadRequestError(Exception):
            pass
        model = Mock(side_effect=[BadRequestError('test'), SimpleNamespace()])
        env = {'timing_stage': timing_stage, 'timed_model_call': timed_model_call,
               'now_taipei': lambda: datetime(2026, 9, 6), 'weekday_zh': lambda _: '日',
               'SYSTEM_PROMPT': '{current_user}', 'ACTION_SCHEMA': {'fake': True},
               'get_app_version': lambda: '1.39.0', 'get_recent_conversation': lambda *a: [],
               'claude': SimpleNamespace(messages=SimpleNamespace(create=model)),
               'anthropic': SimpleNamespace(BadRequestError=BadRequestError),
               '_response_text': lambda _: '{}', 'print': Mock()}
        for key in ('get_style_instruction', 'get_family_members_info', 'get_current_food',
                    'get_current_todo', 'get_device_info', 'get_lighting_area_info', 'get_schedule_info'):
            env[key] = lambda *a: ''
        parse = endpoint('conversation.py', 'ask_claude', env)
        with patch('request_timing.print') as output:
            with request_timing('siri_full'):
                self.assertEqual(parse('id', 'text', 'name', object()), '{}')
        ends = [r for r in records(output) if r['event'] == 'stage_end']
        self.assertEqual([(r['stage'], r['status']) for r in ends],
                         [('context_prepare', 'completed'), ('ai_parse', 'error'), ('ai_parse_fallback', 'completed')])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0].kwargs['thinking'], {'type': 'adaptive'})
        self.assertEqual(model.call_args_list[1].kwargs['thinking'], {'type': 'disabled'})

    def test_device_route_retains_handled_failure_and_request_correlation(self):
        from device_voice import run_device_voice
        ctx = SimpleNamespace(load=Mock(), get=Mock(return_value=[]))
        response = SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(
            {'actions': [{'action': 'query_devices', 'args': []}]}))])
        create = Mock(return_value=response)
        handler = Mock(side_effect=TimeoutError('private device detail'))
        route = endpoint('device_voice_api.py', 'api_device_voice', {
            'run_device_voice': run_device_voice, 'RequestContext': lambda: ctx,
            'claude': SimpleNamespace(messages=SimpleNamespace(create=create)),
            'DEVICE_HANDLERS': {'query_devices': handler},
        })
        with patch('request_timing.print') as output:
            reply = route(SimpleNamespace(text='有哪些設備'))
        self.assertIn('結果未確認', reply['reply'])
        rows = records(output)
        self.assertEqual(len({r['request_id'] for r in rows}), 1)
        self.assertEqual({r['source'] for r in rows}, {'siri_devices'})
        self.assertEqual([(r['stage'], r['status']) for r in rows if r['event'] == 'stage_end'],
                         [('sheets_load', 'completed'), ('context_prepare', 'completed'),
                          ('ai_parse', 'completed'), ('validate_actions', 'completed'),
                          ('action.query_devices', 'error')])
        # A handled error still returns a reply: completed is NOT device success.
        self.assertEqual(rows[-1]['status'], 'completed')
        create.assert_called_once()
        handler.assert_called_once()
        self.assertNotIn('private', json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
