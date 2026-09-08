"""Connection lifecycle and fresh state writes using fake Sheets; no credentials."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_callback_concurrency import endpoint


def a1(row, col):
    letters = ''
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f'{letters}{row}'


class SheetsReuseTests(unittest.TestCase):
    def connection(self, factory):
        env = {'_spreadsheet': None, '_spreadsheet_lock': threading.RLock(), '_get_client': factory}
        get = endpoint('sheets.py', '_get_spreadsheet', env)
        invalidate = endpoint('sheets.py', '_invalidate_spreadsheet', env)
        return get, invalidate, env

    def test_one_connection_reused_across_concurrent_callers_and_after_time_passes(self):
        factory = Mock(return_value=object())
        get, invalidate, env = self.connection(factory)
        barrier = threading.Barrier(4)
        def run():
            barrier.wait(timeout=5)
            return get()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: run(), range(4)))
        self.assertTrue(all(obj is factory.return_value for obj in results))
        # Regression against the previous time-based eviction, independent of
        # actual wall clock. A future time must not trigger credential recreation.
        env.update(time=SimpleNamespace(time=lambda: 10**12), _spreadsheet_time=0, _sheets_cache_ttl=60)
        self.assertIs(get(), factory.return_value)
        factory.assert_called_once()
        invalidate()
        self.assertIs(get(), factory.return_value)
        self.assertEqual(factory.call_count, 2)

    def test_failed_initialization_is_not_cached_and_does_not_deadlock_invalidation(self):
        factory = Mock()
        get, invalidate, env = self.connection(factory)
        def fail():
            invalidate()  # GET retry exhaustion during open_by_key.
            raise TimeoutError('fake')
        factory.side_effect = fail
        with self.assertRaises(TimeoutError):
            get()
        self.assertIsNone(env['_spreadsheet'])
        factory.side_effect = None
        expected = get()
        self.assertIs(get(), expected)
        self.assertEqual(factory.call_count, 2)

    def test_exhausted_get_invalidates_connection_but_does_not_retry_nontransient(self):
        get, invalidate, env = self.connection(Mock(return_value=object()))
        get()
        env.update(_retry_off=threading.local(), _RETRY_ATTEMPTS=3, _RETRY_BASE_SLEEP=0,
                   _is_transient=lambda e: isinstance(e, TimeoutError),
                   time=SimpleNamespace(sleep=Mock()), random=SimpleNamespace(uniform=lambda *a: 0),
                   print=Mock())
        retry = endpoint('sheets.py', '_with_retry', env)
        op = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            retry(op)
        self.assertEqual(op.call_count, 3)
        self.assertIsNone(env['_spreadsheet'])
        get()
        self.assertEqual(env['_get_client'].call_count, 2)
        op = Mock(side_effect=ValueError())
        with self.assertRaises(ValueError):
            retry(op)
        op.assert_called_once()

    def writer(self, values):
        ss = SimpleNamespace(values_get=Mock(return_value={'values': values}),
                             values_batch_update=Mock(), worksheet=Mock())
        env = {'_get_spreadsheet': lambda: ss, 'rowcol_to_a1': a1}
        endpoint('sheets.py', '_parse_sheet_values', env)
        return endpoint('sheets.py', 'update_device_state_fields', env), ss

    def test_fresh_row_and_header_positions_blank_rows_and_columns_are_respected(self):
        write, ss = self.writer([['Device ID', '名稱', '最後電源', '最後溫度'],
                                 ['other', '其他', 'on', '27'], [], ['target', '主臥', 'off', '26']])
        row, applied = write('target', {'最後電源': 'on', '最後溫度': 25, '未知欄': 'ignored'})
        self.assertEqual(row['最後溫度'], 25)
        self.assertEqual(applied, {'最後電源': 'on', '最後溫度': 25})
        self.assertEqual(ss.values_batch_update.call_args.args[0], {'valueInputOption': 'RAW', 'data': [
            {'range': "'智能居家'!C4", 'values': [['on']]}, {'range': "'智能居家'!D4", 'values': [[25]]}]})
        # Between requests the user moves both columns and the target row.
        ss.values_get.return_value = {'values': [['最後溫度', '', '名稱', 'Device ID', '最後電源'],
                                                ['25', '', '主臥', 'target', 'on']]}
        write('target', {'最後電源': 'off'})
        self.assertEqual(ss.values_batch_update.call_args.args[0]['data'],
                         [{'range': "'智能居家'!E2", 'values': [['off']]}])
        self.assertEqual(ss.values_get.call_count, 2)
        self.assertEqual(ss.values_batch_update.call_count, 2)
        ss.worksheet.assert_not_called()

    def test_missing_renamed_or_ambiguous_sheet_layout_never_writes(self):
        grids = [[], [['新ID欄', '最後電源'], ['target', 'off']],
                 [['Device ID', '最後電源'], ['other', 'off']],
                 [['Device ID', '最後電源'], ['target', 'off'], ['target', 'on']],
                 [['Device ID', '最後電源', '最後電源'], ['target', 'off', 'on']],
                 [['Device ID', '名稱'], ['target', '主臥']]]
        for grid in grids:
            with self.subTest(grid=grid):
                write, ss = self.writer(grid)
                with self.assertRaises(ValueError):
                    write('target', {'最後電源': 'on'})
                ss.values_batch_update.assert_not_called()
        write, ss = self.writer([])
        ss.values_get.side_effect = ValueError('renamed sheet / invalid range')
        with self.assertRaises(ValueError):
            write('target', {'最後電源': 'on'})
        ss.worksheet.assert_not_called()  # No fallback to a different worksheet.
        ss.values_batch_update.assert_not_called()

    def test_unknown_write_outcome_is_not_retried(self):
        write, ss = self.writer([['Device ID', '最後電源'], ['target', 'off']])
        ss.values_batch_update.side_effect = TimeoutError('may already have committed')
        with self.assertRaises(TimeoutError):
            write('target', {'最後電源': 'on'})
        ss.values_batch_update.assert_called_once()

    def test_feedback_marker_missing_column_prevents_partial_state_write(self):
        write, ss = self.writer([['Device ID', '最後電源'], ['target', 'off']])
        fields = {'最後電源': 'on', '空調溫度回饋狀態': '{}'}
        with self.assertRaises(ValueError):
            write('target', fields, required_fields=fields.keys())
        ss.values_batch_update.assert_not_called()

    def test_manual_state_save_atomically_restores_comfort_and_ir_temperatures(self):
        import json
        grid = [['Device ID', '名稱', '最後電源', '最後溫度', '最後模式', '最後風速',
                 '最後更新時間', '空調溫度回饋狀態'], ['target', '主臥', 'on', 26, '冷氣', '低', '', '{}']]
        write, ss = self.writer(grid)
        save, status = self.saver(write)
        ctx = SimpleNamespace(get=lambda _: [{'Device ID': 'target'}],
            _feedback_state={'blocked': True, 'ir_temperature': 24})
        save(ctx, 'target', 'on', 27, 2, 2)
        self.assertTrue(ctx._ac_state_saved)
        writes = ss.values_batch_update.call_args.args[0]['data']
        self.assertEqual(next(w for w in writes if w['range'].endswith('D2'))['values'], [[27]])
        saved = json.loads(next(w for w in writes if w['range'].endswith('H2'))['values'][0][0])
        self.assertEqual(saved['ir_temperature'], 27)
        self.assertFalse(saved['blocked'])
        self.assertEqual(status.update.call_args.kwargs['fields']['lastTemperature'], 27)

    def saver(self, write):
        status = SimpleNamespace(update=Mock())
        save = endpoint('handlers/device.py', '_save_ac_last_state', {
            'update_device_state_fields': write, 'device_status': status,
            'now_taipei': lambda: datetime(2026, 9, 7, 0, 30),
            '_AC_MODE_LABEL': {2: '冷氣', 4: '送風'}, '_AC_FAN_LABEL': {1: '自動', 2: '低'},
            'print': Mock()})
        return save, status

    def test_state_save_keeps_power_anchor_and_antimold_restore_and_updates_by_id(self):
        grid = [['Device ID', '名稱', '最後電源', '最後溫度', '最後模式', '最後風速', '最後開機時間', '最後更新時間'],
                ['target', '主臥', 'off', '27', '冷氣', '低', '', '']]
        write, ss = self.writer(grid)
        save, status = self.saver(write)
        cache = [{'Device ID': 'other', '最後電源': 'off'}, {'Device ID': 'target', '最後電源': 'off'}]
        ctx = SimpleNamespace(get=lambda name: cache)
        save(ctx, 'target', 'on', 26, 2, 2, mark_on_time=True)
        self.assertEqual(cache[1]['最後開機時間'], '2026-09-07 00:30')
        self.assertEqual(cache[1]['最後模式'], '冷氣')
        self.assertEqual(cache[0], {'Device ID': 'other', '最後電源': 'off'})
        save(ctx, 'target', 'on', 26, 4, 1)  # Antimold fan mode must not reset anchor.
        self.assertEqual(cache[1]['最後開機時間'], '2026-09-07 00:30')
        self.assertEqual(cache[1]['最後模式'], '送風')
        save(ctx, 'target', 'off', restore_on_off={'最後模式': '冷氣', '最後溫度': 26, '最後風速': '低'})
        self.assertEqual(cache[1]['最後開機時間'], '')
        self.assertEqual(cache[1]['最後模式'], '冷氣')
        self.assertEqual(cache[1]['最後溫度'], 26)
        self.assertEqual(status.update.call_count, 3)

    def test_save_failure_does_not_advance_request_or_dashboard_state(self):
        write = Mock(side_effect=TimeoutError('fake'))
        save, status = self.saver(write)
        cache = [{'Device ID': 'target', '最後電源': 'off'}]
        save(SimpleNamespace(get=lambda name: cache), 'target', 'on', 26, 2, 2)
        self.assertEqual(cache, [{'Device ID': 'target', '最後電源': 'off'}])
        status.update.assert_not_called()
        write.assert_called_once()


if __name__ == '__main__':
    unittest.main()
