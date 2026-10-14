"""Automatic notifications use approved identities and fresh synthetic sources."""
from dataclasses import replace
from datetime import date, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.lab_calendar import LabEvent, LabSchedule
from hermes_zalo.reminders import VIETNAM_TZ
from hermes_zalo.sheets import SheetsError, SheetsSettings
from hermes_zalo.work import WorkService
from hermes_zalo.workload import WorkloadPerson, WorkloadSettings


class ScheduleReminderFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='operator',
            REGISTRATION_WORKLOAD_TAB='Workload Test', REGISTRATION_WORKLOAD_NAME_COLUMN='A',
            REGISTRATION_WORKLOAD_PHONE_COLUMN='B', REGISTRATION_WORKLOAD_HEADER_ROW='1',
            REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên', REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại',
            ZALO_AUTO_REMINDERS_ENABLED='true', TICKETS_LAB_PERIODS_JSON=json.dumps(
                {'Sáng': {'start': '09:00', 'end': '13:00'}}))
        self.settings = Settings('test-auto-notices', frozenset({'operator', 'static-unregistered'}),
            frozenset({'group-id'}), state_dir=self.root, knowledge_dir=self.root/'no-docs')
        self.sheet_settings = SheetsSettings('synthetic-sheet', self.root/'unused.json', calendar_format='lab',
            calendar_tabs=('Lịch lab thử',), issues_enabled=False)
        cfg = WorkloadSettings.from_mapping(self.values, self.sheet_settings)
        self.people = {str(i): WorkloadPerson('Người ' + name, '090000000' + str(i), cfg.source_key,
                                             'fingerprint-' + str(i), True)
                       for i, name in ((1, 'Một'), (2, 'Hai'), (3, 'Ba'))}
        def lookup_many(phones):
            requested = set(phones)
            return {p.phone: p for p in self.people.values() if p.phone in requested}
        self.directory = SimpleNamespace(source_key=cfg.source_key, lookup_many=AsyncMock(side_effect=lookup_many))
        self.event = LabEvent(date(2026,10,15), 'Python', 'Bài 1', 'Người Ba', 'Lab 1', 'Sáng', 10, 11,
                             ['Người Một', 'Người Hai'], ['Người Một', 'Người Hai'])
        self.schedules = [LabSchedule('Lịch lab thử', 12, {self.event.day}, [self.event])]
        self.sheets = SimpleNamespace(settings=self.sheet_settings,
            _lab_schedules=AsyncMock(return_value=self.schedules), close=AsyncMock())
        self.work = WorkService(self.settings, sheets_client=self.sheets, environ=self.values,
                                registration_directory=self.directory)
        self.records = {}
        for i, person in self.people.items():
            message = IncomingMessage('private-' + i, 'zalo-' + i, 'enroll-' + i, 'PRIVATE', '')
            self.records[i] = self.work.registration.store.request(message, person, auto_approve=i != '3')
        self.now = datetime(2026,10,14,20,0,tzinfo=VIETNAM_TZ).timestamp()
        self.tick = 100
        self.work._automatic_schedule._monotonic = lambda: self.tick

    async def asyncTearDown(self):
        await self.work.close()
        self.temp.cleanup()

    async def notices(self):
        return await self.work.automatic_schedule_candidates(self.now)

    async def test_one_batch_read_only_registered_private_exact_lab_coaches(self):
        notices = await self.notices()
        self.assertEqual({'private-1', 'private-2'}, {n.chat_id for n in notices})
        self.assertEqual({r.member_id for i,r in self.records.items() if i != '3'}, {n.member_id for n in notices})
        self.directory.lookup_many.assert_awaited_once()
        self.sheets._lab_schedules.assert_awaited_once()
        for notice in notices:
            self.assertTrue(self.work.automatic_schedule_delivery_allowed(notice, self.now))
            self.assertEqual(notice, await self.work.revalidate_automatic_schedule_candidate(notice, self.now))
            self.assertNotEqual(notice.user_id, notice.chat_id)
            self.assertNotIn('090000000', notice.text)
        self.assertEqual(1, self.sheets._lab_schedules.await_count)

    async def test_duplicate_name_or_changed_workload_identity_is_skipped(self):
        self.people['1'] = replace(self.people['1'], name_unique=False)
        self.people['2'] = replace(self.people['2'], fingerprint='changed-identity')
        self.assertEqual([], await self.notices())
        self.sheets._lab_schedules.assert_not_awaited()

    async def test_revocation_during_network_read_or_after_plan_blocks_delivery(self):
        original = self.sheets._lab_schedules
        async def revoke_while_reading():
            self.work.registration.store.revoke('operator', self.records['1'].member_id)
            return self.schedules
        original.side_effect = revoke_while_reading
        notices = await self.notices()
        self.assertEqual(['private-2'], [n.chat_id for n in notices])
        self.work.registration.store.revoke('operator', self.records['2'].member_id)
        self.assertFalse(self.work.automatic_schedule_delivery_allowed(notices[0], self.now))
        self.assertIsNone(await self.work.revalidate_automatic_schedule_candidate(notices[0], self.now))

    async def test_admin_swap_invalidates_old_owner_and_refreshes_fresh_schedule(self):
        self.event.coach_names = ['Người Một']
        old = (await self.notices())[0]
        self.work.begin_schedule_change()
        self.assertFalse(self.work.automatic_schedule_delivery_allowed(old, self.now))
        self.assertEqual([], await self.notices())
        self.event.coach_names = ['Người Hai']
        self.work.end_schedule_change()
        self.assertIsNone(await self.work.revalidate_automatic_schedule_candidate(old, self.now))
        new = await self.notices()
        self.assertEqual(['private-2'], [n.chat_id for n in new])

    async def test_snapshot_expiry_and_source_failure_never_use_stale_payload(self):
        notice = (await self.notices())[0]
        self.tick += 6
        self.assertFalse(self.work.automatic_schedule_delivery_allowed(notice, self.now))
        self.sheets._lab_schedules.side_effect = SheetsError('synthetic unavailable')
        with self.assertRaises(SheetsError):
            await self.work.revalidate_automatic_schedule_candidate(notice, self.now)
        self.assertFalse(self.work.automatic_schedule_delivery_allowed(notice, self.now))

    async def test_source_read_racing_swap_is_discarded(self):
        async def changed_during_read():
            self.work.begin_schedule_change()
            self.work.end_schedule_change()
            return self.schedules
        self.sheets._lab_schedules.side_effect = changed_during_read
        self.assertEqual([], await self.notices())

    async def test_disabled_or_registration_unavailable_never_reads_sources(self):
        self.work._automatic_schedule.settings = replace(self.work.schedule_reminder_settings, enabled=False)
        self.assertEqual([], await self.notices())
        self.directory.lookup_many.assert_not_awaited()
        self.sheets._lab_schedules.assert_not_awaited()
        self.work._automatic_schedule.settings = self.work.schedule_reminder_settings
        self.work.registration.enabled = False
        self.assertEqual([], await self.notices())

    async def test_menu_hides_manual_command_and_notifications_explain_real_policy(self):
        message = IncomingMessage('private-1','zalo-1','help','PRIVATE','/menu')
        self.assertNotIn('/nhac', self.work.help('', message))
        text = self.work.help('thongbao', message)
        self.assertIn('20:00', text)
        self.assertIn('30 phút', text)
        self.assertIn('check-in', text)


if __name__ == '__main__':
    unittest.main()
