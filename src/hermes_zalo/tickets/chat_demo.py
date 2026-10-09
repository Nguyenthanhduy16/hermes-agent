"""Replay synthetic A/B/manager messages locally; no credentials or network."""
import re
import json
from pathlib import Path
import sys
import tempfile

from ..events import IncomingMessage
from .bindings import IdentityStore
from .chat import TicketsChat
from .service import DemoAssignments, TicketService, TicketStore, digest


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    print('LABCOACH TICKETS — MÔ PHỎNG HỘI THOẠI RIÊNG\n'
          'Tự chạy A → B → quản lý. Không kết nối Zalo, Google hoặc AI.\n')
    with tempfile.TemporaryDirectory(prefix='labcoach-chat-demo-') as folder:
        store = TicketStore(Path(folder) / 'demo.sqlite3')
        provider = DemoAssignments(store)
        service = TicketService(store, provider)
        scope = digest('offline-synthetic-bot')
        identities = IdentityStore(store)
        for user in ('a', 'b', 'manager'):
            identities.bind(scope, user, 'dm-' + user, 'demo-' + user)
        chat = TicketsChat(service, identities, bot_scope=scope)
        sequence = 0
        def say(who, text):
            nonlocal sequence
            sequence += 1
            print(f'[{who.upper()} → BOT] {text}')
            reply = chat.handle(IncomingMessage('dm-' + who, who, str(sequence), 'PRIVATE', text))
            if reply is None:
                raise RuntimeError('Demo message was not handled')
            print(f'[BOT → {who.upper()}]\n{reply}\n')
            return reply
        def choose(who, reply, label):
            scope_key = chat._scope(IncomingMessage('dm-' + who, who, 'lookup', 'PRIVATE', ''))
            body = json.loads(store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope_key,)).fetchone()[0])
            code = next(c['code'] for c in body['choices'] if c['item']['id'] == label)
            if not re.search(r'^' + re.escape(code) + r'\. ', reply, re.M):
                raise RuntimeError('Expected demo choice is not visible')
            return say(who, code)
        def act(who, tid, command):
            reply = say(who, '/yeucau ' + tid)
            match = re.search(re.escape(command) + r' [0-9]{4}\b', reply)
            if not match:
                raise RuntimeError('Expected confirmation was not offered')
            return say(who, match[0])
        try:
            say('a', '/tickets')
            reply = say('a', '/doica')
            reply = choose('a', reply, 'CA-11')
            reply = choose('a', reply, 'coach-b')
            choose('a', reply, 'CA-21')
            reply = say('a', 'Xin đổi ca do có việc riêng — dữ liệu mô phỏng.')
            reply = say('a', re.search(r'/doica gui [A-F0-9]+', reply)[0])
            tid = re.search(r'DC-[A-F0-9]+', reply)[0]
            say('b', 'Yêu cầu chờ tôi')
            act('b', tid, '/dongy')
            say('manager', '/choduyet')
            act('manager', tid, '/duyet')
            say('a', '/lichsudoica')
            done = service.detail(store.account('demo-a'), tid)
            roster = service._source()
            if (done['status'] != 'completed' or roster['CA-11']['member_id'] != 'coach-b'
                    or roster['CA-21']['member_id'] != 'coach-a'):
                raise RuntimeError('Demo did not complete the exchange')
            print('ĐẠT: yêu cầu hoàn tất và đọc lại đúng cả hai ca mô phỏng.\n'
                  'Chưa gửi tin Zalo hoặc ghi Google Sheets thật. Dữ liệu demo tạm được xóa khi kết thúc.')
        finally:
            chat.close()
            provider.close()
            store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
