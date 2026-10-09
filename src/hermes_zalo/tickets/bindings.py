"""Administrator-verified private Zalo identities, scoped to one bot."""
import re
import secrets
import unicodedata

from .service import TicketError


class IdentityStore:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS ticket_zalo_bindings (
                  bot TEXT NOT NULL, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                  account TEXT NOT NULL, member_id TEXT NOT NULL, role TEXT NOT NULL,
                  revision TEXT NOT NULL,
                  PRIMARY KEY(bot,user_id,chat_id), UNIQUE(bot,user_id),
                  UNIQUE(bot,chat_id), UNIQUE(bot,account));
            ''')

    @staticmethod
    def _validate(bot_scope, user_id, chat_id):
        # Zalo IDs are opaque strings, not an ASCII-only naming convention.
        # Exact SQL parameters provide isolation; reject only invalid lengths
        # and invisible separators/control characters, never normalize IDs.
        if (not isinstance(bot_scope, str) or not re.fullmatch(r'[a-f0-9]{64}', bot_scope)
                or any(not isinstance(value, str) or not 1 <= len(value) <= 200
                       or any(char.isspace() or unicodedata.category(char) in ('Cc', 'Cf', 'Cs')
                              for char in value)
                       for value in (user_id, chat_id))):
            raise TicketError('Cần đúng mã người dùng và cuộc trò chuyện riêng do bot nhận được.')

    def bind(self, bot_scope, user_id, chat_id, account_id):
        self._validate(bot_scope, user_id, chat_id)
        with self.store.transaction() as db:
            account = self.store.account(account_id)
            if (not account or account['role'] not in ('member', 'manager')
                    or (account['role'] == 'member' and not account['member_id'])):
                raise TicketError('Tài khoản chưa được quản trị xác minh hoặc đã bị thu hồi.')
            existing = db.execute('SELECT user_id,chat_id,account FROM ticket_zalo_bindings WHERE bot=? '
                                  'AND (user_id=? OR chat_id=? OR account=?)',
                                  (bot_scope, user_id, chat_id, account_id)).fetchall()
            if any(row['user_id'] != user_id or row['chat_id'] != chat_id for row in existing):
                raise TicketError('Người dùng, cuộc trò chuyện hoặc tài khoản đã liên kết; thu hồi liên kết cũ trước.')
            revision = secrets.token_urlsafe(32)
            db.execute('INSERT INTO ticket_zalo_bindings VALUES (?,?,?,?,?,?,?) '
                       'ON CONFLICT(bot,user_id,chat_id) DO UPDATE SET account=excluded.account,'
                       'member_id=excluded.member_id,role=excluded.role,revision=excluded.revision',
                       (bot_scope, user_id, chat_id, account_id, account['member_id'], account['role'], revision))
        return self.resolve(bot_scope, user_id, chat_id)

    def revoke(self, bot_scope, user_id, chat_id):
        self._validate(bot_scope, user_id, chat_id)
        with self.store.transaction() as db:
            db.execute('DELETE FROM ticket_zalo_bindings WHERE bot=? AND user_id=? AND chat_id=?',
                       (bot_scope, user_id, chat_id))

    def _account(self, row):
        if row is None:
            return None
        account = self.store.account(row['account'])
        if (not account or account['role'] not in ('member', 'manager')
                or account['member_id'] != row['member_id'] or account['role'] != row['role']):
            return None
        return dict(account, _binding=row['revision'])

    def resolve(self, bot_scope, user_id, chat_id):
        self._validate(bot_scope, user_id, chat_id)
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM ticket_zalo_bindings WHERE bot=? AND user_id=? AND chat_id=?',
                                        (bot_scope, user_id, chat_id)).fetchone()
            return self._account(row)

    def linked_accounts(self, bot_scope):
        self._validate(bot_scope, 'lookup', 'lookup')
        with self.store.lock:
            rows = self.store.db.execute('SELECT * FROM ticket_zalo_bindings WHERE bot=? ORDER BY account',
                                        (bot_scope,)).fetchall()
            return [account for row in rows if (account := self._account(row)) is not None]

    def destination(self, bot_scope, account_id):
        """Return only an administrator-verified private route for this bot."""
        self._validate(bot_scope, 'lookup', 'lookup')
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM ticket_zalo_bindings WHERE bot=? AND account=?',
                                        (bot_scope, account_id)).fetchone()
            account = self._account(row)
            if account is None:
                return None
            return dict(account, user_id=row['user_id'], chat_id=row['chat_id'])
