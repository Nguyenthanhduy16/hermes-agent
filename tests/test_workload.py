"""Synthetic Workload identity reads; no real directory or credentials."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

import httpx

from hermes_zalo.sheets import SheetsClient, SheetsSettings
from hermes_zalo.workload import (DUPLICATE_PHONE_ERROR, LOOKUP_ERROR, NOT_FOUND_ERROR,
                                 WorkloadDirectory, WorkloadError, WorkloadSettings, normalize_phone)


def settings(**changes):
    values = dict(spreadsheet_id='synthetic_directory_123', tab='Workload - Lab coach (Tháng 10)',
                  name_column='B', phone_column='C', header_row=5,
                  name_header='Họ tên', phone_header='Số điện thoại')
    values.update(changes)
    return WorkloadSettings(**values)


def mapping():
    return dict(REGISTRATION_ENABLED='true', REGISTRATION_WORKLOAD_TAB='Workload - Lab coach (Tháng 10)',
                REGISTRATION_WORKLOAD_NAME_COLUMN='B', REGISTRATION_WORKLOAD_PHONE_COLUMN='C',
                REGISTRATION_WORKLOAD_HEADER_ROW='5', REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên',
                REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')


def cell(value):
    return {'values': [{'formattedValue': value, 'userEnteredValue': {'stringValue': value},
                        'effectiveValue': {'stringValue': value}}]}


def payload(rows=None, cfg=None, row_count=270):
    cfg = cfg or settings()
    rows = rows if rows is not None else [('Nguyễn Văn An', '0912345678'), ('Trần Văn Bình', '0387654321')]
    columns = []
    for index, column, header in ((0, cfg.name_column, cfg.name_header), (1, cfg.phone_column, cfg.phone_header)):
        number = 0
        for character in column:
            number = number * 26 + ord(character) - ord('A') + 1
        columns.append(dict(startRow=cfg.header_row-1, startColumn=number-1,
                            rowData=[cell(header)] + [cell(row[index]) for row in rows]))
    return dict(spreadsheetId=cfg.spreadsheet_id, sheets=[dict(
        properties=dict(sheetId=7, title=cfg.tab, gridProperties=dict(rowCount=row_count, columnCount=107)),
        data=columns)])


class PhoneAndSettingsTests(unittest.TestCase):
    def test_complete_vn_numbers_normalize_without_guessing(self):
        for prefix in ('03', '05', '07', '08', '09'):
            local = prefix + '12345678'
            for formatted in (local, '+84' + local[1:], ' ' + local[:4] + '.' + local[4:7] + '-' + local[7:] + ' ',
                              '+84 ' + local[1:4] + ' ' + local[4:]):
                with self.subTest(value=formatted):
                    self.assertEqual(local, normalize_phone(formatted))

    def test_invalid_or_incomplete_numbers_never_acquire_a_guessed_zero(self):
        for value in ('912345678', '9.12345678E+08', '091234567', '09123456789', '+840912345678',
                      '84912345678', '0212345678', '0612345678', '(091)2345678', '0912/345678',
                      '0912345678 ext1', '٠٩١٢٣٤٥٦٧٨', '０９１２３４５６７８', '0912\n345678',
                      '0912\t345678', '0912\u200b345678', '', None, 912345678, True):
            with self.subTest(value=value), self.assertRaises(WorkloadError):
                normalize_phone(value)

    def test_disabled_is_noop_and_enabled_requires_every_explicit_schema_field(self):
        base = SimpleNamespace(spreadsheet_id='synthetic_directory_123')
        self.assertIsNone(WorkloadSettings.from_mapping({}, None))
        self.assertIsNone(WorkloadSettings.from_mapping({'REGISTRATION_ENABLED': 'false'}, None))
        self.assertEqual(settings(), WorkloadSettings.from_mapping(mapping(), base))
        for key in mapping():
            if key == 'REGISTRATION_ENABLED':
                continue
            values = mapping()
            del values[key]
            with self.subTest(key=key), self.assertRaises(WorkloadError):
                WorkloadSettings.from_mapping(values, base)
        for values, source in ((mapping(), None), (mapping() | {'REGISTRATION_ENABLED': 'yes'}, base),
                               (mapping() | {'REGISTRATION_WORKLOAD_HEADER_ROW': '5.0'}, base)):
            with self.assertRaises(WorkloadError):
                WorkloadSettings.from_mapping(values, source)

    def test_unsafe_columns_headers_and_sources_rejected_before_http(self):
        for change in (dict(name_column='B1'), dict(phone_column='B'), dict(name_column='AAA'),
                       dict(header_row=True), dict(header_row=0), dict(header_row=1001),
                       dict(tab='Workload!A1:C100'), dict(tab='Workload\nPrivate'),
                       dict(name_header=''), dict(phone_header='Họ tên'), dict(spreadsheet_id='https://private.invalid')):
            with self.subTest(change=change), self.assertRaises(WorkloadError):
                settings(**change)
        with self.assertRaises(WorkloadError):
            WorkloadDirectory(settings(), SimpleNamespace(settings=SimpleNamespace(spreadsheet_id='wrong-source')))


class WorkloadDirectoryTests(unittest.IsolatedAsyncioTestCase):
    def directory(self, data=None, cfg=None, handler=None):
        cfg = cfg or settings()
        data = payload(cfg=cfg) if data is None else data
        calls = []
        def transport(request):
            calls.append(request)
            return handler(request) if handler else httpx.Response(200, json=data)
        async def token():
            return 'synthetic-token'
        source = SheetsSettings(cfg.spreadsheet_id, Path('not-a-real-key.json'), issues_enabled=False)
        client = SheetsClient(source, client=httpx.AsyncClient(transport=httpx.MockTransport(transport)), token_provider=token)
        self.addAsyncCleanup(client.close)
        return WorkloadDirectory(cfg, client), calls

    async def test_exact_source_headers_and_only_two_bounded_get_ranges(self):
        directory, calls = self.directory()
        person = await directory.lookup('+84 912 345 678')
        self.assertEqual('Nguyễn Văn An', person.name)
        self.assertEqual('0912345678', person.phone)
        self.assertTrue(person.name_unique)
        self.assertNotIn(person.phone, repr(person))
        self.assertEqual(directory.source_key, person.source_key)
        self.assertRegex(person.fingerprint, r'^[a-f0-9]{64}$')
        self.assertEqual(1, len(calls))
        request = calls[0]
        self.assertEqual('GET', request.method)
        self.assertEqual(["'Workload - Lab coach (Tháng 10)'!B5:B1005", "'Workload - Lab coach (Tháng 10)'!C5:C1005"],
                         request.url.params.get_list('ranges'))
        self.assertIn('formattedValue', request.url.params['fields'])
        self.assertEqual('Bearer synthetic-token', request.headers['Authorization'])
        self.assertEqual(b'', request.content)

    async def test_row_reorder_blank_insert_and_phone_punctuation_keep_identity(self):
        directory, _ = self.directory()
        first = await directory.lookup('0912345678')
        changed, _ = self.directory(payload([('', ''), ('Trần Văn Bình', '0387654321'), ('Nguyễn Văn An', '+84 912-345.678')]))
        second = await changed.lookup('0912345678')
        self.assertEqual(first, second)
        cfg = settings(header_row=6)
        moved, _ = self.directory(payload(cfg=cfg), cfg=cfg)
        self.assertEqual(first, await moved.lookup('0912345678'))

    async def test_name_phone_or_source_change_invalidates_fingerprint(self):
        directory, _ = self.directory()
        original = await directory.lookup('0912345678')
        for cfg, name, phone in ((settings(), 'Nguyễn Văn An Khác', original.phone),
                                 (settings(), original.name, '0912345679'),
                                 (settings(tab='Workload khác'), original.name, original.phone),
                                 (settings(spreadsheet_id='synthetic_other_123'), original.name, original.phone)):
            with self.subTest(kind=(cfg.tab, name)):
                other, _ = self.directory(payload([(name, phone)], cfg), cfg=cfg)
                self.assertNotEqual(original.fingerprint, (await other.lookup(phone)).fingerprint)

    async def test_same_reader_fetches_fresh_roster_for_approval_recheck(self):
        state = [payload()]
        directory, calls = self.directory(handler=lambda request: httpx.Response(200, json=state[0]))
        first = await directory.lookup('0912345678')
        state[0] = payload([('Tên đã thay đổi', '0912345678')])
        second = await directory.lookup('0912345678')
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(2, len(calls))
        state[0] = payload([('Tên đã thay đổi', '0387654321')])
        with self.assertRaises(WorkloadError):
            await directory.lookup('0912345678')
        self.assertEqual(3, len(calls))

    async def test_duplicate_normalized_phone_is_rejected_even_when_names_match(self):
        for name in ('Nguyễn Văn An', 'Trần Văn Bình', ''):
            directory, _ = self.directory(payload([('Nguyễn Văn An', '0912345678'), (name, '+84 912 345 678')]))
            with self.subTest(name=name), self.assertRaises(WorkloadError) as caught:
                await directory.lookup('0912345678')
            self.assertEqual(DUPLICATE_PHONE_ERROR, str(caught.exception))
            self.assertNotIn('0912345678', str(caught.exception))
            self.assertNotIn('Nguyễn Văn An', str(caught.exception))

    async def test_not_found_and_invalid_person_are_distinct_without_revealing_roster_data(self):
        for rows, expected in (([('Nguyễn Văn An', '0387654321')], NOT_FOUND_ERROR),
                               ([('', '0912345678')], LOOKUP_ERROR)):
            directory, _ = self.directory(payload(rows))
            with self.subTest(expected=expected), self.assertRaises(WorkloadError) as caught:
                await directory.lookup('0912345678')
            self.assertEqual(expected, str(caught.exception))
            for private in ('Nguyễn Văn An', '0912345678', '0387654321'):
                self.assertNotIn(private, str(caught.exception))
        self.assertNotEqual(NOT_FOUND_ERROR, DUPLICATE_PHONE_ERROR)

    async def test_name_uniqueness_counts_missing_invalid_and_accent_folded_phone_rows(self):
        for phone in ('', '912345678', '9.123E+08', '0387654321'):
            for name in ('Nguyễn Văn An', 'NGUYEN VAN AN'):
                directory, _ = self.directory(payload([('Nguyễn Văn An', '0912345678'), (name, phone)]))
                with self.subTest(phone=phone, name=name):
                    self.assertFalse((await directory.lookup('0912345678')).name_unique)
                    with self.assertRaises(WorkloadError):
                        await directory.resolve_name('Nguyễn Văn An')

    async def test_unique_name_requires_exact_spelling_and_unique_valid_phone(self):
        directory, _ = self.directory()
        self.assertEqual(await directory.lookup('0912345678'), await directory.resolve_name('Nguyễn Văn An'))
        for name in ('nguyễn văn an', 'Nguyen Van An', 'Không có trong bảng'):
            with self.assertRaises(WorkloadError):
                await directory.resolve_name(name)
        for rows in ([('Nguyễn Văn An', '')], [('Nguyễn Văn An', '912345678')],
                     [('Nguyễn Văn An', '0912345678'), ('Tên khác', '0912345678')]):
            other, _ = self.directory(payload(rows))
            with self.assertRaises(WorkloadError):
                await other.resolve_name('Nguyễn Văn An')

    async def test_formatted_leading_zero_is_preserved_but_missing_zero_is_not_repaired(self):
        data = payload()
        phone_cell = data['sheets'][0]['data'][1]['rowData'][1]['values'][0]
        phone_cell.update(userEnteredValue={'numberValue': 912345678}, effectiveValue={'numberValue': 912345678})
        directory, _ = self.directory(data)
        self.assertEqual('0912345678', (await directory.lookup('0912345678')).phone)
        for displayed in ('912345678', '9.12345678E+08', '9.12346E+08'):
            changed = deepcopy(data)
            changed['sheets'][0]['data'][1]['rowData'][1]['values'][0]['formattedValue'] = displayed
            other, _ = self.directory(changed)
            with self.assertRaises(WorkloadError):
                await other.lookup('0912345678')

    async def test_maximum_records_supported_without_reading_beyond_bound(self):
        rows = [(f'Thành viên mẫu {i}', f'09{i:08d}') for i in range(1000)]
        directory, calls = self.directory(payload(rows, row_count=1005))
        self.assertEqual('Thành viên mẫu 999', (await directory.lookup('0900000999')).name)
        self.assertEqual(1, len(calls))
        oversized, _ = self.directory(payload(row_count=1006))
        with self.assertRaises(WorkloadError):
            await oversized.lookup('0912345678')

    async def test_wrong_header_source_offsets_shape_and_merges_fail_closed(self):
        def mutate(which):
            data = payload()
            sheet = data['sheets'][0]
            if which == 'source': data['spreadsheetId'] = 'another_source'
            elif which == 'title': sheet['properties']['title'] = 'Different tab'
            elif which == 'header': sheet['data'][0]['rowData'][0] = cell('Unexpected header')
            elif which == 'row-offset': sheet['data'][0]['startRow'] = 3
            elif which == 'column-offset': sheet['data'][0]['startColumn'] = 0
            elif which == 'duplicate-column': sheet['data'][1]['startColumn'] = 1
            elif which == 'merged': sheet['merges'] = [dict(startRowIndex=4, endRowIndex=6, startColumnIndex=1, endColumnIndex=2)]
            elif which == 'rows': sheet['data'][0]['rowData'] = 'invalid'
            elif which == 'extra-column': sheet['data'][0]['rowData'][1]['values'].append({'formattedValue': 'Unexpected'})
            elif which == 'phone-formula': sheet['data'][1]['rowData'][1]['values'][0]['userEnteredValue'] = {'formulaValue': '=PRIVATE()'}
            elif which == 'name-formula': sheet['data'][0]['rowData'][1]['values'][0]['userEnteredValue'] = {'formulaValue': '=PRIVATE()'}
            elif which == 'error': sheet['data'][1]['rowData'][1]['values'][0]['effectiveValue'] = {'errorValue': {'message': 'PRIVATE'}}
            return data
        for kind in ('source', 'title', 'header', 'row-offset', 'column-offset', 'duplicate-column', 'merged',
                     'rows', 'extra-column', 'phone-formula', 'name-formula', 'error'):
            directory, _ = self.directory(mutate(kind))
            with self.subTest(kind=kind), self.assertRaises(WorkloadError) as caught:
                await directory.lookup('0912345678')
            self.assertNotIn('PRIVATE', str(caught.exception))

    async def test_unrelated_merges_and_missing_trailing_phone_do_not_hide_name_collision(self):
        data = payload([('Nguyễn Văn An', '0912345678'), ('Nguyễn Văn An', '')])
        sheet = data['sheets'][0]
        sheet['merges'] = [dict(startRowIndex=0, endRowIndex=4, startColumnIndex=1, endColumnIndex=3),
                           dict(startRowIndex=4, endRowIndex=7, startColumnIndex=5, endColumnIndex=6)]
        sheet['data'][1]['rowData'].pop()
        directory, _ = self.directory(data)
        self.assertFalse((await directory.lookup('0912345678')).name_unique)

    async def test_observed_header_and_blank_footer_shapes_preserve_exact_row_pairs(self):
        cfg = settings(tab='Workload - Lab coach', name_column='D', phone_column='C', name_header='Thành viên')
        rows = [('', ''), ('Nguyễn Văn An', '0912345678'), ('Trần Văn Bình', '0387654321')] + [('', '')] * 61
        data = payload(rows, cfg, row_count=1000)
        sheet = data['sheets'][0]
        sheet['merges'] = [dict(startRowIndex=4, endRowIndex=6, startColumnIndex=column, endColumnIndex=column+1)
                           for column in (2, 3)]
        sheet['merges'] += [dict(startRowIndex=69, endRowIndex=70, startColumnIndex=0, endColumnIndex=4)]
        sheet['merges'] += [dict(startRowIndex=row, endRowIndex=row+1, startColumnIndex=0, endColumnIndex=41)
                            for row in (71, 72, 73, 75, 76, 77, 78)]
        self.assertEqual([65, 65], [len(block['rowData']) for block in sheet['data']])
        # Google need not return grid blocks in the requested range order.
        sheet['data'].reverse()
        directory, calls = self.directory(data, cfg)
        person = await directory.lookup('0912345678')
        self.assertEqual('Nguyễn Văn An', person.name)
        self.assertTrue(person.name_unique)
        self.assertEqual('Trần Văn Bình', (await directory.lookup('0387654321')).name)
        self.assertTrue(all(request.method == 'GET' for request in calls))
        self.assertEqual(["'Workload - Lab coach'!D5:D1005", "'Workload - Lab coach'!C5:C1005"],
                         calls[0].url.params.get_list('ranges'))

    async def test_arbitrary_and_unequal_header_heights_skip_only_verified_blank_continuations(self):
        for ends in ((8, 8), (6, 8), (8, 5)):
            data = payload([('', '')] * 3 + [('Nguyễn Văn An', '0912345678')])
            data['sheets'][0]['merges'] = [dict(startRowIndex=4, endRowIndex=end, startColumnIndex=column,
                                                    endColumnIndex=column+1)
                                           for column, end in zip((1, 2), ends) if end > 5]
            directory, _ = self.directory(data)
            with self.subTest(ends=ends):
                self.assertEqual('Nguyễn Văn An', (await directory.lookup('0912345678')).name)
        # A longer name header must not hide a phone-only or name-only row.
        for column, value in ((0, 'Tên bị che'), (1, '0387654321')):
            hidden = deepcopy(data)
            hidden['sheets'][0]['data'][column]['rowData'][2] = cell(value)
            directory, _ = self.directory(hidden)
            with self.subTest(column=column), self.assertRaises(WorkloadError):
                await directory.lookup('0912345678')

    async def test_header_merge_must_anchor_exact_header_and_single_selected_column(self):
        for merge in (
                dict(startRowIndex=3, endRowIndex=6, startColumnIndex=1, endColumnIndex=2),
                dict(startRowIndex=4, endRowIndex=6, startColumnIndex=1, endColumnIndex=3),
                dict(startRowIndex=4, endRowIndex=6, startColumnIndex=0, endColumnIndex=3)):
            data = payload([('', ''), ('Nguyễn Văn An', '0912345678')])
            data['sheets'][0]['merges'] = [merge]
            directory, _ = self.directory(data)
            with self.subTest(merge=merge), self.assertRaises(WorkloadError):
                await directory.lookup('0912345678')

    async def test_overlapping_or_out_of_bounds_header_merges_rejected(self):
        header = dict(startRowIndex=4, endRowIndex=7, startColumnIndex=1, endColumnIndex=2)
        for other in (header, dict(startRowIndex=6, endRowIndex=8, startColumnIndex=1, endColumnIndex=2),
                      dict(startRowIndex=4, endRowIndex=271, startColumnIndex=2, endColumnIndex=3)):
            data = payload([('', '')] * 3 + [('Nguyễn Văn An', '0912345678')])
            data['sheets'][0]['merges'] = [header, other]
            directory, _ = self.directory(data)
            with self.subTest(other=other), self.assertRaises(WorkloadError):
                await directory.lookup('0912345678')

    async def test_header_can_end_at_grid_boundary_without_inventing_records(self):
        data = payload([('', '')] * 3, row_count=8)
        data['sheets'][0]['merges'] = [dict(startRowIndex=4, endRowIndex=8, startColumnIndex=column,
                                          endColumnIndex=column+1) for column in (1, 2)]
        directory, _ = self.directory(data)
        self.assertEqual([], await directory._read())
        with self.assertRaises(WorkloadError):
            await directory.lookup('0912345678')

    async def test_data_merges_are_not_interpreted_as_people_even_when_blank(self):
        for merge in (
                dict(startRowIndex=5, endRowIndex=7, startColumnIndex=1, endColumnIndex=2),
                dict(startRowIndex=7, endRowIndex=9, startColumnIndex=2, endColumnIndex=3),
                dict(startRowIndex=7, endRowIndex=8, startColumnIndex=1, endColumnIndex=3),
                dict(startRowIndex=7, endRowIndex=8, startColumnIndex=0, endColumnIndex=2)):
            data = payload()
            data['sheets'][0]['merges'] = [merge]
            directory, _ = self.directory(data)
            with self.subTest(merge=merge), self.assertRaises(WorkloadError):
                await directory.lookup('0912345678')

    async def test_wide_footer_requires_both_selected_columns_blank_through_entire_height(self):
        for column in (0, 1):
            for offset in (3, 4):
                data = payload([('Nguyễn Văn An', '0912345678'), ('', ''), ('', ''), ('', '')])
                data['sheets'][0]['merges'] = [dict(startRowIndex=7, endRowIndex=9, startColumnIndex=0, endColumnIndex=4)]
                data['sheets'][0]['data'][column]['rowData'][offset] = cell('Nội dung riêng')
                directory, _ = self.directory(data)
                with self.subTest(column=column, offset=offset), self.assertRaises(WorkloadError) as caught:
                    await directory.lookup('0912345678')
                self.assertNotIn('Nội dung riêng', str(caught.exception))

    async def test_formulas_errors_and_hidden_values_cannot_masquerade_as_blank_merged_cells(self):
        for location in ('header', 'footer'):
            for kind in ('formula', 'error', 'hidden-string', 'hidden-number', 'hidden-bool'):
                data = payload([('', ''), ('Nguyễn Văn An', '0912345678'), ('', '')])
                data['sheets'][0]['merges'] = [
                    dict(startRowIndex=4, endRowIndex=6, startColumnIndex=1, endColumnIndex=2),
                    dict(startRowIndex=7, endRowIndex=8, startColumnIndex=0, endColumnIndex=4)]
                selected = data['sheets'][0]['data'][0]['rowData'][1 if location == 'header' else 3]['values'][0]
                if kind == 'formula': selected['userEnteredValue'] = {'formulaValue': '=PRIVATE()'}
                elif kind == 'error': selected['effectiveValue'] = {'errorValue': {'message': 'PRIVATE'}}
                elif kind == 'hidden-string': selected['userEnteredValue'] = {'stringValue': 'PRIVATE'}
                elif kind == 'hidden-number': selected['effectiveValue'] = {'numberValue': 0}
                else: selected['userEnteredValue'] = {'boolValue': False}
                directory, _ = self.directory(data)
                with self.subTest(location=location, kind=kind), self.assertRaises(WorkloadError) as caught:
                    await directory.lookup('0912345678')
                self.assertNotIn('PRIVATE', str(caught.exception))

    async def test_absent_blank_name_and_invalid_input_do_not_leak_or_call_with_user_range(self):
        directory, calls = self.directory(payload([('', '0912345678')]))
        with self.assertRaises(WorkloadError):
            await directory.lookup('0912345678')
        with self.assertRaises(WorkloadError):
            await directory.lookup('0387654321')
        count = len(calls)
        for value in ('B5:C9999', '0912345678&range=PRIVATE', '123'):
            with self.assertRaises(WorkloadError):
                await directory.lookup(value)
        self.assertEqual(count, len(calls))

    async def test_http_errors_have_fixed_message_and_no_retry(self):
        def failure(request):
            raise httpx.ReadTimeout('PRIVATE_PHONE_AND_TOKEN', request=request)
        directory, calls = self.directory(handler=failure)
        with self.assertRaises(WorkloadError) as caught:
            await directory.lookup('0912345678')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(1, len(calls))
        denied, calls = self.directory(handler=lambda request: httpx.Response(403, json={'error': 'PRIVATE'}))
        with self.assertRaises(WorkloadError):
            await denied.lookup('0912345678')
        self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
