# Mở rộng Agent Console bằng trang tính năng

Mỗi tính năng mới (Observe, Benchmarks, Integrations, System…) là một **trang tự chứa**: một module Python ở backend và một file JS/CSS ở frontend. Thêm trang mới không cần sửa router hay `console.js`.

## Backend

1. Tạo `src/hermes_zalo/admin_<tên>.py` và đăng ký trang ngay khi import:

```python
from .admin_auth import AdminError
from .console_sections import Section, register, validate_action

def snapshot(ctx, query):
    if ctx.demo:
        return {"section": "vi-du", "available": True, "items": [{"id": "a", "name": "Dữ liệu mẫu"}]}
    ...  # chỉ đọc trong ctx.home / ctx.assistant_dir
    return {"section": "vi-du", "available": True, "items": [...], "actions": [...]}

def action(ctx, data):
    action_id, revision, values = validate_action(data)
    ...  # kiểm tra revision, kiểm tra values, ghi thay đổi
    return {"ok": True, "message": "Đã lưu."}

register(Section("vi-du", snapshot, action, query_keys=frozenset({"days"})))
```

2. Thêm tên module vào `EXTENSION_MODULES` trong `src/hermes_zalo/console_sections.py`.

Router (`admin_web._extension_route`) đã lo các việc sau, module không phải làm lại:

- Đăng nhập, kiểm tra cùng nguồn (same-origin), CSRF và quyền **System owner**. Quyền được kiểm tra cả trước lẫn sau khi chạy.
- `GET /api/assistant/<tên>?…` chỉ nhận các tham số nằm trong `query_keys`, mỗi giá trị là chuỗi ≤ 200 ký tự.
- `POST /api/assistant/<tên>/action` ghi audit, chỉ gồm tên trang và mã thao tác, không bao giờ ghi giá trị.

Quy ước bắt buộc:

- Ở chế độ `ctx.demo`, trả dữ liệu giả lập: không đọc đĩa thật, không chạy tiến trình, không gọi mạng.
- Không trả về secret. Ô mật khẩu chỉ cho ghi, không bao giờ điền sẵn.
- Ghi vào `config.yaml`, `.env` hoặc cron qua các dịch vụ sẵn có (`ConnectionsAdmin`, `AutomationAdmin`, `ControlStore`) để chỉ có một nơi ghi.

## Frontend

1. Tạo `src/hermes_zalo/admin_static/console-<tên>.js` và `console-<tên>.css`.
2. Thêm cặp file này vào ba chỗ:
   - `CONSOLE_EXTENSIONS` trong `admin_web.py`;
   - danh sách trong `deploy/vercel-admin/scripts/build.js`;
   - thẻ `<link>`/`<script defer>` trong `console.html`.

```js
"use strict";
(() => {
  const { register } = window.HermesConsole;
  let days = "7";
  register("vi-du", {
    title: "Ví dụ", description: "Mô tả ngắn.", icon: "chart", group: "observe", // main|agent|observe|integrations|people|advanced
    essential: false,               // true: hiện trong nhóm "Essentials" thay vì khu "Advanced"
    actions: true,                  // dùng descriptor actions từ snapshot
    query: () => ({ days }),        // tham số GET
    commands: [                     // thao tác nhanh trong bảng lệnh Ctrl/Cmd+K (tối đa 8)
      { label: "Xem báo cáo mới nhất", keywords: "report latest", icon: "file", run: kit => kit.navigate("vi-du") },
    ],
    render(data, root, kit) {
      root.append(kit.note("Dữ liệu cập nhật mỗi giờ", "Giải thích dài nằm sau nút Details."));
      root.append(kit.panel("Tiêu đề", "Phụ đề"));
      // kit: node, button, iconButton, badge, empty, panel, metric, table, barChart, note, noteFrom,
      //      actionPlan, controlToolbar, poll, reload, navigate, openChat, openPalette…
    },
  });
})();
```

### Điều hướng (Essentials / Advanced)

- Thanh bên có hai khu: **Essentials** (dùng hằng ngày: Home, Chat, Reports, Automations, Connectors, Channels, Memory) và **Advanced** (thu gọn mặc định, trạng thái mở/đóng nhớ trong `localStorage`).
- Trang đăng ký với `essential: true` (hoặc `group: "main"`) vào Essentials, theo thứ tự cố định ở trên. Các `group` khác (`agent`, `integrations`, `observe`, `people`, `advanced`) nằm trong Advanced; giá trị lạ rơi về `agent`.
- Trang `briefing` (nếu đã đăng ký) trở thành Home: `#overview`, `#home` và trang mở mặc định đều dẫn tới nó. Tổng quan kỹ thuật cũ vẫn ở `#system-overview` (Advanced → Observe).
- Hash cũ vẫn chạy. Bí danh: `#automations`, `#schedules` → `#routines`.

### Thông báo gọn: `kit.note(title, text, tone)`

- Dùng thay cho đoạn văn cảnh báo dài ở đầu trang. Hiện một dòng `title`; `text` (tùy chọn) ẩn sau nút **Details** (`aria-expanded`/`aria-controls`).
- `tone`: `info` (mặc định), `warning`, `error` (role `alert`), `good`. Class: `.note.<tone>`, `.note-icon`, `.note-body`, `.note-line`, `.note-title`, `.note-toggle`, `.note-text`.
- `kit.noteFrom(message, tone)` tách câu đầu của thông điệp từ server làm tiêu đề, phần còn lại vào Details.

### Thời gian dễ đọc, toast và chuyển trang

- `kit.describeSchedule(cron)`: cron 5 trường hoặc khoảng của Hermes (`every 30m`) → câu tiếng Anh: `0 8 * * *` → "Every day at 08:00", `0 8 * * 1-5` → "Weekdays at 08:00", `*/15 * * * *` → "Every 15 minutes". Mẫu không hiểu được thì trả nguyên văn. Không hiển thị cron thô làm chữ chính.
- `kit.formatRelative(ts)`: nhận ISO, giây hoặc mili giây → "in 5 min", "2 h ago", "tomorrow"; quá 7 ngày thì hiện ngày; giá trị hỏng → "—".
- `kit.toast(text, tone)`: `tone` = `good` (mặc định), `info`, `warning`, `error` (role `alert`, hiện lâu hơn).
- Console tự thêm class `page-enter` vào `#content` ở lần render đầu sau khi đổi trang (hiệu ứng của console.css); poll và bộ lọc không chạy lại hiệu ứng.

### Tùy chỉnh thanh bên

- Người xem ghim/bỏ ghim bất kỳ trang nào vào Essentials bằng **Customize sidebar** (cuối thanh bên) hoặc bảng lệnh ("Pin … to the sidebar", "Reset sidebar to default"). Lưu riêng theo tài khoản trên trình duyệt (`localStorage` khóa `hermes-console-sidebar:<encoded username>`, có try/catch); Home luôn hiện. Đăng xuất xóa lựa chọn khỏi bộ nhớ trang; đăng nhập tài khoản khác không thừa hưởng các trang đã ghim.
- `essential: true` chỉ là mặc định. Trang bị bỏ khỏi Essentials quay về nhóm `group` của nó trong Advanced.

### Bảng lệnh (Ctrl/Cmd+K) và bộ lọc trang

- Ctrl/Cmd+K hoặc nút "Search or jump to…" trên topbar mở bảng lệnh: nhảy tới mọi trang được phép, chạy thao tác nhanh (New report, New automation, Open chat, Add memory…), các `commands` của trang, và các action toàn cục của trang đang mở. Gõ một câu bất kỳ → "Ask Hermes" mở Chat với tin nhắn điền sẵn (không tự gửi).
- `kit.openChat(text)` mở Chat với nội dung điền sẵn — dùng cho các gợi ý câu hỏi.
- Ô "Filter this page" nằm ở đầu trang, hiện trên mọi trang có danh sách để lọc (ẩn khi `searchable: false`); phím `/` đưa con trỏ vào ô này. Trong `render`, lọc bản ghi bằng `kit.matches(item)`.

Nguyên tắc:

- Mọi dữ liệu từ server đều coi là không tin cậy. Chỉ dựng DOM bằng `kit.node` hoặc `textContent`, không dùng `innerHTML`.
- CSP chặn thuộc tính `style` viết trong HTML. Dùng class, hoặc gán qua CSSOM (`el.style.width = …`).
- Màu lấy từ biến trong `console.css` (`--surface`, `--accent`, `--good`…), nhờ vậy tự hỗ trợ cả giao diện sáng lẫn tối.
- Thao tác có ghi dữ liệu dùng *descriptor action* (`{id, label, revision, target?, fields}`). Console tự mở hộp thoại, bắt xác nhận, gửi CSRF, không tự thử lại khi lỗi, và khóa thao tác tiếp theo cho tới khi tải lại trang.
- Trang cần tự cập nhật (tiến trình, trạng thái) thì gọi `kit.poll(ms)` trong `render`. Bộ hẹn giờ tự dừng khi chuyển trang.

## Kiểm thử

- Python: `PYTHONPATH=src:. .runtime/venv/bin/python -m unittest tests.test_console_sections tests.test_admin_<tên>`
- JS: `cd deploy/vercel-admin && node --test tests/*.test.js`. Xem `tests/console-*.test.js` để biết cách chạy `console.js` cùng file mở rộng trong `vm`.
- Demo: `PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port 8799`, đăng nhập `admin` / `LabCoach-Demo-2026`.

## Briefing: ô KPI trực tiếp (live KPI tiles)

### Mở đúng báo cáo từ Home

`console-integrations.js` cung cấp `window.HermesReports.open(job, file)` với job ID và
tên output đã kiểm tra (ví dụ `open("abc123", "2026-11-01_08-00-00.md")`). Hàm giữ tham
chiếu trước khi chuyển trang Reports rồi tải `GET /api/assistant/reports?job=…&file=…`.
Briefing truyền `item.report = {job, file}` cho hàm này, vì vậy **Read report** không
tự mở báo cáo mới nhất thay cho báo cáo đã chọn. Giá trị dạng đường dẫn bị từ chối.
Đây là tham chiếu trong phiên trang, không phải URL chia sẻ lưu qua lần reload.

Reports cũng cung cấp `HermesReports.create()` để mở trình chọn mẫu. Palette có
**New report**, **New report from template** và **Open latest report**; mở trình chọn
không tự tạo lịch. **New automation** tải snapshot Automations rồi mở descriptor
`create_routine`, kể cả demo. Thao tác ghi vẫn cần xác nhận.

### Đích nhận báo cáo

AutomationAdmin là nơi duy nhất kiểm tra `deliver`. Giao diện chỉ gửi lựa chọn kín
`local`, `telegram`, `zalo`, hoặc `origin` khi job đã có origin thuộc home chat được
cấu hình. Server giải lựa chọn Telegram/Zalo thành `platform:<configured chat>`
trước khi lưu lịch; không trả chat ID, origin metadata hay token về trình duyệt.
Không nhận đích chat tự nhập. Home chat lấy từ `.env` của home/process được chọn
hoặc block platform hiệu lực trong `config.yaml`; cần có token tương ứng.
Đích bị gỡ khỏi cấu hình không được chọn hay dùng Run now. Job cũ có đích khác chỉ
cho giữ nguyên qua lựa chọn **Keep current delivery**, không cho nhân bản đích đó.
Reports truyền trường `delivery` tới cùng bộ kiểm tra, và demo chỉ dùng đích giả lập.

### Dữ liệu KPI

Trang **Briefing** (`admin_briefing.py`) không gọi mạng. Số liệu nền tảng (Facebook, Zalo OA, Telegram, X…) ở chế độ thật
được đọc từ một file do chính report job của bạn ghi: `<HERMES_HOME>/console/kpis.json`. Mẫu báo cáo **KPI snapshot**
trên trang Reports hướng dẫn agent ghi file này sau mỗi lần chạy. Bạn cũng có thể tự viết job khác ghi cùng định dạng.

```json
{
  "period": "Last 7 days",
  "items": [
    {"id": "fb-reach", "platform": "facebook", "label": "Facebook page reach",
     "value": 18420, "previous": 16390, "unit": "", "good_when": "up",
     "series": [2100, 2300, 2250, 2600, 2700, 2650, 2720]}
  ]
}
```

- `platform`: `facebook`, `zalo`, `telegram`, `x`, `instagram`, `tiktok`, `youtube`, `work` hoặc `custom` (quyết định biểu tượng).
- `value` bắt buộc và phải là số. `previous` (hoặc `change`, dạng tỉ lệ, ví dụ `0.12` = +12%) dùng để tính mức thay đổi.
- `series`: tối đa 30 số, cũ trước mới sau, dùng để vẽ sparkline. `good_when`: `up`, `down` hoặc `neutral` (chọn màu xanh/đỏ).
- Tối đa 8 ô. Mục sai định dạng bị bỏ qua, file hỏng thì trang ghi chú "could not be read" và vẫn hiện các khối khác.
- Thiếu số liệu thì bỏ hẳn chỉ số đó, không đoán. Briefing tự thêm các ô sẵn có trong máy: Assistant requests, AI turns today,
  Healthy automations, Reports saved, Channels online.

Các mục "Needs your attention" có thể ẩn bằng **Dismiss** / **Dismiss all**. Trạng thái lưu ở `console/briefing.json` qua
`ControlStore` (có kiểm tra revision). Mã mục gồm cả lần xảy ra, nên một lỗi mới vẫn hiện lại sau khi lỗi cũ đã bị ẩn.
