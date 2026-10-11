"""Read-only registration preflight with synthetic Sheets responses only."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from hermes_zalo.sheets import SheetsClient
from hermes_zalo.workload import SCHEMA_ERROR, WorkloadError


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('registration_check_script', ROOT / 'scripts' / 'registration.py')
registration_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration_cli)


def configuration():
    return {'ZALO_SHEETS_ID': 'synthetic_workload_123', 'ZALO_GOOGLE_CREDENTIALS': 'private-key.json',
            'ZALO_ISSUES_ENABLED': 'true', 'ZALO_CALENDAR_FORMAT': 'unrelated-unconfigured-value',
            'REGISTRATION_ENABLED': 'false', 'REGISTRATION_ADMIN_USERS': '',
            'REGISTRATION_WORKLOAD_TAB': 'Workload - Lab coach', 'REGISTRATION_WORKLOAD_NAME_COLUMN': 'D',
            'REGISTRATION_WORKLOAD_PHONE_COLUMN': 'C', 'REGISTRATION_WORKLOAD_HEADER_ROW': '5',
            'REGISTRATION_WORKLOAD_NAME_HEADER': 'Thành viên', 'REGISTRATION_WORKLOAD_PHONE_HEADER': 'Số điện thoại'}


def payload():
    records = [('Tên Riêng', '0912345678'), ('Ten Rieng', '0387654321'),
               ('Người Khác', '+84 912 345 678'), ('Thiếu Số', ''), ('Sai Số', '912345678')]
    blocks = []
    for column, header, index in ((3, 'Thành viên', 0), (2, 'Số điện thoại', 1)):
        blocks.append({'startRow': 4, 'startColumn': column, 'rowData': [
            {'values': [{'formattedValue': value, 'userEnteredValue': {'stringValue': value}}]}
            for value in [header, ''] + [record[index] for record in records]]})
    return {'spreadsheetId': 'synthetic_workload_123', 'sheets': [{
        'properties': {'sheetId': 1, 'title': 'Workload - Lab coach',
                       'gridProperties': {'rowCount': 1000, 'columnCount': 90}},
        'merges': [{'startRowIndex': 4, 'endRowIndex': 6, 'startColumnIndex': column, 'endColumnIndex': column + 1}
                   for column in (2, 3)], 'data': blocks}]}


class RegistrationCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_feature_without_admin_reads_aggregates_with_readonly_scope(self):
        values = configuration()
        original_values = dict(values)
        captured_configs, calls, clients = [], [], []

        async def token():
            return 'synthetic-token'

        def factory(settings):
            captured_configs.append(settings)
            def transport(request):
                calls.append(request)
                return httpx.Response(200, json=payload())
            client = SheetsClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
                                  token_provider=token)
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            env_before = dict(os.environ)
            output = io.StringIO()
            with patch.object(registration_cli, 'ROOT', folder), patch.object(registration_cli, 'SheetsClient', factory), \
                    patch('sqlite3.connect', side_effect=AssertionError('No database permitted')), \
                    contextlib.redirect_stdout(output):
                result = await registration_cli.check(values)
            self.assertEqual([], list(folder.iterdir()))
            self.assertEqual(env_before, dict(os.environ))
        self.assertEqual(original_values, values)
        self.assertEqual({'rows': 5, 'valid_phone_rows': 3, 'invalid_phone_rows': 2,
                          'duplicate_phone_groups': 1, 'duplicate_name_groups': 1}, result)
        self.assertEqual(1, len(captured_configs))
        self.assertFalse(captured_configs[0].issues_enabled)
        self.assertTrue(clients[0].client.is_closed)
        self.assertEqual(['GET'], [request.method for request in calls])
        self.assertEqual(["'Workload - Lab coach'!D5:D1005", "'Workload - Lab coach'!C5:C1005"],
                         calls[0].url.params.get_list('ranges'))
        text = output.getvalue()
        self.assertIn('Dòng có dữ liệu: 5', text)
        self.assertIn('Nhóm số điện thoại trùng: 1', text)
        for private in ('Tên Riêng', 'Ten Rieng', 'Người Khác', '0912345678', '0387654321',
                        '912345678', 'synthetic_workload_123', 'synthetic-token', 'private-key.json'):
            self.assertNotIn(private, text)

    async def test_schema_failure_closes_client_and_does_not_print_partial_directory(self):
        data = payload()
        data['sheets'][0]['data'][0]['rowData'][0]['values'][0]['formattedValue'] = 'PRIVATE_WRONG_HEADER'
        clients = []

        async def token():
            return 'synthetic-token'

        def factory(settings):
            client = SheetsClient(settings, client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))), token_provider=token)
            clients.append(client)
            return client

        output = io.StringIO()
        with patch.object(registration_cli, 'SheetsClient', factory), contextlib.redirect_stdout(output):
            with self.assertRaises(WorkloadError) as caught:
                await registration_cli.check(configuration())
        self.assertEqual(SCHEMA_ERROR, str(caught.exception))
        self.assertEqual('', output.getvalue())
        self.assertTrue(clients[0].client.is_closed)

    async def test_explicit_schema_required_before_creating_network_client(self):
        values = configuration()
        del values['REGISTRATION_WORKLOAD_PHONE_HEADER']
        with patch.object(registration_cli, 'SheetsClient') as factory:
            with self.assertRaises(WorkloadError):
                await registration_cli.check(values)
        factory.assert_not_called()


class RegistrationCheckEntryTests(unittest.TestCase):
    def test_literal_env_read_preserves_file_and_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            content = '# comment\nREGISTRATION_ENABLED=false\nSOURCE_LITERAL="$(private) ${secret}"\n'
            path.write_text(content, encoding='utf-8-sig')
            before = path.read_bytes()
            environment = {'REGISTRATION_ENABLED': 'true'}
            result = registration_cli.local_values(path, environment)
            self.assertEqual({'REGISTRATION_ENABLED': 'true', 'SOURCE_LITERAL': '$(private) ${secret}'}, result)
            self.assertEqual({'REGISTRATION_ENABLED': 'true'}, environment)
            self.assertEqual(before, path.read_bytes())

    def test_entry_point_fixed_errors_and_success_exit_codes(self):
        for error, expected in ((None, 0), (WorkloadError(SCHEMA_ERROR), 1), (RuntimeError('PRIVATE_TOKEN_PHONE'), 1)):
            runner = AsyncMock(side_effect=error)
            output = io.StringIO()
            with patch.object(registration_cli, 'check', runner), \
                    patch.object(registration_cli, 'local_values', return_value={}), contextlib.redirect_stdout(output):
                self.assertEqual(expected, registration_cli.main(['check']))
            runner.assert_awaited_once_with({})
            self.assertNotIn('PRIVATE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
