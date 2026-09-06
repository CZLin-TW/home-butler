"""Count external operations without contacting Sheets, models or devices."""

from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from request_timing import request_timing, timing_stage
from test_callback_concurrency import endpoint
from test_request_timing import records


class VoiceIOTests(unittest.TestCase):
    def test_prompt_lighting_is_read_only_and_keeps_names_and_disabled_filter(self):
        rows = [{'Hue ID': '1', '顯示名稱': '主臥', 'Hue 名稱': 'Bedroom'},
                {'Hue ID': '2', 'Hue 名稱': '客廳'},
                {'Hue ID': '3', '顯示名稱': '停用房', '狀態': '停用'},
                {'Hue ID': '', '顯示名稱': '忽略'}]
        get_records = Mock(return_value=rows)
        worksheet = Mock(side_effect=AssertionError('must not create/repair sheets'))
        load = endpoint('hue_area_settings.py', 'load_area_settings', {
            'get_sheet_records': get_records, 'SHEET_NAME': 'Hue 照明區域', '_worksheet': worksheet})
        prompt = endpoint('prompt.py', 'get_lighting_area_info', {
            'load_area_settings': load, 'DEFAULT_LIGHT_AREA_NAME': '客廳'})
        self.assertEqual(prompt(object()), '主臥、客廳')
        get_records.assert_called_once_with('Hue 照明區域')
        worksheet.assert_not_called()
        get_records.side_effect = RuntimeError('sheet unavailable')
        self.assertIn('尚未取得照明區域', prompt(object()))
        worksheet.assert_not_called()

    def test_settings_default_keeps_schema_maintenance(self):
        worksheet = Mock(return_value=SimpleNamespace(get_all_records=Mock(return_value=[])))
        read = Mock()
        load = endpoint('hue_area_settings.py', 'load_area_settings', {
            '_worksheet': worksheet, 'get_sheet_records': read, 'SHEET_NAME': 'Hue 照明區域'})
        self.assertEqual(load(), {})
        worksheet.assert_called_once()
        read.assert_not_called()

    def schedule(self, rows, power='on', hours=2):
        data = {'智能居家': [{'名稱': '測試冷氣', '類型': '空調', '狀態': '啟用',
                         '最後電源': power, '自動關機小時數': hours}], '排程指令': rows}
        sheets = {n: SimpleNamespace(row_values=Mock(return_value=['狀態', '觸發時間']),
                                    append_row=Mock(), delete_rows=Mock()) for n in ('排程指令', '排程封存')}
        ctx = SimpleNamespace(get=lambda name: data[name], get_worksheet=Mock(side_effect=sheets.__getitem__))
        fn = endpoint('handlers/device.py', 'maintain_ac_auto_schedule', {
            '_parse_int_safe': int, '_is_ac_off_action': lambda r: r.get('動作') == 'control_ac',
            'now_taipei': lambda: datetime(2026, 9, 6, 12), 'timedelta': timedelta, 'json': json,
            'build_row': lambda headers, row: [row.get(h, '') for h in headers]})
        return fn, ctx, sheets

    def auto_row(self):
        return {'設備名稱': '測試冷氣', '狀態': '待執行', '來源': '自動', '觸發時間': '2026-09-06 13:00'}

    def test_noop_schedule_skips_all_worksheet_metadata_without_resetting_timer(self):
        row = self.auto_row()
        for rows, power, hours in (([row.copy()], 'on', 2), ([], 'off', 2), ([], 'on', 0)):
            with self.subTest(power=power, hours=hours):
                fn, ctx, sheets = self.schedule(rows, power, hours)
                before = [r.copy() for r in rows]
                fn('測試冷氣', ctx)
                ctx.get_worksheet.assert_not_called()
                self.assertEqual(rows, before)

    def test_add_schedule_does_not_fetch_archive_and_still_updates_context(self):
        rows = []
        fn, ctx, sheets = self.schedule(rows)
        fn('測試冷氣', ctx)
        ctx.get_worksheet.assert_called_once_with('排程指令')
        sheets['排程指令'].append_row.assert_called_once_with(['待執行', '2026-09-06 14:00'])
        self.assertEqual(rows[0]['來源'], '自動')

    def test_reset_and_cancel_preserve_archive_before_delete_and_context(self):
        for transition, power in ((True, 'on'), (False, 'off')):
            with self.subTest(transition=transition):
                rows = [self.auto_row()]
                fn, ctx, sheets = self.schedule(rows, power)
                order = []
                sheets['排程封存'].append_row.side_effect = lambda row: order.append('archive')
                sheets['排程指令'].delete_rows.side_effect = lambda row: order.append('delete')
                sheets['排程指令'].append_row.side_effect = lambda row: order.append('add')
                fn('測試冷氣', ctx, transitioned_to_on=transition)
                self.assertEqual(order, ['archive', 'delete', 'add'] if transition else ['archive', 'delete'])
                self.assertEqual(len(rows), 1 if transition else 0)

    def test_nested_timing_parents_and_restoration(self):
        with patch('request_timing.print') as output:
            with request_timing('siri_full'):
                with timing_stage('context_prepare'):
                    try:
                        with timing_stage('context.lighting'):
                            raise ValueError('private')
                    except ValueError:
                        pass
                    with timing_stage('context.version'):
                        pass
        starts = [r for r in records(output) if r['event'] == 'stage_start']
        self.assertIsNone(starts[0]['parent_span_id'])
        self.assertEqual(starts[1]['parent_span_id'], starts[0]['span_id'])
        self.assertEqual(starts[2]['parent_span_id'], starts[0]['span_id'])

    def test_sheets_http_timing_preserves_retry_boundary_and_redacts_url(self):
        original = Mock(return_value='ok')
        class Client:
            def request(self, *a, **kw):
                return original(*a, **kw)
        retry = Mock(side_effect=lambda call, what: call())
        install = endpoint('sheets.py', '_install_gspread_get_retry', {
            'gspread': SimpleNamespace(http_client=SimpleNamespace(HTTPClient=Client)), '_with_retry': retry})
        install()
        install()  # Must not stack the monkeypatch.
        with patch('request_timing.print') as output:
            with request_timing('siri_full'):
                for method, url in [('GET', 'https://fake/private-id/values/private-range'),
                                    ('GET', 'https://fake/private-id'), ('POST', 'https://fake/private-id')]:
                    self.assertEqual(Client().request(method, url), 'ok')
        self.assertEqual(retry.call_count, 2)
        self.assertEqual(original.call_count, 3)
        rows = records(output)
        self.assertNotIn('private', json.dumps(rows))
        self.assertEqual([r['stage'] for r in rows if r['event'] == 'stage_end'],
                         ['sheets.http.values_read', 'sheets.http.metadata_read', 'sheets.http.write'])


if __name__ == '__main__':
    unittest.main()
