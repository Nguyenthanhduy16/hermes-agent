"""Read-only schedule snapshots for private, registered-member reminders."""
import asyncio
from datetime import datetime
import logging
import threading
import time

from .registration import RegistrationError
from .schedule_reminders import AutoReminderSettings, ScheduleReminderStore, plan_notices


logger = logging.getLogger(__name__)


class ScheduleReminderFlow:
    # Reuse one fresh batch for a few recipients, rather than rereading the full
    # roster per member. Admin writes invalidate it immediately in this process.
    SNAPSHOT_SECONDS = 5

    def __init__(self, work, values, path, *, monotonic=time.monotonic):
        self.work = work
        self.error = False
        try:
            self.settings = AutoReminderSettings.from_mapping(values)
        except ValueError:
            self.settings = AutoReminderSettings.from_mapping({})
            self.error = True
            logger.warning('Automatic schedule reminders disabled: invalid configuration')
        self.store = ScheduleReminderStore(path)
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._change_lock = threading.Lock()
        self._epoch = 0
        self._writers = 0
        self._snapshot = None

    @property
    def ready(self):
        return bool(self.settings.enabled and not self.error and self.work.registration.enabled
                    and self.work.sheets and self.work.sheets.settings.calendar_format == 'lab')

    def begin_schedule_change(self):
        with self._change_lock:
            self._writers += 1
            self._epoch += 1
            self._snapshot = None

    def end_schedule_change(self):
        with self._change_lock:
            self._writers = max(0, self._writers - 1)
            self._epoch += 1
            self._snapshot = None

    def close(self):
        self.store.close()

    def _record_allowed(self, record):
        work = self.work
        return bool(self.ready and record and work.registration.identity(record.user_id) == record
                    and work.registration.member_allows(record.user_id, record.chat_id, 'PRIVATE')
                    and work.member_feature_allowed(record.user_id, record.chat_id, 'PRIVATE')
                    and work._calendar_scope_allowed(record.user_id, record.chat_id, 'PRIVATE'))

    async def _read_snapshot(self, *, force=False):
        if not self.ready or self._writers:
            return None
        async with self._lock:
            previous = self._snapshot
            if (not force and previous and previous['epoch'] == self._epoch
                    and self._monotonic() - previous['at'] < self.SNAPSHOT_SECONDS):
                return previous
            # Never fall back to an older cache if a live source fails.
            self._snapshot = None
            epoch = self._epoch
            registration = self.work.registration
            records = [record for record in registration.store.approved_records(registration.directory.source_key)
                       if self._record_allowed(record)]
            if not records:
                return None
            people = await registration.directory.lookup_many(record.phone for record in records)
            verified = {}
            for record in records:
                person = people.get(record.phone)
                if person is None or not self._record_allowed(record):
                    continue
                try:
                    registration._validated_person(record, person, require_unique_name=True)
                except RegistrationError:
                    continue
                verified[record.member_id] = record
            if not verified:
                return None
            schedules = await self.work.sheets._lab_schedules()
            with self._change_lock:
                if not self.ready or self._writers or epoch != self._epoch:
                    return None
                self._snapshot = dict(epoch=epoch, at=self._monotonic(), records=verified, schedules=schedules)
                return self._snapshot

    @staticmethod
    def _timestamp(now):
        return now.timestamp() if isinstance(now, datetime) else time.time() if now is None else float(now)

    def _plan(self, snapshot, record, now):
        if not self._record_allowed(record):
            return []
        return plan_notices(self.settings, snapshot['schedules'],
                            source_key=self.work.sheets.settings.spreadsheet_id,
                            member_id=record.member_id, member_name=record.name,
                            user_id=record.user_id, chat_id=record.chat_id, now=now)

    async def candidates(self, now=None):
        now = self._timestamp(now)
        snapshot = await self._read_snapshot(force=True)
        if not snapshot:
            return []
        result = []
        for record in snapshot['records'].values():
            result.extend(self._plan(snapshot, record, now))
        return sorted(result, key=lambda notice: (notice.due_at, notice.id))

    async def revalidate(self, notice, now=None):
        now = self._timestamp(now)
        if not self.ready or self._writers or not notice.due_at <= now < notice.expires_at:
            return None
        snapshot = await self._read_snapshot()
        if snapshot is None:
            return None
        record = snapshot['records'].get(notice.member_id)
        if (not record or record.user_id != notice.user_id or record.chat_id != notice.chat_id
                or not self._record_allowed(record)):
            return None
        return next((fresh for fresh in self._plan(snapshot, record, now) if fresh.id == notice.id), None)

    def delivery_allowed(self, notice, now=None):
        now = self._timestamp(now)
        with self._change_lock:
            snapshot = self._snapshot
            if (not self.ready or self._writers or not snapshot or snapshot['epoch'] != self._epoch
                    or self._monotonic() - snapshot['at'] >= self.SNAPSHOT_SECONDS
                    or not notice.due_at <= now < notice.expires_at):
                return False
            record = snapshot['records'].get(notice.member_id)
            return bool(record and record.user_id == notice.user_id and record.chat_id == notice.chat_id
                        and self._record_allowed(record)
                        and any(fresh == notice for fresh in self._plan(snapshot, record, now)))
