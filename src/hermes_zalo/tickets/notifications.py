"""Durable, one-attempt private notifications with separately scoped actions.

Delivery is owned by the adapter. A crash or ambiguous send never requeues a
message; users can always reopen their request using /choxacnhan.
"""
import json

from .service import CONFIRM_TTL_SECONDS, TicketError, digest, packed


class PeerNotifications:
    def __init__(self, chat):
        self.chat = chat
        self.service, self.identities = chat.service, chat.identities
        self.store, self.clock, self.bot = chat.store, chat.clock, chat.bot_scope
        with self.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ticket_peer_notifications (
                id INTEGER PRIMARY KEY, bot TEXT NOT NULL, ticket TEXT NOT NULL,
                context TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                code TEXT UNIQUE, expires REAL, used INTEGER NOT NULL DEFAULT 0,
                UNIQUE(bot,ticket))''')
            db.execute("UPDATE ticket_peer_notifications SET status='uncertain' WHERE bot=? AND status='sending'",
                       (self.bot,))

    @staticmethod
    def _identity(route):
        return {key: route[key] for key in ('id', 'member_id', 'role', '_binding', 'user_id', 'chat_id')}

    def enqueue_callback(self, user, peer):
        creator = self.identities.destination(self.bot, user['id'])
        recipient = self.identities.destination(self.bot, peer['id'])
        if (not creator or not recipient or creator['_binding'] != user['_binding']
                or recipient['_binding'] != peer['_binding']):
            raise TicketError('Liên kết nhắn riêng đã thay đổi. Gửi /doica để kiểm tra lại.', 409)
        context = packed(dict(creator=self._identity(creator), recipient=self._identity(recipient),
                              target=self.service.target, require_manager=self.service.require_manager, version=1))

        def enqueue(db, ticket):
            db.execute('INSERT INTO ticket_peer_notifications(bot,ticket,context,status,created) VALUES (?,?,?,?,?)',
                       (self.bot, ticket, context, 'queued', self.clock()))
        return enqueue

    def _valid(self, notice, *, allowed_users=None):
        context = json.loads(notice['context'])
        if (context['target'] != self.service.target
                or context['require_manager'] != self.service.require_manager):
            return None
        for side in ('creator', 'recipient'):
            expected = context[side]
            current = self.identities.destination(self.bot, expected['id'])
            if (not current or self._identity(current) != expected
                    or allowed_users is not None and current['user_id'] not in allowed_users
                    or self.chat.live_test and (current['role'] != 'member'
                                                or current['name'] not in self.service.test_members)):
                return None
        row = self.store.db.execute('SELECT * FROM exchange_tickets WHERE id=?', (notice['ticket'],)).fetchone()
        if (not row or row['status'] != 'pending_peer' or row['version'] != context['version']
                or row['expires'] <= self.clock() or row['target'] != context['target']
                or bool(row['require_manager']) != context['require_manager']
                or row['creator'] != context['creator']['id']
                or row['recipient'] != context['recipient']['member_id']):
            return None
        return context, row

    def claim(self, allowed_users):
        """Claim one eligible item. A claimed item must never be sent twice."""
        allowed_users = frozenset(allowed_users)
        with self.store.lock:
            rows = self.store.db.execute("SELECT * FROM ticket_peer_notifications WHERE bot=? AND status='queued' ORDER BY id",
                                         (self.bot,)).fetchall()
            for notice in rows:
                valid = self._valid(notice, allowed_users=allowed_users)
                if not valid:
                    self._status(notice['id'], 'blocked')
                    continue
                context, row = valid
                try:
                    # Do not send an old proposal after the source changed.
                    self.service._still_valid(row)
                    recipient = self.identities.resolve(self.bot, context['recipient']['user_id'], context['recipient']['chat_id'])
                    ticket = self.service.detail(recipient, row['id'])
                    # Reading the source can take long enough for the ticket
                    # to expire; detail() may already have transitioned it.
                    valid = self._valid(notice, allowed_users=allowed_users)
                    if not valid:
                        self._status(notice['id'], 'blocked')
                        continue
                    context, row = valid
                    code = self.chat._confirmation_code()
                    expires = min(self.clock() + CONFIRM_TTL_SECONDS, row['expires'])
                    text = self.chat._notification_display(ticket, code, expires)
                except TicketError as exc:
                    self._status(notice['id'], 'failed' if exc.status == 503 else 'blocked')
                    continue
                except Exception:
                    self._status(notice['id'], 'failed')
                    continue
                if not self._valid(notice, allowed_users=allowed_users):
                    self._status(notice['id'], 'blocked')
                    continue
                with self.store.transaction() as db:
                    db.execute("UPDATE ticket_peer_notifications SET status='sending',code=?,expires=? WHERE id=? AND status='queued'",
                               (code, expires, notice['id']))
                return dict(id=notice['id'], user_id=context['recipient']['user_id'],
                            chat_id=context['recipient']['chat_id'], text=text)
            return None

    def _status(self, notification_id, status):
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_peer_notifications SET status=? WHERE id=? AND bot=?',
                       (status, notification_id, self.bot))

    def finish(self, notification_id, status):
        if status not in ('sent', 'failed', 'uncertain', 'blocked'):
            raise ValueError('Invalid notification result')
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_peer_notifications SET status=? WHERE id=? AND bot=? AND status='sending'",
                       (status, notification_id, self.bot))

    def action(self, scope, user, code, action):
        """Return None for a non-notification code, otherwise execute exactly it."""
        notice = self.store.db.execute('SELECT * FROM ticket_peer_notifications WHERE bot=? AND code=?',
                                       (self.bot, code)).fetchone()
        if notice is None:
            return None
        context = json.loads(notice['context'])
        peer = context['recipient']
        expected_scope = digest(packed([self.bot, 'PRIVATE', peer['user_id'], peer['chat_id']]))
        if (scope != expected_scope or user['id'] != peer['id'] or user['_binding'] != peer['_binding']
                or action not in ('accept', 'reject') or notice['used']
                or notice['status'] not in ('sending', 'sent', 'uncertain')
                or notice['expires'] is None or notice['expires'] <= self.clock() or not self._valid(notice)):
            raise TicketError('Mã không còn hợp lệ cho bạn hoặc đã hết hạn. Gửi /choxacnhan để xem yêu cầu chờ xác nhận.', 409)
        # Consume before crossing the service/provider write boundary. A failed
        # or interrupted action requires a fresh explicit review, never replay.
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_peer_notifications SET used=1 WHERE id=?', (notice['id'],))
        return self.service.action(user, notice['ticket'], action, context['version'])
