"""Synthetic Issue fixtures for the offline Admin demo, never production data."""
from .events import IncomingMessage


def seed_issues(work, members):
    if work.settings.token != 'admin-demo-synthetic':
        raise ValueError('Dữ liệu mẫu chỉ được tạo trong Admin demo ngoại tuyến.')
    if len(members) != 4:
        raise ValueError('Admin demo cần đúng bốn thành viên mẫu.')
    store = work.managed_issues
    if store is None:
        raise ValueError('Admin demo cần bật chế độ Issue quản lý.')
    store.mark_source_reconciled('synthetic/no-legacy-issues', [])
    faculty = dict(username='admin', role='faculty', permissions=['issues'], active=True)
    support = dict(username='hotro', role='admin', permissions=['issues'], active=True)
    samples = (
        ('support', 'Máy chiếu phòng học mẫu chưa hoạt động',
         'Mình cần hỗ trợ kiểm tra máy chiếu trước buổi học. Đây là nội dung mô phỏng.'),
        ('feature', 'Nhắc lịch trước ca trực 30 phút',
         'Mình đề xuất bot gửi tin nhắc trước ca để các bạn chủ động chuẩn bị.'),
        ('support', 'Không thấy lịch cá nhân trên bot',
         'Mình đã đăng ký nhưng khi hỏi lịch tuần sau chưa thấy kết quả.'),
        ('support', 'Hướng dẫn gửi yêu cầu đổi ca',
         'Mình cần hướng dẫn các bước chọn ca và xác nhận đổi ca.'),
    )
    for index, (member, (kind, title, body)) in enumerate(zip(members, samples), 1):
        message = IncomingMessage('demo-private-' + str(index), 'demo-user-' + str(index),
                                  'demo-issue-' + str(index), 'PRIVATE', '/issue ' + title)
        draft = store.create(message, member.member_id, title, body, kind, access_guard=lambda: True)
        issue = store.confirm(draft['id'], message, member.member_id, access_guard=lambda: True)
        if index == 1:
            continue
        actor = faculty if index == 4 else support
        current = store.detail(issue['id'], actor)['issue']
        if current['revision'] != 1:
            continue
        current = store.act(issue['id'], actor, 'claim', current['revision'])['issue']
        if index == 2:
            current = store.act(issue['id'], actor, 'plan', current['revision'],
                                priority='normal', estimate='2 ngày', due_date='2026-11-02')['issue']
            store.act(issue['id'], actor, 'comment', current['revision'], visibility='internal',
                      text='Cần kiểm tra múi giờ và tránh gửi nhắc trùng. Ghi chú này chỉ dành cho đội hỗ trợ.')
        elif index == 3:
            store.act(issue['id'], actor, 'status', current['revision'], status='waiting',
                      text='Bạn cho mình ngày và ca đang cần xem nhé, không cần gửi số điện thoại.')
        else:
            current = store.act(issue['id'], actor, 'comment', current['revision'], visibility='internal',
                                text='Đã đối chiếu luồng mẫu; không thay đổi lịch thật.')['issue']
            store.act(issue['id'], actor, 'status', current['revision'], status='done',
                      text='Bạn nhắn /doica trong tin nhắn riêng, chọn ca rồi làm theo hướng dẫn xác nhận.')
    # Simulate delivery results locally; no Zalo client is created or called.
    count = 0
    while notice := store.claim_notification(lambda n: n['user_id'].startswith('demo-user-')
                                             and n['chat_id'].startswith('demo-private-')):
        count += 1
        store.finish_notification(notice['id'], 'failed' if count == 2 else 'sent',
                                  'network' if count == 2 else '')
