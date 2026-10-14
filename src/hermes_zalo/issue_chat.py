"""Member Issue commands; identity comes exclusively from approved DM bindings."""
from .commands import normalized
from .issue_management import IssueError, STATUS_LABELS, preview

GUIDE = ('ISSUE CỦA BẠN\n'
         'Báo lỗi/hỗ trợ: /issue tao Tiêu đề | Mô tả\n'
         'Đề xuất: /issue dexuat Tiêu đề | Mô tả\n'
         'Bot gửi bản nháp; bạn xác nhận mới tạo.\n'
         '/issue danhsach — yêu cầu của mình\n'
         '/issue xem ISS-000001 — xem trạng thái\n'
         '/issue bosung ISS-000001 | Nội dung — bổ sung thông tin')


def issue_id(value):
    value = value.strip()
    return value.upper() if value.upper().startswith('ISS-') else value


async def handle(work, message, argument):
    if message.chat_type != 'PRIVATE':
        return 'Nhắn riêng bot để gửi hoặc xem Issue của bạn.\n/issue — hướng dẫn'
    if not work.managed_issues_ready:
        return 'Quản lý Issue chưa sẵn sàng; nhờ người vận hành hoàn tất kiểm tra chuyển dữ liệu.'
    record = work.registration.approved(message)
    if record is None:
        return 'Cần đăng ký thành viên trước khi dùng Issue.\n/dangky SỐ_ĐIỆN_THOẠI'
    await work.registration.validate(record, require_unique_name=False)

    def allowed():
        return (work.managed_issues_ready and work.registration.approved(message) == record
                and work.member_feature_allowed(message.user_id, message.chat_id, message.chat_type))

    if not allowed():
        raise IssueError('Quyền thành viên đã thay đổi; kiểm tra /dangky trangthai.')
    action, _, body = argument.strip().partition(' ')
    action = normalized(action)
    store = work.managed_issues
    if action in ('tao', 'dexuat', 'gopy'):
        title, sep, description = body.partition('|')
        if not sep:
            return 'Gửi đủ nội dung trong một tin nhắn:\n/issue ' + ('dexuat' if action != 'tao' else 'tao') + ' Tiêu đề | Mô tả'
        draft = store.create(message, record.member_id, title, description,
                             'support' if action == 'tao' else 'feature', access_guard=allowed)
        if draft['status'] == 'created':
            return 'Đã tạo ' + draft['issue_id'] + '.\n/issue xem ' + draft['issue_id']
        return preview(draft)
    if action == 'xacnhan':
        issue = store.confirm(body.strip().upper(), message, record.member_id, access_guard=allowed)
        return f"Đã tạo {issue['id']}\n{issue['title']}\nTrạng thái: {STATUS_LABELS[issue['status']]}\n/issue xem {issue['id']} — theo dõi"
    if action == 'huy':
        cancelled = store.cancel(body.strip().upper(), message, record.member_id, access_guard=allowed)
        return 'Đã hủy bản nháp.' if cancelled else 'Không tìm thấy bản nháp đang chờ của bạn.'
    if action in ('danhsach', 'cuatoi'):
        raw = body.strip() or '1'
        if not raw.isascii() or not raw.isdigit():
            return 'Dùng /issue danhsach hoặc /issue danhsach 2 để chuyển trang.'
        result = store.member_list(record.member_id, page=int(raw), limit=5)
        if not result['items']:
            return 'Chưa có Issue ở trang này.\n/issue — hướng dẫn'
        lines = [f"ISSUE CỦA BẠN · Trang {result['page']}"]
        lines += [f"• {i['id']} · {STATUS_LABELS[i['status']]}\n  {i['title']}" for i in result['items']]
        lines.append('/issue xem MÃ — xem chi tiết')
        if result['page'] * result['limit'] < result['total']:
            lines.append('/issue danhsach ' + str(result['page'] + 1) + ' — trang sau')
        return '\n'.join(lines)
    if action in ('xem', 'trangthai'):
        issue = store.member_detail(issue_id(body), record.member_id)
        lines = [f"{issue['id']} · {STATUS_LABELS[issue['status']]}", issue['title'], issue['body']]
        if issue.get('comments'):
            lines.append('Phản hồi gần đây:')
            lines += ['• ' + c['body'] for c in issue['comments'][-3:]]
        lines.append('/issue bosung ' + issue['id'] + ' | Nội dung')
        return '\n'.join(lines)
    if action == 'bosung':
        identity, sep, content = body.partition('|')
        if not sep:
            return 'Gửi /issue bosung ISS-000001 | Thông tin cần bổ sung'
        store.member_reply(issue_id(identity), message, record.member_id, content, access_guard=allowed)
        return 'Đã gửi thông tin bổ sung vào Issue của bạn.\n/issue xem ' + issue_id(identity)
    return GUIDE
