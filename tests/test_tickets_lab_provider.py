"""Synthetic lab layouts only. No live spreadsheets or private roster fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from hermes_zalo.lab_calendar import HEADER
from hermes_zalo.tickets.lab_provider import GoogleLabAssignments, LabSheetsSettings
from hermes_zalo.tickets.providers import TicketsSourceError, TicketsWriteUncertain
from hermes_zalo.tickets.service import TicketService, TicketStore, packed


SID = 'synthetic_lab_spreadsheet'
NOW = datetime.fromisoformat('2035-10-09T12:00:00+07:00')
PERIODS = {'Sáng': {'start': '08:00', 'end': '11:00'},
           'Chiều': {'start': '13:00', 'end': '16:00'},
           'Tối': {'start': '18:00', 'end': '21:00'}}


def value(text):
    return {'formattedValue': text, 'userEnteredValue': {'stringValue': text},
            'effectiveValue': {'stringValue': text}}


def fixture():
    rows = [
        ['LỊCH LAB MẪU'], ['Thứ 4 - 10/10/2035'], HEADER + ['Ghi chú', 'Trạng thái'],
        ['1', 'Track A', 'Session A', 'Coach Alpha', 'Key Gamma', 'R101', 'Sáng', '20'],
        ['2', '', '', 'Coach Other', '', '', '', '', '', ''], [],
        ['Thứ 5 - 11/10/2035'], HEADER + ['Ghi chú', 'Trạng thái'],
        ['1', 'Track B', 'Session B', 'Coach Beta', 'Key Gamma', 'R102', 'Chiều', '20'],
    ]
    return {'spreadsheetId': SID, 'sheets': [{
        'properties': {'sheetId': 17, 'title': 'Lịch lab thử', 'gridProperties': {'rowCount': 1000}},
        'data': [{'rowData': [{'values': [value(cell) for cell in row]} for row in rows]}],
        'merges': [{'startRowIndex': 3, 'endRowIndex': 5, 'startColumnIndex': column, 'endColumnIndex': column + 1}
                   for column in (1, 2, 4, 5, 6)],
    }]}


def set_cell(payload, row, col, text):
    cells = payload['sheets'][0]['data'][0]['rowData'][row]['values']
    cells.extend({} for _ in range(col + 1 - len(cells)))
    cells[col] = value(text)


def config(**extra):
    return LabSheetsSettings.from_mapping(dict(
        ZALO_CALENDAR_FORMAT='lab', ZALO_SHEETS_ID=SID, ZALO_GOOGLE_CREDENTIALS='fake-key.json',
        ZALO_CALENDAR_TABS_JSON='["Lịch lab thử"]', TICKETS_LAB_PERIODS_JSON=json.dumps(PERIODS),
        TICKETS_LAB_WRITE_ENABLED='true', TICKETS_LAB_EXCLUSIVE_WRITER='true') | extra)


def test_values(**extra):
    return dict(ZALO_CALENDAR_FORMAT='lab', ZALO_SHEETS_ID=SID, ZALO_GOOGLE_CREDENTIALS='fake-key.json',
                ZALO_CALENDAR_TABS_JSON='["Lịch lab thử"]',
                TICKETS_LAB_TEST_MEMBERS_JSON='["Coach Alpha","Coach Beta"]',
                TICKETS_LAB_TEST_WRITE_ENABLED='true', TICKETS_LAB_TEST_EXCLUSIVE_WRITER='true') | extra


class LabLiveConfigTests(unittest.TestCase):
    def test_no_default_hours_but_onboarding_can_construct_settings(self):
        values = dict(ZALO_CALENDAR_FORMAT='lab', ZALO_SHEETS_ID=SID, ZALO_GOOGLE_CREDENTIALS='secret-file.json',
                      ZALO_CALENDAR_TABS_JSON='["Lịch lab thử"]')
        settings = LabSheetsSettings.from_mapping(values)
        self.assertEqual({}, settings.periods)
        self.assertFalse(settings.write_enabled)
        self.assertNotIn('secret-file', repr(settings))
        for changes in ({'ZALO_CALENDAR_FORMAT': 'standard'}, {'ZALO_CALENDAR_TABS_JSON': '[]'},
                        {'TICKETS_LAB_WRITE_ENABLED': 'true'},
                        {'TICKETS_LAB_PERIODS_JSON': '{"Other":{"start":"08:00","end":"11:00"}}'},
                        {'TICKETS_LAB_PERIODS_JSON': json.dumps(PERIODS | {'Sáng': {'start': '24:00', 'end': '11:00'}})},
                        {'ZALO_SHEETS_ID': 'https://docs.google.com/test'},
                        {'ZALO_CALENDAR_TABS_JSON': '["Bad\u0027!A1"]'}):
            with self.subTest(changes=changes), self.assertRaises(TicketsSourceError):
                LabSheetsSettings.from_mapping(values | changes)

    def test_only_confirmed_periods_are_required_in_configuration(self):
        settings = config(TICKETS_LAB_PERIODS_JSON=json.dumps({'Sáng': PERIODS['Sáng']}))
        self.assertEqual({'Sáng': PERIODS['Sáng']}, settings.periods)
        for periods in ([], {'Sáng': None}, {'Sáng': {'start': '08:00', 'end': '08:00'}},
                        {'Sáng': {'start': '08:00', 'end': '07:00'}},
                        {'Sáng': {'start': '08:00', 'end': '11:00', 'guess': True}}):
            with self.subTest(periods=periods), self.assertRaises(TicketsSourceError):
                config(TICKETS_LAB_PERIODS_JSON=json.dumps(periods))


class LabLiveProviderTests(unittest.TestCase):
    def setUp(self):
        self.payload = fixture()
        self.calls = []
        self.now = NOW
        self.post_behavior = 'success'

    def handler(self, request):
        self.calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json=self.payload)
        if self.post_behavior == 'timeout':
            raise httpx.ReadTimeout('private-data-not-for-output')
        if self.post_behavior == 'bad200':
            return httpx.Response(200, json={})
        if self.post_behavior == '500':
            return httpx.Response(500, json={'error': 'private-data-not-for-output'})
        for operation in json.loads(request.content)['requests']:
            update = operation['updateCells']
            for sheet in self.payload['sheets']:
                if sheet['properties']['sheetId'] == update['range']['sheetId']:
                    row = update['range']['startRowIndex']
                    name = update['rows'][0]['values'][0]['userEnteredValue']['stringValue']
                    sheet['data'][0]['rowData'][row]['values'][3] = value(name)
        if self.post_behavior == 'after_write_timeout':
            raise httpx.ReadTimeout('response-lost')
        return httpx.Response(200, json={'spreadsheetId': SID, 'replies': [{}, {}]})

    def provider(self, *, settings=None, **changes):
        settings = settings or config()
        if changes:
            from dataclasses import replace
            settings = replace(settings, **changes)
        provider = GoogleLabAssignments(settings, client=httpx.Client(transport=httpx.MockTransport(self.handler)),
                                        token_provider=lambda: 'fake-token', clock=lambda: self.now)
        self.addCleanup(provider.close)
        return provider

    @staticmethod
    def pair(provider):
        by_name = {a['member_name']: a for a in provider.list_assignments()}
        return by_name['Coach Alpha'], by_name['Coach Beta']

    def test_merge_aware_rows_names_and_source_refs_without_default_time(self):
        provider = self.provider(periods={})
        members = provider.inspect_members()
        self.assertEqual({'Coach Alpha', 'Coach Beta', 'Coach Other'}, {m['member_name'] for m in members})
        self.assertTrue(all(m['member_id'].startswith('lab-') for m in members))
        with self.assertRaises(TicketsSourceError):
            provider.list_assignments()
        self.assertTrue(all(call.method == 'GET' for call in self.calls))
        ready = self.provider()
        a, b = self.pair(ready)
        self.assertIn('Lịch lab thử D4', a['course'])
        self.assertIn('Session A', a['course'])
        self.assertEqual('2035-10-10T08:00:00+07:00', a['start'])
        self.assertEqual('2035-10-11T16:00:00+07:00', b['end'])
        self.assertEqual(["'Lịch lab thử'!A1:J1001"], self.calls[0].url.params.get_list('ranges'))

    def test_single_batch_changes_only_two_D_cells_and_ids_survive_swap(self):
        provider = self.provider()
        before = deepcopy(self.payload)
        a, b = self.pair(provider)
        provider.swap(a, b, 'DC-TEST')
        post = [request for request in self.calls if request.method == 'POST']
        self.assertEqual(1, len(post))
        updates = json.loads(post[0].content)['requests']
        self.assertEqual(2, len(updates))
        for operation, row, name in zip(updates, [3, 8], ['Coach Beta', 'Coach Alpha']):
            update = operation['updateCells']
            self.assertEqual({'sheetId': 17, 'startRowIndex': row, 'endRowIndex': row + 1,
                              'startColumnIndex': 3, 'endColumnIndex': 4}, update['range'])
            self.assertEqual('userEnteredValue', update['fields'])
            self.assertEqual(name, update['rows'][0]['values'][0]['userEnteredValue']['stringValue'])
            before['sheets'][0]['data'][0]['rowData'][row]['values'][3] = value(name)
        self.assertEqual(before, self.payload)
        after = {item['id']: item for item in provider.list_assignments()}
        self.assertEqual('Coach Beta', after[a['id']]['member_name'])
        self.assertEqual('Coach Alpha', after[b['id']]['member_name'])
        self.assertEqual(a['id'], after[a['id']]['id'])
        with self.assertRaises(TicketsSourceError):
            provider.swap(a, b, 'DC-TEST')
        self.assertEqual(1, sum(req.method == 'POST' for req in self.calls))

    def test_non_D_edits_invalidate_all_ids_instead_of_guessing_shifted_rows(self):
        for mutation in ('room', 'header', 'insert', 'merge', 'unrelated_note'):
            with self.subTest(mutation=mutation):
                self.payload = fixture()
                provider = self.provider()
                a, b = self.pair(provider)
                if mutation == 'room':
                    set_cell(self.payload, 3, 5, 'R999')
                elif mutation == 'header':
                    set_cell(self.payload, 2, 3, 'Other role')
                elif mutation == 'merge':
                    self.payload['sheets'][0]['merges'].append(dict(startRowIndex=0, endRowIndex=1,
                                                                   startColumnIndex=0, endColumnIndex=2))
                elif mutation == 'unrelated_note':
                    set_cell(self.payload, 0, 8, 'A changed note outside both selected shifts')
                else:
                    self.payload['sheets'][0]['data'][0]['rowData'].insert(0, {'values': []})
                    for merge in self.payload['sheets'][0]['merges']:
                        merge['startRowIndex'] += 1
                        merge['endRowIndex'] += 1
                with self.assertRaises(TicketsSourceError):
                    provider.swap(a, b, 'DC-STRUCTURE')
        self.assertTrue(all(req.method == 'GET' for req in self.calls))

    def test_formula_multi_person_merged_or_noncanonical_D_is_rejected(self):
        for variant in ('formula', 'merged', 'multiname', 'linebreak', 'space', 'unicode'):
            with self.subTest(variant=variant):
                self.payload = fixture()
                if variant == 'formula':
                    self.payload['sheets'][0]['data'][0]['rowData'][3]['values'][3]['userEnteredValue'] = {'formulaValue': '=SECRET()'}
                elif variant == 'merged':
                    self.payload['sheets'][0]['merges'].append(dict(startRowIndex=3, endRowIndex=5,
                                                                   startColumnIndex=3, endColumnIndex=4))
                else:
                    set_cell(self.payload, 3, 3, {'multiname': 'Coach A / Coach B', 'linebreak': 'Coach A\nCoach B',
                                                'space': ' Coach Alpha', 'unicode': 'Nguye\u0302n A'}[variant])
                with self.assertRaises(TicketsSourceError) as cm:
                    self.provider().inspect_members()
                self.assertNotIn('SECRET', str(cm.exception))

    def test_placeholders_are_not_fake_members(self):
        set_cell(self.payload, 4, 3, '-')
        members = self.provider().inspect_members()
        self.assertEqual({'Coach Alpha', 'Coach Beta'}, {m['member_name'] for m in members})

    def test_key_coach_conflicts_block_preview_and_write_without_exposing_E_as_shift(self):
        set_cell(self.payload, 3, 4, 'Coach Beta')
        provider = self.provider()
        a, b = self.pair(provider)
        self.assertEqual(3, len(provider.list_assignments()))
        for call in (lambda: provider.validate_pair(a, b), lambda: provider.swap(a, b, 'DC-KEY')):
            with self.assertRaises(TicketsSourceError) as cm:
                call()
            self.assertIn('Key Coach', str(cm.exception))
        self.assertTrue(all(request.method == 'GET' for request in self.calls))

    def test_explicit_period_hours_override_mapping_without_guessing(self):
        set_cell(self.payload, 8, 6, 'Chiều 15:30–19:30')
        _, b = self.pair(self.provider())
        self.assertEqual('2035-10-11T15:30:00+07:00', b['start'])
        self.assertEqual('2035-10-11T19:30:00+07:00', b['end'])
        set_cell(self.payload, 8, 6, 'Chiều khoảng 15:30–19:30')
        with self.assertRaises(TicketsSourceError):
            self.provider().list_assignments()

    def test_explicit_hours_support_all_members_without_a_default_period_map(self):
        set_cell(self.payload, 3, 6, 'Sáng 08:00–11:00')
        set_cell(self.payload, 8, 6, 'Chiều 13:00–16:00')
        provider = self.provider(periods={})
        by_name = {a['member_name']: a for a in provider.list_assignments()}
        self.assertEqual({'Coach Alpha', 'Coach Beta', 'Coach Other'}, set(by_name))
        # A third member can swap in normal mode; the two-member test list is
        # not used. Real provider requests are captured by MockTransport.
        third, second = by_name['Coach Other'], by_name['Coach Beta']
        provider.validate_pair(third, second)
        provider.swap(third, second, 'DC-ALL-MEMBERS')
        provider.validate_result(third, second)
        posts = [request for request in self.calls if request.method == 'POST']
        self.assertEqual(1, len(posts))
        operations = json.loads(posts[0].content)['requests']
        self.assertEqual([4, 8], [op['updateCells']['range']['startRowIndex'] for op in operations])
        self.assertTrue(all(op['updateCells']['range']['startColumnIndex'] == 3
                            and op['updateCells']['range']['endColumnIndex'] == 4 for op in operations))
        self.assertEqual('Coach Alpha', self.payload['sheets'][0]['data'][0]['rowData'][3]['values'][3]['formattedValue'])

    def test_partial_period_map_requires_hours_for_every_future_assignment(self):
        provider = self.provider(periods={'Sáng': PERIODS['Sáng']})
        with self.assertRaises(TicketsSourceError):
            provider.list_assignments()
        set_cell(self.payload, 8, 6, 'Chiều 13:00–16:00')
        self.assertEqual(3, len(provider.list_assignments()))
        set_cell(self.payload, 3, 6, 'Tối')
        with self.assertRaises(TicketsSourceError):
            provider.list_assignments()
        self.assertTrue(all(request.method == 'GET' for request in self.calls))

    def test_missing_hours_on_key_only_future_assignment_still_block_full_mode(self):
        set_cell(self.payload, 8, 3, '')
        provider = self.provider(periods={'Sáng': PERIODS['Sáng']})
        # The afternoon commitment has only a Key Coach. It must remain part
        # of the conflict roster even though it is not a selectable Lab shift.
        self.assertEqual(2, len(provider.inspect_members()))
        with self.assertRaises(TicketsSourceError) as cm:
            provider.list_assignments()
        self.assertIn('Key Coach', str(cm.exception))
        self.assertTrue(all(request.method == 'GET' for request in self.calls))

    def test_explicit_hours_still_block_third_member_key_coach_conflicts(self):
        set_cell(self.payload, 3, 6, 'Sáng 08:00–11:00')
        set_cell(self.payload, 8, 6, 'Chiều 13:00–16:00')
        set_cell(self.payload, 3, 4, 'Coach Beta')
        provider = self.provider(periods={})
        by_name = {a['member_name']: a for a in provider.list_assignments()}
        third, second = by_name['Coach Other'], by_name['Coach Beta']
        for operation in (lambda: provider.validate_pair(third, second),
                          lambda: provider.swap(third, second, 'DC-THIRD-KEY')):
            with self.assertRaises(TicketsSourceError) as cm:
                operation()
            self.assertIn('Key Coach', str(cm.exception))
        self.assertTrue(all(request.method == 'GET' for request in self.calls))

    def test_valid_past_rows_and_ids_remain_for_midnight_reconciliation(self):
        provider = self.provider()
        a, b = self.pair(provider)
        self.post_behavior = 'after_write_timeout'
        with self.assertRaises(TicketsWriteUncertain):
            provider.swap(a, b, 'DC-MIDNIGHT')
        self.now += timedelta(days=4)
        read_back = {item['id']: item for item in provider.list_assignments()}
        self.assertEqual(b['member_id'], read_back[a['id']]['member_id'])
        self.assertEqual(a['member_id'], read_back[b['id']]['member_id'])
        provider.validate_result(a, b)
        self.assertEqual(1, sum(call.method == 'POST' for call in self.calls))

    def test_unmapped_name_row_is_not_silently_lost_from_conflict_checks(self):
        set_cell(self.payload, 8, 0, '')
        with self.assertRaises(TicketsSourceError):
            self.provider().inspect_members()

    def test_unmapped_key_coach_row_cannot_hide_a_conflicting_commitment(self):
        provider = self.provider()
        a, b = self.pair(provider)
        hidden = ['', 'Track C', 'Session C', '', 'Coach Alpha', 'R103', 'Chiều', '20']
        self.payload['sheets'][0]['data'][0]['rowData'].append({'values': [value(cell) for cell in hidden]})
        with self.assertRaises(TicketsSourceError):
            provider.validate_pair(a, b)
        with self.assertRaises(TicketsSourceError):
            provider.swap(a, b, 'DC-UNMAPPED-KEY')
        self.assertTrue(all(call.method == 'GET' for call in self.calls))

    def test_historical_ambiguous_key_name_does_not_block_D_onboarding(self):
        set_cell(self.payload, 3, 4, 'Key A\nKey B')
        provider = self.provider()
        self.assertEqual(3, len(provider.inspect_members()))
        with self.assertRaises(TicketsSourceError):
            provider.list_assignments()
        set_cell(self.payload, 1, 0, 'Thứ 4 - 01/10/2035')
        self.assertEqual(3, len(provider.list_assignments()))

    def test_incomplete_historical_rows_do_not_invent_hours_or_block_future_rows(self):
        set_cell(self.payload, 1, 0, 'Thứ 4 - 01/10/2035')
        set_cell(self.payload, 3, 6, '')
        provider = self.provider()
        self.assertEqual(3, len(provider.inspect_members()))
        self.assertEqual(['Coach Beta'], [a['member_name'] for a in provider.list_assignments()])
        set_cell(self.payload, 6, 0, 'Thứ 5 - 11/10/2035')
        set_cell(self.payload, 8, 6, '')
        with self.assertRaises(TicketsSourceError):
            provider.list_assignments()

    def test_write_guards_and_uncertain_outcomes_never_retry(self):
        blocked = self.provider(write_enabled=False)
        with self.assertRaises(TicketsSourceError):
            blocked.swap({}, {}, 'DC-DISABLED')
        self.assertEqual([], self.calls)
        for behavior in ('timeout', 'bad200', '500'):
            self.calls.clear()
            self.post_behavior = behavior
            provider = self.provider()
            a, b = self.pair(provider)
            with self.assertRaises(TicketsWriteUncertain) as cm:
                provider.swap(a, b, 'DC-UNCERTAIN')
            self.assertNotIn('private', str(cm.exception))
            self.assertEqual(1, sum(call.method == 'POST' for call in self.calls))

    def test_source_key_changes_with_time_rules_and_other_tabs_are_not_discovered(self):
        original = self.provider()
        changed = self.provider(periods=PERIODS | {'Sáng': {'start': '09:00', 'end': '11:00'}})
        self.assertNotEqual(original.source_key, changed.source_key)
        original.inspect_members()
        self.assertEqual(["'Lịch lab thử'!A1:J1001"], self.calls[-1].url.params.get_list('ranges'))

    def test_explicit_test_mode_uses_separate_guards_and_exact_two_names(self):
        test = LabSheetsSettings.from_mapping(test_values(), test_mode=True)
        self.assertTrue(test.test_mode)
        self.assertEqual(('Coach Alpha', 'Coach Beta'), test.test_members)
        self.assertEqual({}, test.periods)
        self.assertTrue(test.write_enabled)
        normal = LabSheetsSettings.from_mapping(test_values(TICKETS_CHAT_MODE='lab_live_test'))
        self.assertFalse(normal.test_mode)
        self.assertFalse(normal.write_enabled)
        self.assertEqual((), normal.test_members)
        flags = test_values(TICKETS_LAB_WRITE_ENABLED='true', TICKETS_LAB_EXCLUSIVE_WRITER='true',
                            TICKETS_LAB_TEST_WRITE_ENABLED='false', TICKETS_LAB_TEST_EXCLUSIVE_WRITER='false')
        self.assertFalse(LabSheetsSettings.from_mapping(flags, test_mode=True).write_enabled)
        for names in ([], ['Coach Alpha'], ['Coach Alpha', 'Coach Alpha'],
                      ['Coach Alpha', 'Coach Beta', 'Coach Other'], [' Coach Alpha', 'Coach Beta'],
                      ['Nguye\u0302n A', 'Coach Beta']):
            with self.subTest(names=names), self.assertRaises(TicketsSourceError):
                LabSheetsSettings.from_mapping(test_values(TICKETS_LAB_TEST_MEMBERS_JSON=json.dumps(names)), test_mode=True)
        with self.assertRaises(TicketsSourceError):
            LabSheetsSettings.from_mapping(test_values(TICKETS_LAB_TEST_EXCLUSIVE_WRITER='false'), test_mode=True)

    def test_future_real_write_test_only_changes_whitelisted_two_D_cells(self):
        # This would conflict with the destination shift in normal mode.
        set_cell(self.payload, 3, 4, 'Coach Beta')
        settings = LabSheetsSettings.from_mapping(test_values(), test_mode=True)
        provider = self.provider(settings=settings)
        self.assertEqual({'Coach Alpha', 'Coach Beta'}, {m['member_name'] for m in provider.inspect_members()})
        self.assertEqual(2, len(provider.list_assignments()))
        a, b = self.pair(provider)
        self.assertTrue(a['start'].endswith('T00:00:00+07:00'))
        self.assertEqual(timedelta(days=1), datetime.fromisoformat(a['end']) - datetime.fromisoformat(a['start']))
        provider.validate_pair(a, b)
        provider.swap(a, b, 'DC-FUTURE-TEST')
        # Read-back stays possible when the selected date is now historical.
        self.now = NOW + timedelta(days=100)
        provider.validate_result(a, b)
        posts = [req for req in self.calls if req.method == 'POST']
        self.assertEqual(1, len(posts))
        requests = json.loads(posts[0].content)['requests']
        self.assertEqual(2, len(requests))
        self.assertEqual([3, 8], [r['updateCells']['range']['startRowIndex'] for r in requests])
        self.assertTrue(all(r['updateCells']['range']['startColumnIndex'] == 3
                            and r['updateCells']['range']['endColumnIndex'] == 4 for r in requests))
        self.assertEqual('Coach Other', self.payload['sheets'][0]['data'][0]['rowData'][4]['values'][3]['formattedValue'])

    def test_test_mode_past_and_same_day_unknown_hours_cannot_validate_or_write(self):
        provider = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        a, b = self.pair(provider)
        for current in ('2035-10-10T00:00:00+07:00', '2035-10-10T12:00:00+07:00', '2036-01-01T00:00:00+07:00'):
            self.now = datetime.fromisoformat(current)
            with self.subTest(current=current):
                # Keep the complete source for audit/read-back, but forbid swaps.
                self.assertEqual((a, b), self.pair(provider))
                with self.assertRaises(TicketsSourceError):
                    provider.validate_pair(a, b)
                with self.assertRaises(TicketsSourceError):
                    provider.swap(a, b, 'DC-PAST-TEST')
        self.assertFalse(any(call.method == 'POST' for call in self.calls))

    def test_test_mode_honors_explicit_shift_hours_and_exact_start_boundary(self):
        set_cell(self.payload, 3, 6, 'Sáng 08:00–11:00')
        set_cell(self.payload, 8, 6, 'Chiều 13:30-16:30')
        provider = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        a, b = self.pair(provider)
        self.assertEqual('2035-10-10T08:00:00+07:00', a['start'])
        self.assertEqual('2035-10-11T16:30:00+07:00', b['end'])
        self.now = datetime.fromisoformat('2035-10-10T07:59:59+07:00')
        provider.validate_pair(a, b)
        self.now += timedelta(seconds=1)
        with self.assertRaises(TicketsSourceError):
            provider.validate_pair(a, b)
        with self.assertRaises(TicketsSourceError):
            provider.swap(a, b, 'DC-STARTED')
        self.assertFalse(any(call.method == 'POST' for call in self.calls))

    @staticmethod
    def legacy_placeholder(item):
        midnight = datetime.fromisoformat(item['start']).replace(hour=0, minute=0, second=0, microsecond=0)
        return dict(item, start=midnight.isoformat(), end=(midnight + timedelta(days=1)).isoformat())

    def test_legacy_placeholder_readback_normalizes_only_test_mode_not_write_validation(self):
        set_cell(self.payload, 3, 6, 'Sáng 08:00–11:00')
        set_cell(self.payload, 8, 6, 'Chiều 13:30–16:30')
        provider = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        a, b = self.pair(provider)
        old_a, old_b = self.legacy_placeholder(a), self.legacy_placeholder(b)
        self.assertEqual(a, provider.reconciliation_snapshot(old_a))
        self.assertEqual(b, provider.reconciliation_snapshot(old_b))
        self.assertEqual(old_a, self.provider().reconciliation_snapshot(old_a))
        # A compatibility conversion must never make an old proposal writable.
        with self.assertRaises(TicketsSourceError):
            provider.validate_pair(old_a, old_b)
        with self.assertRaises(TicketsSourceError):
            provider.swap(old_a, old_b, 'DC-OLD-SNAPSHOT')
        set_cell(self.payload, 3, 3, b['member_name'])
        set_cell(self.payload, 8, 3, a['member_name'])
        self.now = NOW + timedelta(days=100)
        provider.validate_result(old_a, old_b)
        self.assertFalse(any(call.method == 'POST' for call in self.calls))

    def legacy_uncertain_result(self, change=None):
        self.payload, self.calls, self.now = fixture(), [], NOW
        set_cell(self.payload, 3, 6, 'Sáng 08:00–11:00')
        set_cell(self.payload, 8, 6, 'Chiều 13:30–16:30')
        provider = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        a, b = self.pair(provider)
        with TemporaryDirectory() as directory:
            store = TicketStore(Path(directory) / 'legacy-reconcile.sqlite3')
            try:
                with store.transaction() as db:
                    for account, item in (('a', a), ('b', b)):
                        db.execute('INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                                   (account, item['member_name'], 'member', item['member_id'], '!synthetic'))
                service = TicketService(store, provider, mode='lab_live_test', require_manager=False,
                                        clock=lambda: self.now.timestamp())
                user_a, user_b = store.account('a'), store.account('b')
                preview = service.preview(user_a, a['id'], b['id'])
                ticket = service.create(user_a, preview['preview_token'], '', 'legacy-reconcile-001')
                body = json.loads(store.db.execute('SELECT body FROM exchange_tickets WHERE id=?', (ticket['id'],)).fetchone()[0])
                body.update(a=self.legacy_placeholder(a), b=self.legacy_placeholder(b))
                set_cell(self.payload, 3, 3, b['member_name'])
                set_cell(self.payload, 8, 3, a['member_name'])
                if change == 'course':
                    set_cell(self.payload, 3, 1, 'Changed class')
                elif change == 'period':
                    set_cell(self.payload, 3, 6, 'Sáng 09:00–12:00')
                elif change == 'identity':
                    body['a']['id'] += '-different-source'
                elif change == 'day':
                    for key in ('start', 'end'):
                        body['a'][key] = (datetime.fromisoformat(body['a'][key]) + timedelta(days=1)).isoformat()
                elif change == 'not-placeholder':
                    body['a']['start'] = body['a']['start'].replace('T00:00:00', 'T00:00:01')
                with store.transaction() as db:
                    db.execute("UPDATE exchange_tickets SET body=?,status='uncertain',version=version+1 WHERE id=?",
                               (packed(body), ticket['id']))
                self.now = NOW + timedelta(days=100)
                detail = service.detail(user_b, ticket['id'])
                self.assertIn('reconcile', detail['actions'])
                result = service.action(user_b, ticket['id'], 'reconcile', detail['version'])
                stored = json.loads(store.db.execute('SELECT body FROM exchange_tickets WHERE id=?', (ticket['id'],)).fetchone()[0])
                self.assertEqual(body, stored)  # Never rewrite the approved proposal.
                self.assertFalse(any(call.method == 'POST' for call in self.calls))
                return result['status']
            finally:
                store.close()

    def test_legacy_uncertain_placeholder_request_can_finish_readback_after_start_without_write(self):
        self.assertEqual('completed', self.legacy_uncertain_result())

    def test_legacy_reconciliation_keeps_other_snapshot_changes_uncertain(self):
        for change in ('course', 'period', 'identity', 'day', 'not-placeholder'):
            with self.subTest(change=change):
                self.assertEqual('uncertain', self.legacy_uncertain_result(change))

    def test_normal_mode_still_requires_hours_rejects_past_and_has_different_target(self):
        self.now = NOW + timedelta(days=100)
        normal = self.provider()
        a, b = self.pair(normal)
        with self.assertRaises(TicketsSourceError):
            normal.validate_pair(a, b)
        test = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        self.assertNotEqual(normal.source_key, test.source_key)
        # Historical rows with unknown hours are not invented as assignments.
        self.assertEqual([], self.provider(periods={}).list_assignments())
        self.now = NOW
        with self.assertRaises(TicketsSourceError):
            self.provider(periods={}).list_assignments()
        self.assertEqual([], [r for r in self.calls if r.method == 'POST'])

    def test_test_mode_retains_fresh_snapshot_and_structure_guards(self):
        provider = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        a, b = self.pair(provider)
        set_cell(self.payload, 3, 3, 'Coach Other')
        with self.assertRaises(TicketsSourceError):
            provider.swap(a, b, 'DC-NAME-CHANGED')
        self.payload = fixture()
        set_cell(self.payload, 0, 8, 'Changed source structure note')
        with self.assertRaises(TicketsSourceError):
            provider.swap(a, b, 'DC-STRUCTURE-CHANGED')
        self.assertTrue(all(r.method == 'GET' for r in self.calls))

    def test_test_mode_does_not_invent_missing_class_details(self):
        set_cell(self.payload, 8, 5, '')
        provider = self.provider(settings=LabSheetsSettings.from_mapping(test_values(), test_mode=True))
        self.assertEqual(['Coach Alpha'], [a['member_name'] for a in provider.list_assignments()])


if __name__ == '__main__':
    unittest.main()
