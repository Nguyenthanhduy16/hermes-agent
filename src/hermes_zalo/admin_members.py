"""Admin member operations on the running bot's stores, never on a copy.

HTTP authentication and the ``members`` capability are enforced by the caller.
An actor label is audit attribution, not an alternative registration-admin ACL.
All public methods are async and must run on the gateway's event loop.
"""
import asyncio
from contextlib import nullcontext
import hashlib
import json
import re
import secrets
import time

from .commands import normalized
from .registration import RegistrationError
from .tickets.service import ACTIVE, digest, password_hash
from .workload import WorkloadError, normalize_phone


class AdminMemberError(ValueError):
    pass


class MemberAdmin:
    def __init__(self, work, *, clock=time.time, conversations=None):
        self.work, self.clock = work, clock
        self.conversations = conversations
        self._previews = {}
        self._registration_previews = {}

    @property
    def flow(self):
        flow = self.work.registration
        if not flow.enabled or not flow.store:
            raise AdminMemberError('Member registration is not fully configured.')
        return flow

    @staticmethod
    def _actor(actor):
        if not isinstance(actor, str) or not re.fullmatch(r'web:[A-Za-z0-9_.@-]{1,100}', actor):
            raise AdminMemberError('Invalid administrator session.')

    @staticmethod
    def _page(page, limit):
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 100:
            raise AdminMemberError('Invalid page or page size.')

    def _record(self, member_id):
        if not isinstance(member_id, str) or not re.fullmatch(r'member-[a-f0-9]{32}', member_id):
            raise AdminMemberError('Invalid member ID.')
        record = self.flow.store.latest_member(member_id)
        if record is None:
            raise AdminMemberError('Member not found.')
        return record

    def _basic(self, record):
        control = self.flow.store.member_control(record.member_id)
        return dict(member_id=record.member_id, name=record.name, status=record.status,
                    locked=control['locked'], revision=control['revision'],
                    phone_masked='******' + record.phone[-4:], request_id=record.id,
                    zalo_user_id=record.user_id, private_chat_id=record.chat_id,
                    group_chat_ids=[], group_note='Group access is configured separately and cannot be inferred from a private chat.',
                    schedule_name=record.name, name_unique=record.name_unique,
                    created=record.created, updated=max(record.updated, control['updated']))

    async def _chat(self):
        # Opening does not bootstrap or repair existing bindings implicitly.
        return await self.work._get_tickets()

    def _local_link(self, chat, record):
        if chat is None:
            return dict(link_status='disabled', link_reason='Shift exchange is disabled or not ready.', tickets_account=None)
        current = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
        if not current:
            return dict(link_status='missing', link_reason='No active Tickets link exists.', tickets_account=None)
        account = {key: current[key] for key in ('id', 'member_id', 'name', 'role')}
        if not record.name_unique:
            state, reason = 'conflict', 'Duplicate name: a unique ID is required in the schedule source before the mapping can be confirmed.'
        elif current['name'] != record.name:
            state, reason = 'conflict', 'The Tickets link name differs from the registration name.'
        else:
            state, reason = 'linked', 'A local link exists. Schedule features still verify the source before use.'
        return dict(link_status=state, link_reason=reason, tickets_account=account)

    async def list_members(self, query='', status='', link='', page=1, limit=20):
        self._page(page, limit)
        if not isinstance(query, str) or len(query) > 160 or status not in ('', 'pending', 'approved', 'rejected', 'revoked', 'reset', 'locked'):
            raise AdminMemberError('Invalid member filters.')
        if link not in ('', 'linked', 'missing', 'conflict', 'disabled'):
            raise AdminMemberError('Invalid link filters.')
        chat = await self._chat()
        def read():
            with chat.store.lock if chat else nullcontext():
                result = []
                for record in self.flow.store.latest_members():
                    if not status and record.status == 'reset':
                        continue
                    item = self._basic(record) | self._local_link(chat, record)
                    if query and normalized(query) not in normalized(record.name + ' ' + record.member_id):
                        continue
                    if status and not (item['locked'] if status == 'locked' else record.status == status):
                        continue
                    if link and item['link_status'] != link:
                        continue
                    result.append(item)
                return dict(items=result[(page - 1) * limit:page * limit], total=len(result), page=page, limit=limit)
        return await asyncio.to_thread(read)

    async def _validated(self, record, *, unique=False):
        if record.source_key != self.flow.directory.source_key:
            raise AdminMemberError('The registration source has changed. Verify it again before changing access or links.')
        try:
            person = await self.flow.directory.lookup(record.phone)
        except (RegistrationError, WorkloadError):
            raise AdminMemberError('The person could not be verified in Workload. Check the source before making changes.') from None
        if any(getattr(record, key) != getattr(person, key) for key in ('phone', 'name', 'source_key', 'fingerprint')):
            raise AdminMemberError('Workload details have changed. Verify the registration again.')
        if unique and not person.name_unique:
            raise AdminMemberError('Workload contains duplicate names. A unique ID and schedule mapping are required; do not select by name alone.')
        if self._record(record.member_id) != record:
            raise AdminMemberError('The registration has changed. Reload the member details.')
        return person

    async def _source_candidate(self, chat, record):
        if not chat or chat.service.mode not in ('lab_live', 'lab_live_test'):
            raise AdminMemberError('This version only repairs links for the configured Lab schedule source.')
        if record.status != 'approved':
            raise AdminMemberError('Only approved members can have their links repaired. This action does not approve registration.')
        await self._validated(record, unique=True)
        provider = chat.service.provider
        if not self.work.sheets or provider.settings.spreadsheet_id != self.work.sheets.settings.spreadsheet_id:
            raise AdminMemberError('The schedule and Workload sources do not match.')
        if chat.service.mode == 'lab_live_test' and record.name not in chat.service.test_members:
            raise AdminMemberError('This member is outside the shift exchange pilot scope.')
        try:
            members = await asyncio.to_thread(provider.inspect_members)
        except Exception:
            raise AdminMemberError('The schedule roster could not be read. Try again later. No data was changed.') from None
        found = [item for item in members if item.get('member_name') == record.name]
        if len(found) != 1 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', str(found[0].get('member_id', ''))):
            raise AdminMemberError('The name does not match one unique schedule ID. Add a mapping instead of guessing.')
        source = found[0]
        def candidate():
            with chat.store.lock:
                rows = chat.store.db.execute('SELECT id,name,role,member_id,active FROM ticket_accounts WHERE member_id=?',
                                             (source['member_id'],)).fetchall()
                active = [dict(row) for row in rows if row['active']]
                if len(active) > 1 or rows and not active:
                    raise AdminMemberError('The Tickets account is revoked or duplicated. Resolve the data issue first.')
                if active:
                    account = active[0]
                    if account['name'] != record.name or account['role'] != 'member':
                        raise AdminMemberError('The Tickets account name or role does not match. The link was not changed.')
                else:
                    account = dict(id='zalo-' + digest(source['member_id'])[:24], name=record.name,
                                   role='member', member_id=source['member_id'], active=1)
                return dict(account_id=account['id'], member_id=account['member_id'], name=account['name'],
                            create_account=not bool(active))
        return await asyncio.to_thread(candidate)

    async def detail(self, member_id):
        record, chat = self._record(member_id), await self._chat()
        def read():
            with chat.store.lock if chat else nullcontext():
                return self._basic(record) | self._local_link(chat, record)
        result = await asyncio.to_thread(read)
        result['candidates'] = []
        try:
            candidate = await self._source_candidate(chat, record)
            result['candidates'] = [candidate]
            result['source_checked'] = True
            current = result['tickets_account']
            if current and (current['member_id'] != candidate['member_id'] or current['id'] != candidate['account_id']):
                result.update(link_status='conflict', link_reason='The current link differs from the verified schedule ID.')
        except AdminMemberError as error:
            result.update(source_checked=False, source_reason=str(error))
        return result

    async def set_locked(self, member_id, locked, actor, expected_revision, confirm=False):
        self._actor(actor)
        if type(locked) is not bool or type(expected_revision) is not int or expected_revision < 0 or confirm is not True:
            raise AdminMemberError('Confirm the action and provide the displayed record revision.')
        record, chat = self._record(member_id), await self._chat()
        if not locked:
            await self._validated(record)
        def change():
            # Same order as Tickets guards: wait for an in-flight network write
            # to finish before changing grants; no second DB writer/process.
            with chat.store.lock if chat else nullcontext():
                store = self.flow.store
                with store._transaction() as db:
                    if self._record(member_id) != record:
                        raise AdminMemberError('The registration has changed. Reload it before confirming.')
                    before = store.member_control(member_id)
                    if before['revision'] != expected_revision:
                        raise AdminMemberError('The details have been updated. Reload them before confirming.')
                    if before['locked'] != locked:
                        store._bump_revision(db, member_id)
                        db.execute('UPDATE registration_member_controls SET locked=? WHERE member_id=?', (int(locked), member_id))
                        store._audit_admin(db, member_id, actor, 'locked' if locked else 'unlocked',
                                           {'locked': before['locked'], 'status': record.status},
                                           {'locked': locked, 'status': record.status})
                    return self._basic(record) | {'message': 'Bot access was locked.' if locked else 'Bot access was unlocked. Registration status and group access are unchanged.'}
        return await asyncio.to_thread(change)

    def _snapshot(self, chat, record, candidate):
        with chat.store.lock:
            account = chat.store.account(candidate['account_id'])
            if candidate['create_account']:
                if chat.store.db.execute('SELECT 1 FROM ticket_accounts WHERE id=? OR (member_id=? AND active=1)',
                                         (candidate['account_id'], candidate['member_id'])).fetchone():
                    raise AdminMemberError('The target account has changed. Reload it before making changes.')
            elif (not account or any(account[key] != candidate[source] for key, source in
                    (('id', 'account_id'), ('member_id', 'member_id'), ('name', 'name'))) or account['role'] != 'member'):
                raise AdminMemberError('The target account has changed. Reload it before making changes.')
            rows = [dict(row) for row in chat.store.db.execute(
                'SELECT * FROM ticket_zalo_bindings WHERE bot=? AND (user_id=? OR chat_id=? OR account=?) ORDER BY account',
                (chat.bot_scope, record.user_id, record.chat_id, candidate['account_id']))]
            if any(row['user_id'] != record.user_id or row['chat_id'] != record.chat_id for row in rows):
                raise AdminMemberError('The target account or chat belongs to someone else. Nothing was overwritten. Verify and revoke the correct old link first.')
            accounts = {row['account'] for row in rows} | {candidate['account_id']}
            members = {row['member_id'] for row in rows} | {candidate['member_id']}
            for ticket in chat.store.db.execute('SELECT creator,recipient,body FROM exchange_tickets WHERE status IN (?,?,?,?)', ACTIVE):
                body = json.loads(ticket['body'])
                if ticket['creator'] in accounts or ticket['recipient'] in members or any(
                        body.get(side, {}).get('member_id') in members for side in ('a', 'b')):
                    raise AdminMemberError('A shift exchange request is pending or needs reconciliation. Complete or cancel the correct request before repairing the link.')
            return dict(record_id=record.id, user_id=record.user_id, chat_id=record.chat_id,
                        registration_revision=self.flow.store.member_control(record.member_id)['revision'],
                        bindings=rows, candidate=candidate)

    async def preview_link(self, member_id, target_account_id, actor):
        self._actor(actor)
        record, chat = self._record(member_id), await self._chat()
        candidate = await self._source_candidate(chat, record)
        if target_account_id != candidate['account_id']:
            raise AdminMemberError('Choose the verified account from the suggested list.')
        snapshot = await asyncio.to_thread(self._snapshot, chat, record, candidate)
        self._previews = {key: value for key, value in self._previews.items() if value['expires'] > self.clock()}
        if len(self._previews) >= 100:
            raise AdminMemberError('Too many previews are open. Try again in a few minutes.')
        token = secrets.token_urlsafe(32)
        self._previews[hashlib.sha256(token.encode()).hexdigest()] = dict(
            actor=actor, member_id=member_id, snapshot=snapshot, expires=self.clock() + 300)
        return dict(preview_token=token, expires=self.clock() + 300,
                    before=snapshot['bindings'], after=dict(candidate, zalo_user_id=record.user_id, private_chat_id=record.chat_id),
                    message='Repair only this member\'s internal link. Do not change the schedule, grant roles or transfer another person\'s account.')

    async def confirm_link(self, preview_token, actor):
        self._actor(actor)
        if not isinstance(preview_token, str) or not 20 <= len(preview_token) <= 100:
            raise AdminMemberError('Invalid preview.')
        key = hashlib.sha256(preview_token.encode()).hexdigest()
        preview = self._previews.get(key)
        if not preview or preview['actor'] != actor or preview['expires'] <= self.clock():
            raise AdminMemberError('The preview has expired or belongs to another administrator session.')
        record, chat = self._record(preview['member_id']), await self._chat()
        candidate = await self._source_candidate(chat, record)
        def apply():
            with chat.store.lock:
                store = self.flow.store
                with store._transaction() as reg_db:
                    if preview['expires'] <= self.clock() or self._previews.get(key) is not preview:
                        raise AdminMemberError('The preview has expired or has already been used.')
                    if self._record(record.member_id) != record or self._snapshot(chat, record, candidate) != preview['snapshot']:
                        raise AdminMemberError('The details or link have changed. Create a new preview.')
                    before = preview['snapshot']['bindings']
                    with chat.store.transaction() as db:
                        if candidate['create_account']:
                            db.execute('INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                                       (candidate['account_id'], candidate['name'], 'member', candidate['member_id'], password_hash(secrets.token_urlsafe(48))))
                        # Same checks and mutation as IdentityStore.bind, inside
                        # this transaction together with an immutable audit row.
                        db.execute('INSERT INTO ticket_zalo_bindings VALUES (?,?,?,?,?,?,?) '
                                   'ON CONFLICT(bot,user_id,chat_id) DO UPDATE SET account=excluded.account,member_id=excluded.member_id,'
                                   'role=excluded.role,revision=excluded.revision',
                                   (chat.bot_scope, record.user_id, record.chat_id, candidate['account_id'], candidate['member_id'], 'member', secrets.token_urlsafe(32)))
                        db.execute('CREATE TABLE IF NOT EXISTS ticket_registration_links (bot TEXT NOT NULL,member_id TEXT NOT NULL,'
                                   'registration_id TEXT NOT NULL,account TEXT NOT NULL,PRIMARY KEY(bot,member_id))')
                        db.execute('INSERT INTO ticket_registration_links VALUES (?,?,?,?) ON CONFLICT(bot,member_id) '
                                   'DO UPDATE SET registration_id=excluded.registration_id,account=excluded.account',
                                   (chat.bot_scope, record.member_id, record.id, candidate['account_id']))
                        after = dict(candidate, user_id=record.user_id, chat_id=record.chat_id)
                        db.execute('CREATE TABLE IF NOT EXISTS ticket_admin_member_audit (id INTEGER PRIMARY KEY,member_id TEXT NOT NULL,'
                                   'actor TEXT NOT NULL,at REAL NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL)')
                        db.execute('INSERT INTO ticket_admin_member_audit(member_id,actor,at,before_json,after_json) VALUES (?,?,?,?,?)',
                                   (record.member_id, actor, self.clock(), json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
                        store._bump_revision(reg_db, record.member_id)
                        store._audit_admin(reg_db, record.member_id, actor, 'tickets_link', {'bindings': before}, after)
                    self._previews.pop(key, None)
                    return dict(ok=True, member_id=record.member_id, revision=store.member_control(record.member_id)['revision'],
                                message='The Tickets link was updated. The actual schedule was not changed.')
        return await asyncio.to_thread(apply)

    async def _correction_person(self, phone):
        try:
            person = await self.flow.directory.lookup(normalize_phone(phone))
            self.flow.store._person(person)
        except (RegistrationError, WorkloadError) as error:
            raise AdminMemberError(str(error)) from None
        if person.source_key != self.flow.directory.source_key:
            raise AdminMemberError('The Workload source has changed. Reload it before repairing registration.')
        if not person.name_unique:
            raise AdminMemberError('Workload contains duplicate names. Add a unique ID before repairing registration.')
        return person

    async def _correction_schedule(self, chat, person):
        if chat is None:
            if (str(getattr(self.work, '_tickets_values', {}).get('TICKETS_CHAT_MODE') or 'off').lower() != 'off' or
                    getattr(self.work, '_tickets_error', False)):
                raise AdminMemberError('Shift exchange is enabled, but its link could not be verified. Try again later.')
            return None
        if (chat.service.mode not in ('lab_live', 'lab_live_test') or not self.work.sheets or
                chat.service.provider.settings.spreadsheet_id != self.work.sheets.settings.spreadsheet_id):
            raise AdminMemberError('The Tickets source does not support registration repair from this Workload.')
        if chat.service.mode == 'lab_live_test' and person.name not in chat.service.test_members:
            raise AdminMemberError('The selected person is outside the shift exchange pilot scope.')
        try:
            members = await asyncio.to_thread(chat.service.provider.inspect_members)
        except Exception:
            raise AdminMemberError('The schedule roster could not be read. Try again later. Registration was not changed.') from None
        found = [row for row in members if row.get('member_name') == person.name]
        if len(found) != 1 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', str(found[0].get('member_id', ''))):
            raise AdminMemberError('The name does not match one unique schedule ID. Add a mapping before making changes.')
        return dict(member_id=found[0]['member_id'], name=person.name)

    def _correction_tickets_snapshot(self, chat, record, schedule):
        if chat is None:
            return None
        # Caller holds the Tickets lock, including through final registration
        # commit. Never detach a different account's route or an admin binding.
        rows = [dict(row) for row in chat.store.db.execute(
            'SELECT * FROM ticket_zalo_bindings WHERE bot=? AND (user_id=? OR chat_id=? OR member_id=?) ORDER BY account',
            (chat.bot_scope, record.user_id, record.chat_id, schedule['member_id']))]
        if any(row['user_id'] != record.user_id or row['chat_id'] != record.chat_id for row in rows):
            raise AdminMemberError('The target schedule link belongs to another Zalo account. Nothing was overwritten.')
        if any(row['role'] != 'member' for row in rows):
            raise AdminMemberError('This link has administrative privileges and requires a separate operator review.')
        accounts = [dict(row) for row in chat.store.db.execute(
            'SELECT id,name,role,member_id,active FROM ticket_accounts WHERE member_id=? ORDER BY id',
            (schedule['member_id'],))]
        if accounts and (len(accounts) != 1 or accounts[0]['active'] != 1 or
                         accounts[0]['role'] != 'member' or accounts[0]['name'] != schedule['name']):
            raise AdminMemberError('The target schedule account is locked or conflicting. Review it before making changes.')
        old_accounts = [dict(row) for row in chat.store.db.execute(
            'SELECT id,member_id FROM ticket_accounts WHERE name=?', (record.name,))]
        member_ids = {row['member_id'] for row in rows + accounts + old_accounts} | {schedule['member_id']}
        account_ids = {row['account'] for row in rows} | {row['id'] for row in accounts + old_accounts}
        for ticket in chat.store.db.execute('SELECT creator,recipient,body FROM exchange_tickets WHERE status IN (?,?,?,?)', ACTIVE):
            body = json.loads(ticket['body'])
            if ticket['creator'] in account_ids or ticket['recipient'] in member_ids or any(
                    body.get(side, {}).get('member_id') in member_ids for side in ('a', 'b')):
                raise AdminMemberError('A shift exchange request is still being processed. Complete or cancel it before repairing registration.')
        return dict(bindings=rows, accounts=accounts, old_accounts=old_accounts)

    async def preview_registration(self, member_id, phone, reason, actor):
        self._actor(actor)
        record = self._record(member_id)
        person = await self._correction_person(phone)
        chat = await self._chat()
        schedule = await self._correction_schedule(chat, person)
        try:
            reason = self.flow.store._correction_actor_reason(actor, reason)
            with chat.store.lock if chat else nullcontext():
                registration = self.flow.store.correction_snapshot(record, person)
                tickets = self._correction_tickets_snapshot(chat, record, schedule)
        except RegistrationError as error:
            raise AdminMemberError(str(error)) from None
        self._registration_previews = {key: value for key, value in self._registration_previews.items()
                                       if value['expires'] > self.clock()}
        if len(self._registration_previews) >= 100:
            raise AdminMemberError('Too many previews are open. Try again in a few minutes.')
        token = secrets.token_urlsafe(32)
        self._registration_previews[hashlib.sha256(token.encode()).hexdigest()] = dict(
            record=record, person=person, actor=actor, registration=registration, tickets=tickets,
            schedule=schedule, reason=reason, expires=self.clock() + 300)
        return dict(preview_token=token, expires=self.clock() + 300,
                    before=self._basic(record),
                    after=dict(name=person.name, phone_masked='******' + person.phone[-4:],
                               member_id=registration.get('target_member_id'),
                               zalo_user_id=record.user_id, private_chat_id=record.chat_id),
                    message='Verify the account owner before saving. The previous registration will be revoked and the correct person approved. The old context is reset and old manual reminders are stopped; history and issues are not transferred. Schedule cells in Sheets are not changed. The shift exchange link is recreated when the member uses the bot.')

    async def confirm_registration(self, preview_token, actor, *, authorize=None):
        self._actor(actor)
        if not isinstance(preview_token, str) or not 20 <= len(preview_token) <= 100:
            raise AdminMemberError('Invalid preview.')
        key = hashlib.sha256(preview_token.encode()).hexdigest()
        preview = self._registration_previews.get(key)
        if not preview or preview['actor'] != actor or preview['expires'] <= self.clock():
            raise AdminMemberError('The preview has expired or belongs to another administrator.')
        person = await self._correction_person(preview['person'].phone)
        if person != preview['person']:
            raise AdminMemberError('Workload has changed. Create a new preview.')
        chat = await self._chat()
        schedule = await self._correction_schedule(chat, person)
        record, store = preview['record'], self.flow.store
        # No awaits once mutations start: ReminderStore belongs to the bot loop.
        # Dependent cleanup only removes old capabilities. The new identity is
        # activated by the final, atomic registration transaction, so interruption
        # cannot grant the target identity before cleanup has succeeded.
        with chat.store.lock if chat else nullcontext():
            with store.lock:
                if authorize:
                    authorize()
                if preview['expires'] <= self.clock() or self._registration_previews.get(key) is not preview:
                    raise AdminMemberError('The preview has expired or has already been used.')
                try:
                    if (store.correction_snapshot(record, person) != preview['registration'] or
                            schedule != preview['schedule'] or
                            self._correction_tickets_snapshot(chat, record, schedule) != preview['tickets']):
                        raise AdminMemberError('The details have changed. Create a new preview.')
                    self._registration_previews.pop(key, None)
                    self.work.reminders.detach_registration(record.user_id)
                    self.work.issues.detach_registration(record.user_id)
                    if self.conversations is not None:
                        self.conversations.detach_registration_context(record.user_id, record.member_id, actor)
                    if chat:
                        with chat.store.transaction() as db:
                            db.execute('DELETE FROM ticket_zalo_bindings WHERE bot=? AND user_id=? AND chat_id=?',
                                       (chat.bot_scope, record.user_id, record.chat_id))
                            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_registration_links'").fetchone():
                                db.execute('DELETE FROM ticket_registration_links WHERE bot=? AND member_id=?',
                                           (chat.bot_scope, record.member_id))
                    result = store.correct_registration(actor, record, person, preview['registration'], preview['reason'])
                except RegistrationError as error:
                    raise AdminMemberError(str(error)) from None
        return self._basic(result) | dict(ok=True, message='Registration repaired successfully: ' + result.name +
                '. The member can use /dangky trangthai, /lichcuatoi or /doica without registering again.')

    def _reset_tickets_snapshot(self, chat, record):
        """Inspect only local ownership and open work; roster reads are unnecessary.

        A reset must work when the phone/name in Workload was corrected. It
        removes a route, never grants or selects a replacement person.
        """
        if chat is None:
            if (str(getattr(self.work, '_tickets_values', {}).get('TICKETS_CHAT_MODE') or 'off').lower() != 'off' or
                    getattr(self.work, '_tickets_error', False)):
                raise AdminMemberError('Shift exchange is enabled, but its link could not be verified. Try again later.')
            return None
        db = chat.store.db
        bindings = [dict(row) for row in db.execute(
            'SELECT * FROM ticket_zalo_bindings WHERE bot=? AND (user_id=? OR chat_id=?) ORDER BY account',
            (chat.bot_scope, record.user_id, record.chat_id))]
        if any(row['user_id'] != record.user_id or row['chat_id'] != record.chat_id for row in bindings):
            raise AdminMemberError('The chat link conflicts with another account. Review it before resetting registration.')
        if any(row['role'] != 'member' for row in bindings):
            raise AdminMemberError('This link has administrative privileges and requires a separate operator review.')
        links = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_registration_links'").fetchone():
            links = [dict(row) for row in db.execute(
                'SELECT * FROM ticket_registration_links WHERE bot=? AND member_id=?',
                (chat.bot_scope, record.member_id))]
            if any(row['registration_id'] != record.id for row in links):
                raise AdminMemberError('The shift exchange link belongs to another registration. Reload and review it before resetting.')
        accounts = {row['account'] for row in bindings + links}
        names = [dict(row) for row in db.execute('SELECT id,member_id FROM ticket_accounts WHERE name=?', (record.name,))]
        # A legacy account may exist without a route. Its outstanding work must
        # still be resolved, but the reset never changes its account or owners.
        accounts.update(row['id'] for row in names)
        members = {row['member_id'] for row in bindings + names}
        for link in links:
            owner = db.execute('SELECT user_id,chat_id,role FROM ticket_zalo_bindings WHERE bot=? AND account=?',
                               (chat.bot_scope, link['account'])).fetchone()
            if owner and (owner['user_id'] != record.user_id or owner['chat_id'] != record.chat_id or owner['role'] != 'member'):
                raise AdminMemberError('The shift exchange link belongs to another account. Registration was not reset.')
            account = db.execute('SELECT member_id,role FROM ticket_accounts WHERE id=?', (link['account'],)).fetchone()
            if account:
                if account['role'] != 'member':
                    raise AdminMemberError('This link has administrative privileges and requires a separate operator review.')
                members.add(account['member_id'])
        for ticket in db.execute('SELECT creator,recipient,body FROM exchange_tickets WHERE status IN (?,?,?,?)', ACTIVE):
            body = json.loads(ticket['body'])
            if (ticket['creator'] in accounts or ticket['recipient'] in members or any(
                    body.get(side, {}).get('member_id') in members for side in ('a', 'b'))):
                raise AdminMemberError('A shift exchange request is still being processed. Complete or cancel it before resetting registration.')
        return dict(bindings=bindings, links=links)

    async def reset_registration(self, member_id, request_id, expected_revision, reason, actor, *, confirm=False, authorize=None):
        """Withdraw this enrollment, keeping a tombstone until a fresh DM request.

        Cleanup only removes capabilities and completes before the final store
        transaction. No await between cleanup and commit: stale model turns or
        background deliveries cannot inherit a newly registered identity.
        """
        self._actor(actor)
        if (confirm is not True or type(expected_revision) is not int or expected_revision < 0 or
                not isinstance(request_id, str) or not re.fullmatch(r'DK-[A-F0-9]{8}', request_id)):
            raise AdminMemberError('Confirm the correct registration and provide the displayed record revision.')
        record = self._record(member_id)
        if record.id != request_id:
            raise AdminMemberError('The registration has changed. Reload it before resetting.')
        chat = await self._chat()
        store = self.flow.store
        with chat.store.lock if chat else nullcontext():
            with store.lock:
                if authorize:
                    authorize()
                try:
                    reason = store._correction_actor_reason(actor, reason)
                    snapshot = store.reset_snapshot(record)
                    if store.member_control(member_id)['revision'] != expected_revision:
                        raise AdminMemberError('The details have been updated. Reload them before resetting.')
                    self._reset_tickets_snapshot(chat, record)
                    self.work.reminders.detach_registration(record.user_id)
                    self.work.issues.detach_registration(record.user_id)
                    self.work._managed_issue_store.detach_registration(record.member_id, record.user_id, record.chat_id)
                    if self.conversations is not None:
                        self.conversations.detach_registration_context(record.user_id, record.member_id, actor, stop_personal=True)
                    if chat:
                        with chat.store.transaction() as db:
                            db.execute('DELETE FROM ticket_zalo_bindings WHERE bot=? AND user_id=? AND chat_id=?',
                                       (chat.bot_scope, record.user_id, record.chat_id))
                            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_registration_links'").fetchone():
                                db.execute('DELETE FROM ticket_registration_links WHERE bot=? AND member_id=? AND registration_id=?',
                                           (chat.bot_scope, record.member_id, record.id))
                    result = store.reset_registration(actor, record, snapshot, reason)
                except RegistrationError as error:
                    raise AdminMemberError(str(error)) from None
        return self._basic(result) | dict(ok=True, message='Registration was reset. The member can message the bot privately with /dangky PHONE_NUMBER using their current Workload phone number to register again.')

    async def audit(self, member_id, page=1, limit=20):
        self._record(member_id)
        self._page(page, limit)
        with self.flow.store.lock:
            rows = self.flow.store.db.execute('SELECT id,actor,action,at,before_json,after_json FROM registration_admin_audit '
                                              'WHERE member_id=? ORDER BY id DESC', (member_id,)).fetchall()
            items = [dict(id=row['id'], actor=row['actor'], action=row['action'], at=row['at'],
                          before=json.loads(row['before_json']), after=json.loads(row['after_json'])) for row in rows]
        return dict(items=items[(page - 1) * limit:page * limit], total=len(items), page=page, limit=limit)
