"""Atomic administrator import of exact roster names into private Zalo links."""
import csv
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata

from .bindings import IdentityStore
from .service import TicketError, digest, password_hash


def read_users_csv(path, *, require_ids=True):
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            expected = ['nguoi', 'user_id', 'chat_id', 'member_name']
            if reader.fieldnames != expected:
                raise TicketError('CSV cần đúng các cột: nguoi,user_id,chat_id,member_name.')
            rows = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise TicketError('CSV có dòng thiếu hoặc thừa cột; chưa nhập liên kết nào.')
                if not any(row.values()):
                    continue
                item = dict(row)
                item['nguoi'] = item['nguoi'].strip()
                item['member_name'] = unicodedata.normalize('NFC', item['member_name'].strip())
                if not item['nguoi'] or not 1 <= len(item['member_name']) <= 100:
                    raise TicketError('Mỗi dòng CSV cần nhãn người và họ tên đúng trong cột Lab Coach.')
                if require_ids:
                    IdentityStore._validate('0' * 64, item['user_id'], item['chat_id'])
                rows.append(item)
                if len(rows) > 100:
                    raise TicketError('Mỗi lần nhập tối đa 100 liên kết; chia file trước khi thử lại.')
    except (OSError, UnicodeError, csv.Error):
        raise TicketError('Không đọc được CSV liên kết; kiểm tra file UTF-8 trên máy.') from None
    if not rows:
        raise TicketError('CSV chưa có thành viên để đối chiếu.')
    keys = ('nguoi', 'member_name', 'user_id', 'chat_id') if require_ids else ('nguoi', 'member_name')
    if any(len({item[key] for item in rows}) != len(rows) for key in keys):
        raise TicketError('CSV có nhãn, tên hoặc liên kết trùng; chưa nhập liên kết nào.')
    return rows


def match_members(rows, members):
    """Match case/accents exactly; never let a display name claim an identity."""
    matched = []
    for row in rows:
        found = [member for member in members if member.get('member_name') == row['member_name']]
        if len(found) != 1 or not isinstance(found[0].get('member_id'), str):
            raise TicketError('Có tên không khớp duy nhất với cột Lab Coach; kiểm tra CSV trước khi cấp quyền.')
        member_id = found[0]['member_id']
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', member_id):
            raise TicketError('Mã thành viên từ lịch không hợp lệ; chưa nhập liên kết nào.')
        matched.append(dict(row, member_id=member_id))
    if len({item['member_id'] for item in matched}) != len(matched):
        raise TicketError('Nhiều dòng CSV trỏ tới cùng một thành viên; chưa nhập liên kết nào.')
    return matched


def import_users(store, bot_scope, matched):
    """All-or-nothing local import. Existing bindings keep their revisions."""
    identities = IdentityStore(store)
    for row in matched:
        identities._validate(bot_scope, row['user_id'], row['chat_id'])
    added, unchanged = 0, 0
    try:
        with store.transaction() as db:
            for row in matched:
                accounts = db.execute('SELECT * FROM ticket_accounts WHERE member_id=?',
                                      (row['member_id'],)).fetchall()
                active = [account for account in accounts if account['active'] == 1]
                if len(active) > 1 or accounts and not active:
                    raise TicketError('Một thành viên đã bị thu hồi hoặc có tài khoản mâu thuẫn; quản trị cần xử lý trước.')
                if active:
                    account = active[0]
                    if account['role'] != 'member':
                        raise TicketError('Thành viên đã có vai trò khác; không tự thay đổi tài khoản khi nhập CSV.')
                    account_id = account['id']
                else:
                    account_id = 'zalo-' + digest(row['member_id'])[:24]
                    if db.execute('SELECT 1 FROM ticket_accounts WHERE id=?', (account_id,)).fetchone():
                        raise TicketError('Tên tài khoản đã tồn tại cho liên kết khác; chưa nhập liên kết nào.')
                    db.execute('INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                               (account_id, row['member_name'], 'member', row['member_id'],
                                password_hash(secrets.token_urlsafe(48))))
                bindings = db.execute('SELECT * FROM ticket_zalo_bindings WHERE bot=? '
                                      'AND (user_id=? OR chat_id=? OR account=?)',
                                      (bot_scope, row['user_id'], row['chat_id'], account_id)).fetchall()
                if bindings:
                    if len(bindings) != 1 or any(bindings[0][key] != value for key, value in (
                            ('user_id', row['user_id']), ('chat_id', row['chat_id']),
                            ('account', account_id), ('member_id', row['member_id']), ('role', 'member'))):
                        raise TicketError('Liên kết Zalo đang thuộc tài khoản khác; thu hồi đúng liên kết cũ trước khi nhập lại.')
                    unchanged += 1
                    continue
                db.execute('INSERT INTO ticket_zalo_bindings VALUES (?,?,?,?,?,?,?)',
                           (bot_scope, row['user_id'], row['chat_id'], account_id,
                            row['member_id'], 'member', secrets.token_urlsafe(32)))
                added += 1
    except sqlite3.IntegrityError:
        raise TicketError('Có liên kết hoặc tài khoản trùng; toàn bộ lần nhập đã được hoàn tác.') from None
    return added, unchanged
