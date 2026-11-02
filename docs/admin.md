# Hermes Agent Console

The main console manages the personal assistant in English. Existing login,
cookies, CSRF checks and permissions remain in use. The English Lab interface
is available at `/legacy.html` for the existing member and business workflows.

> **Running it for real (not the demo)?** Follow [production.md](production.md):
> one-command start with `scripts/run.sh Console`, the owner account, HTTPS
> reverse proxy, Docker, backups and updates.

## Management pages and connected actions

Navigation is split into a short daily set and an **Advanced** area (details
below). Pages read the selected bot home and show supported management
actions. Missing or unsupported data is labeled unavailable. Every change needs
owner access, confirmation and the revision returned by its snapshot. A conflict
or uncertain network result requires refreshing; writes are never retried.

The Hostinger-inspired implementation is split into three worker areas:
connections/settings, automation/skills, and interface. The integrating agent owns
persistence, HTTP authorization, proxy routes and end-to-end verification.

The sidebar shows **Essentials** for daily use: Home (the briefing), Chat,
Reports, Automations, Connectors, Channels and Memory. Everything else sits in a
collapsible **Advanced** area: Agent (Models, Skills, Tools (MCP)), Integrations,
Observe (System overview, Conversations, Agent activity, Analytics, Experiments,
Benchmarks), People & usage (Users, Usage & cost) and System (API keys, Settings,
Logs, Files, Plugins, Webhooks, Admin accounts, Help, System). **Customize
sidebar** (bottom of the sidebar, or the command palette) pins any page into
Essentials or removes one; the choice is saved in that browser only and can be
reset. **Ctrl/Cmd+K** opens the command palette to jump to a page or run a quick
action (New report, New automation, Open chat…); `/` focuses the page filter.
The theme follows the system light/dark setting and can be switched from the
top bar.

Feature pages are self-contained extensions (guide: [console-extensions.md](console-extensions.md)):

| Group | Page | Guide |
| --- | --- | --- |
| Observe | Agent activity (step-by-step turn traces), Analytics (latency, errors, tokens, cost) | [observe.md](observe.md) |
| Observe | Experiments (compare runs and settings snapshots), Benchmarks (task suites, runs, comparison) | [benchmark.md](benchmark.md) |
| Integrations | Connectors (MCP catalog incl. social platforms and Zalo OA), Reports (scheduled report jobs) | [integrations.md](integrations.md) |
| More tools | System (gateway status/control, resources, health checks, backups, updates) | [operations.md](operations.md) |

Connector "Test connection", benchmark runs and gateway control are explicit, owner-confirmed
exceptions to the read-only defaults above; gateway control stays off unless
`HERMES_CONSOLE_GATEWAY_CONTROL` is set.

| Tab | Implemented controls | When changes apply |
| --- | --- | --- |
| Models | Change main model/provider and optional endpoint; assign nine auxiliary task slots | Next gateway start/new sessions |
| Config | Edit reasoning, tool-call budget, timeouts, compression, time zone and approval mode | Next gateway start |
| Keys | Add, replace or remove twenty known provider/tool/channel credentials in the bot home `.env`; presence and source only | Next gateway start |
| Channels | Telegram/Discord/Slack automatic/on/off mode, reply threading and typing indicator; configure tokens in Keys | Next gateway start |
| MCP | Add an HTTP server, toggle or remove it; optional token stored by reference in `.env` | Next gateway start |
| Skills | Search/filter by category, enable/disable a skill or category, create a local `SKILL.md` | New sessions |
| Automations | Create/edit/pause/resume/delete jobs, inspect runs and redacted output excerpts; deliver locally or to a configured Telegram/Zalo home chat; request a run when a scheduler heartbeat is fresh | Running Hermes scheduler's next tick |
| Plugins | Toggle an installed optional plugin; policy/authentication plugins are protected | Next gateway start |
| Webhooks | Create a paused signed subscription with local-log delivery, enable/pause/remove a route | Configured webhook gateway must be running |
| Logs | Filter by file, component, severity, text and 50/100/200/500 lines | On Apply/Refresh |

Memory/sandbox/toolset settings that enforce shared-bot isolation are displayed
with an explanation and are not editable through generic Config. MCP enabling
is rejected when chat platforms would receive its tools under their current
toolset configuration; a server can still be saved disabled. Local stdio MCP
processes, OAuth login, skill-hub installation, connection probes, gateway restart
and updates remain operator workflows; the Console does not display working
controls for unimplemented runtime integrations. Pairing approval, Hermes
profiles and task boards are not part of the Console; use the Hermes CLI for them.

Credential values are never returned, prefilled or recorded in audit events.
An empty optional password field preserves the existing secret. Keys removal
only removes the home `.env` entry; a value supplied by the process environment
may remain effective. Zalo credentials stay in this repository's existing setup.
YAML edits preserve unrelated settings but rewrite YAML formatting/comments.

Cron accepts five-field cron expressions, intervals such as `every 2h`, supported
weekday/time phrases, or an ISO one-shot time. New jobs save locally and cannot
send to another person from this form. A missing next-run timestamp is explicitly
pending until Hermes computes it. Deleting a job also deletes its saved output.
Skill creation accepts up to 7,000 characters of Markdown instructions; it does
not download or execute third-party code.

- **Files** browses `HERMES_CONSOLE_WORKSPACE`, defaulting to `<home>/workspace`.
  It lists up to 200 entries and previews supported text files up to 32 KiB and
  200 lines. Hidden files, credential names, symlinks, databases and key files
  are excluded. There are no upload, edit, delete or shell operations.
- **Chat** uses an existing Hermes API configured on the server. Set
  `HERMES_CONSOLE_CHAT_API_URL` to its origin, `/v1`, or `/v1/chat/completions`,
  and `HERMES_CONSOLE_CHAT_API_KEY` to its key (`API_SERVER_KEY` is a fallback).
  `HERMES_CONSOLE_CHAT_MODEL` defaults to `hermes`. HTTPS is required except
  for loopback HTTP. The browser cannot choose an endpoint or supply a key.
  Configuration status does not prove gateway health; the first message makes
  the connection. Requests have a 30-second timeout and are never retried
  automatically. Chat history stays in the current browser page and is cleared
  on logout; the connected gateway may retain its own history.

These management pages require the `faculty` role, displayed as **System owner**. Content
permissions for Sessions and Memory remain separate. Every write also requires
the existing same-origin session and CSRF token. Demo mode uses synthetic files,
an in-memory configuration and an explicitly labeled sample chat reply, without contacting
the real workspace or a model. Standalone mode can use management controls, Files and an
explicitly configured Chat connection; it does not start the bot or API server.

## Trang quản lý mới

| Mục | Dữ liệu và thao tác hiện có |
| --- | --- |
| Tổng quan | Số phiên Hermes, các lần gọi model, token, hoạt động và khả năng có nguồn dữ liệu |
| Người dùng trợ lý | Allowlist tường minh theo kênh và hạn mức hôm nay; chỉ đọc, không thay thế kiểm tra quyền nhóm/đăng ký của gateway |
| Hội thoại | Xem danh sách và tin nhắn đã lưu; phân trang, tìm trong trang hiện tại. Chế độ độc lập đọc runtime Hermes; chế độ gắn bot dùng kho hội thoại quản lý cũ |
| Bộ nhớ | Trong chế độ gắn bot: xem, thêm, sửa, ngừng dùng ghi nhớ quản lý, kiểm tra revision. **Kho này chưa được tiêm vào vòng lặp assistant** |
| Kỹ năng | Tìm/lọc, bật/tắt skill hoặc nhóm và tạo skill cục bộ; áp dụng cho phiên mới |
| Lịch tự động | Tạo/sửa/tạm dừng/tiếp tục/xóa lịch; xem lịch sử và trích đoạn kết quả. Lịch mới lưu kết quả cục bộ |
| Sử dụng & chi phí | Các API attempt từ assistant-core, gồm retry; token, thời gian và lỗi. Chi phí lấy từ mọi phiên Hermes trong cùng home, có nhãn thực tế/ước tính; thiếu giá thì không suy đoán |
| Máy chủ & sandbox | RAM, ổ đĩa, tải hệ thống, commit runtime và backend được cấu hình. Không cung cấp terminal hoặc nút restart |
| Quyền truy cập | Danh sách tài khoản quản trị; dùng quy trình hiện có tại trang quản trị dữ liệu cũ để sửa tài khoản |

Vai trò `faculty` được giao diện mới gọi là **Chủ hệ thống**, được đọc metadata
vận hành. Quyền `conversations` vẫn bắt buộc để xem nội dung hội thoại; xem/sửa
ghi nhớ quản lý cần cả `conversations` và `memories`. Vai trò cao không tự cấp
các quyền nội dung này.

Các snapshot runtime chỉ đọc, giới hạn số dòng và kiểm tra schema. Dữ liệu chưa
có hiện trạng thái chưa kết nối hoặc dấu `—`, không biến thành số 0. Tìm kiếm
trên giao diện lọc những bản ghi của trang đang mở, không phải tìm toàn bộ kho.
Lượt xem nội dung phiên runtime được ghi vào nhật ký tài khoản (mã phiên,
người xem và thời gian; không sao chép nội dung hội thoại vào nhật ký).

Tài khoản `admin` thông thường chỉ thấy các trang nội dung đã được cấp quyền.
Chủ hệ thống dùng role `faculty`; khi dùng Admin gắn bot, việc chỉ định role này
thực hiện tường minh bằng lệnh `designate-faculty --username TEN` của công cụ
quản trị. Console tự chọn trang đầu tiên mà tài khoản được phép mở.

## Chạy thật (production)

Toàn bộ hướng dẫn vận hành thật nằm ở [production.md](production.md): chạy bằng
một lệnh `scripts/run.sh Console`, tạo tài khoản chủ hệ thống, HTTPS qua reverse
proxy, Docker, sao lưu, cập nhật và kiểm tra bảo mật. Demo bên dưới chỉ để xem
giao diện.

## Xem demo trên Linux

```bash
PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port 8790
```

Mở `http://127.0.0.1:8790`, đăng nhập `admin` / `LabCoach-Demo-2026`. Đây là
tài khoản dùng cho dữ liệu mô phỏng tạm thời, không dùng cho triển khai thật.
Demo không gọi Zalo, Telegram, Google hay model. Dừng bằng Ctrl+C.

## Chạy độc lập trên máy hoặc VPS, kể cả Telegram-only

`--home` là **thư mục dữ liệu** của bot (`HERMES_HOME`), không phải thư mục mã
Hermes (`HERMES_SRC`). Mặc định là `.runtime/hermes-home` của repo, không tự
chuyển sang home Hermes cá nhân. Để sử dụng home khác, truyền cùng `--home`
cho cả hai lệnh:

```bash
PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli create-console-account \
  --home .runtime/hermes-home --username owner --permissions conversations

PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli serve \
  --home .runtime/hermes-home --port 8790
```

Mật khẩu nhập ẩn tại terminal. Tài khoản console độc lập lưu trong
`<home>/console-admin.sqlite3`, tách với tài khoản Admin gắn bot. Có thể truyền
`--auth-db` để chọn cơ sở tài khoản hiện có một cách tường minh. Không cần token
Zalo để tạo tài khoản hoặc chạy console độc lập; lệnh `serve` không khởi động
gateway, không nhận/gửi tin và không tự gọi model. Chat chỉ gọi API đã cấu hình
khi chủ hệ thống chủ động gửi tin trong trang Chat.

Chế độ độc lập đọc lịch sử runtime, có Files/Chat như mô tả ở trên,
không bật kho ghi nhớ hoặc nghiệp vụ Lab.
Nó chỉ trả tin nhắn user/assistant, ẩn system prompt, reasoning, tool call/result
và payload ảnh. Hermes có thể lưu lịch sử riêng; console không thay thế cơ chế
đồng ý lưu và thời hạn giữ dữ liệu của gateway.

Mặc định chỉ lắng nghe loopback. Trên VPS có thể truy cập bằng SSH tunnel:

```bash
ssh -N -L 8790:127.0.0.1:8790 user@vps
```

Nếu triển khai sau HTTPS proxy, dùng cơ chế `ZALO_ADMIN_HOST`,
`ZALO_ADMIN_PUBLIC_URL`, `ZALO_ADMIN_PROXY_SECRET` hiện có ở phần hướng dẫn bên
dưới. Không chạy console độc lập và Admin gắn bot cùng một cổng.

## Frontend Vercel và nguồn tham khảo

`deploy/vercel-admin` đóng gói 6 asset: console mới ở `/`, giao diện cũ ở
`/legacy.html` và hai cặp CSS/JS. Proxy đã có allowlist cho các endpoint mới;
auth/Origin/CSRF vẫn do backend kiểm tra. Chỉ build local chưa cập nhật hệ thống
đang triển khai. Cần phát hành frontend và backend tương thích cùng nhau.

Các ý tưởng được tổng hợp, không chép hay cài mã nguồn dashboard bên ngoài:

- [Hostinger Hermes VPS setup](https://www.hostinger.com/support/how-to-get-started-with-hermes-agent-on-hostinger-vps/): deployment context and operational workflow. The supplied screenshots inform model, cron, skills, keys and channel controls; the implementation keeps this repository's English interface and authorization model.

- [Hermes dashboard chính thức](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md): tổ chức theo phiên, kỹ năng, cron, analytics và hệ thống.
- [Hermes WebUI](https://github.com/nesquena/hermes-webui): duyệt phiên và thiết kế cho trình duyệt/điện thoại. Có ràng buộc phiên bản với Hermes; repo này không nạp thêm một runtime WebUI.
- [Hermes Dashboard](https://github.com/Bichev/hermes-dashboard): góc nhìn token và chi phí.
- [Cockpit](https://cockpit-project.org/): tổng quan tài nguyên VPS. Các thao tác quản trị hệ điều hành vẫn dành cho công cụ vận hành riêng.

Các liên kết nhánh `main` mô tả phiên bản hiện hành của dự án tham khảo, có thể
khác commit Hermes đang vận hành. Riêng Bichev chỉ tham khảo ý tưởng analytics;
chưa xác nhận giấy phép để tái sử dụng mã nguồn của dự án đó.

Xem thêm [nghiên cứu cloud của agent](nghien-cuu-quan-ly-cloud.md). Hướng tiếp
theo là kho nhớ theo người, duyệt hành động và cron có quyền chủ sở hữu; những
chức năng này chưa được tuyên bố hoàn tất bởi trang quản lý.

---

# Tài liệu quản trị Lab cũ

Các thao tác nghiệp vụ bên dưới thực hiện ở `/legacy.html`, không phải trang console chính.


Trang riêng cho người quản trị; thành viên vẫn dùng Zalo như hiện tại. Giao diện chạy trên [Vercel](https://hermes-zalo-bot-vercel-admin.vercel.app/legacy.html), API và dữ liệu chạy trên Railway. Cấu hình mẫu/local vẫn mặc định tắt Admin và lưu hội thoại.

**Duyệt đổi ca:** A gửi → B đồng ý → Admin duyệt trên trang này → cập nhật Sheets. Cấp riêng quyền `tickets` để thấy mục **Duyệt đổi ca**; xem [hướng dẫn thành viên và Admin](duyet-doi-ca.md).

Kết nối Vercel–Railway yêu cầu khóa máy chủ, đăng nhập và đúng quyền. Cookie có Secure, HttpOnly, SameSite=Strict; thao tác thay đổi cần CSRF. Model hiện tại dùng [LiteLLM / DeepSeek Flash](model-litellm.md).

API: `https://hermes-zalo-bot-production.up.railway.app`, Target Port 8787. Mở trực tiếp trên trình duyệt sẽ bị từ chối vì đây là backend có khóa kết nối, không phải địa chỉ giao diện Vercel. Mật khẩu Admin và khóa proxy chỉ lưu trong tệp riêng dưới `.runtime/admin-deploy-20261013/`, không đưa lên Git hoặc chat.

## 1. Thử giao diện ngay bằng dữ liệu mẫu

Tại thư mục repo, chạy:

```powershell
.\scripts\run.ps1 AdminDemo
```

Mở [http://127.0.0.1:8787/legacy.html](http://127.0.0.1:8787/legacy.html). Nếu cổng đang được dùng, dừng bản demo trước hoặc chạy `.\.venv\Scripts\python.exe -m hermes_zalo.admin_cli demo --port 8788`.

| Tài khoản demo | Quyền |
| --- | --- |
| `admin` | Giảng viên demo, đủ quyền quản trị và duyệt đổi ca |
| `quanly` | Chỉ quản lý thành viên |
| `hoithoai` | Hội thoại, ngữ cảnh và ghi nhớ |

Mật khẩu chung của **demo**: `LabCoach-Demo-2026`. Không dùng mật khẩu này cho hệ thống thật. Demo tạo dữ liệu giả trong thư mục tạm, không đọc dữ liệu bot hiện tại, không polling Zalo, không gọi Google Sheets hoặc model. Nhấn **Ctrl+C** để dừng; dữ liệu mẫu không giữ cho lần chạy sau. Vì không kết nối lịch thật, phần sửa liên kết lịch trong demo có thể báo chưa có nguồn để đối chiếu.

## 2. Bật trên máy đang vận hành bot

**Nếu Railway đang chạy bot, giữ bản Windows tắt.** Có thể chạy `AdminDemo` trên Windows để xem giao diện; chỉ thực hiện các bước chạy bot thật dưới đây trên nơi được chọn vận hành duy nhất.

1. Giữ nguyên `.env`, khóa và dữ liệu hiện có. Thêm các biến sau vào `.env` trên máy:

   ```dotenv
   ZALO_ADMIN_ENABLED=true
   ZALO_CONVERSATIONS_ENABLED=true
   ZALO_CONVERSATION_RETENTION_DAYS=30
   ZALO_ADMIN_HOST=127.0.0.1
   ZALO_ADMIN_PORT=8787
   ZALO_ADMIN_PUBLIC_URL=
   ```

2. Cập nhật plugin và tạo tài khoản quản trị riêng:

   ```powershell
   .\scripts\run.ps1 Update
   .\scripts\run.ps1 AdminCreate
   ```

   Nhập tên đăng nhập, mật khẩu và xác nhận mật khẩu tại terminal. Mật khẩu được nhập ẩn, cần ít nhất 12 ký tự. Không gửi mật khẩu, Bot Token hay API key vào chat. Kiểm tra quyền được cấp; chỉ định Giảng viên theo [hướng dẫn phân quyền](ISSUES.md).

3. Khi đã dừng phiên bot cũ tại nơi vận hành, khởi động lại:

   ```powershell
   .\scripts\run.ps1 Start
   ```

4. Đăng nhập [http://127.0.0.1:8787/legacy.html](http://127.0.0.1:8787/legacy.html). Trang Admin mở trong cùng tiến trình bot sau khi bot kết nối thành công; dừng bot thì trang cũng dừng. Địa chỉ này chỉ truy cập được trên máy đang chạy.

Tắt trang bằng `ZALO_ADMIN_ENABLED=false`. Muốn dừng cả lưu hội thoại và sử dụng ghi nhớ được quản lý, đặt thêm `ZALO_CONVERSATIONS_ENABLED=false` rồi khởi động lại. Việc tắt cờ không tự xóa dữ liệu đã lưu.

## 3. Tài khoản và quyền quản trị

Tài khoản web tách khỏi đăng ký Zalo. Thành viên đã đăng ký, quản trị nhóm Zalo hoặc người trong `REGISTRATION_ADMIN_USERS` không tự có quyền đăng nhập trang.

| Quyền | Được làm |
| --- | --- |
| `members` | Xem thành viên, khóa/mở quyền, sửa đăng ký nhầm và liên kết được phép |
| `tickets` | Xem yêu cầu đổi ca, duyệt/từ chối và đối soát kết quả chưa xác định |
| `issues` | Xem và xử lý issue trong phạm vi được cấp |
| `conversations` | Xem hội thoại riêng/nhóm bot đã lưu, xem và thay đổi ngữ cảnh; nội dung ghi nhớ được ẩn nếu thiếu quyền `memories` |
| `memories` cùng `conversations` | Xem, thêm, sửa, ngừng dùng ghi nhớ và xem nguồn/lịch sử nội dung |

Trên Windows, có thể tạo tài khoản giới hạn quyền bằng môi trường Python của repo:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.admin_cli create-account --username quanly --permissions members
.\.venv\Scripts\python.exe -m hermes_zalo.admin_cli create-account --username hoithoai --permissions conversations,memories
```

Chỉ cấp các quyền cần thiết. Để bổ sung quyền mà giữ mật khẩu và quyền cũ, dùng `grant-permissions --username TEN --permissions tickets`. Muốn đổi mật khẩu/quyền của tài khoản đã có, chạy lại lệnh tạo tương ứng kèm `--replace`; thao tác yêu cầu nhập mật khẩu mới và thu hồi các phiên đăng nhập cũ. Không đặt mật khẩu vào tham số dòng lệnh. Đăng nhập sai nhiều lần sẽ bị giới hạn tạm thời.

**Tự đổi mật khẩu:** đăng nhập → **Đổi mật khẩu** ở góc trên bên phải → nhập mật khẩu hiện tại và mật khẩu mới hai lần (12–256 ký tự). Thành công sẽ đăng xuất mọi phiên của chính tài khoản; đăng nhập lại bằng mật khẩu mới. Giới hạn 5 lần thử trong 10 phút. Nếu quên mật khẩu, nhờ Giảng viên hoặc người vận hành đặt lại bằng cơ chế hiện có. Mỗi người dùng tài khoản riêng, không chia sẻ mật khẩu.

## 4. Quản lý Thành viên

1. **Tìm kiếm/lọc:** dùng tên hoặc mã thành viên; lọc trạng thái đăng ký, bị khóa và tình trạng liên kết.
2. **Xem chi tiết:** kiểm tra mã thành viên, đăng ký, tài khoản Zalo, chat riêng, nguồn Workload và liên kết Tickets/lịch. Chat riêng và ID nhóm là hai phạm vi khác nhau.
3. **Khóa/mở quyền:** đọc phạm vi tác động rồi xác nhận. Khóa giữ dữ liệu, chặn dùng bot và không thể vượt bằng đăng ký lại. Mở khóa không duyệt lại đăng ký đã bị thu hồi, không cấp quản trị hoặc quyền nhóm mới; hệ thống đối chiếu lại Workload trước khi mở.
4. **Sửa liên kết:** chọn phương án đã được hệ thống đối chiếu → xem trước → xác nhận. Mỗi bước kiểm tra lại dữ liệu; không sửa ô lịch thật.
5. **Sửa đăng ký nhầm:** mở chi tiết hồ sơ → **Sửa đăng ký nhầm** → nhập số điện thoại đúng **hiện có trên Workload** và lý do → **Xem trước thay đổi** → đối chiếu tên, tài khoản Zalo, chat riêng → xác nhận đã kiểm tra đúng chủ tài khoản → **Xác nhận sửa đăng ký**. Thành viên không cần đăng ký lại; dùng `/dangky trangthai` để kiểm tra tên mới, rồi `/lichcuatoi` hoặc `/doica`.
6. **Xóa đăng ký để thành viên tự đăng ký lại:** mở đúng hồ sơ → **Xóa đăng ký** → kiểm tra tên/số điện thoại đã che → nhập lý do → tích xác nhận → **Xác nhận xóa đăng ký**. Thành viên nhắn riêng bot `/dangky SỐ_ĐIỆN_THOẠI` với số hiện có trên Workload. Đăng ký lại vẫn được kiểm tra duy nhất và tự duyệt theo cấu hình hiện tại; không cần thêm ID vào `.env`.

Xóa đăng ký thu hồi quyền dùng bot, gỡ liên kết chat riêng/Tickets, làm mới ngữ cảnh và ngừng dùng ghi nhớ cá nhân cũ. Không xóa Workload, lịch, lịch sử hội thoại hoặc issue. Hồ sơ mang trạng thái **Đã xóa đăng ký**, được ẩn khỏi danh sách mặc định; chọn bộ lọc cùng tên để xem lại. Thao tác cần quyền `members`, lý do và xác nhận; hồ sơ bị khóa, liên kết xung đột hoặc còn đổi ca chưa kết thúc phải xử lý trước. Nếu mất kết nối, **tải lại hồ sơ kiểm tra kết quả**, không bấm gửi lại liên tiếp. Muốn chặn sử dụng lâu dài hãy dùng **Khóa quyền**, thay vì xóa đăng ký.

Sửa liên kết hiện hỗ trợ lịch Lab đã cấu hình qua Tickets. Tên trùng, nguồn thay đổi, tài khoản đích thuộc người khác hoặc còn yêu cầu đổi ca liên quan sẽ bị chặn; cần xác minh đúng người trước. Trang không cho nhập tùy ý user/chat ID để ghi đè một người khác. **Lịch sử thay đổi** cho biết ai thực hiện, khi nào và thông tin trước/sau.

Sửa đăng ký nhầm thu hồi đăng ký cũ và duyệt hồ sơ đúng cho **cùng tài khoản Zalo/chat riêng**. Mã thành viên cũ được giữ; người đúng dùng mã cố định đã có hoặc được cấp mã mới. Lịch sử, issue và ghi nhớ cá nhân cũ không chuyển sang người mới; ngữ cảnh cũ được làm mới, nhắc việc thủ công cũ được ngừng dùng. Liên kết Tickets được kiểm tra và tạo lại khi thành viên dùng bot. Không sửa ô lịch, không cấp thêm quyền quản trị hoặc quyền nhóm.

Hồ sơ đã thu hồi/từ chối, tài khoản bị khóa, số hoặc tên không khớp duy nhất, đích đã có người đăng ký và yêu cầu đổi ca liên quan chưa kết thúc đều bị chặn. Cần xử lý đúng nguyên nhân; nút sửa không ghi đè tài khoản người khác. Nếu thao tác lỗi hoặc mất kết nối, **tải lại hồ sơ để kiểm tra kết quả trước khi thử lại**. Xem thêm [hướng dẫn đăng ký](dang-ky.md#xử-lý-khi-đăng-ký-hoặc-liên-kết-bị-nhầm).

Đăng ký lần đầu vẫn **tự duyệt** khi số điện thoại khớp duy nhất Workload và không xung đột. Việc tự duyệt không chứng minh quyền sở hữu số điện thoại; Admin cần xác minh người thực tế trước khi sửa danh tính.

## 5. Hội thoại & Ghi nhớ

**Chính sách hiện tại:** bot chỉ dùng AI để phân loại câu hỏi công việc. Luồng này không gửi lịch sử, tóm tắt hoặc ghi nhớ cho model; các dữ liệu cũ vẫn được giữ để Admin kiểm tra/quản lý. Tin mới ngoài phạm vi vẫn có thể xuất hiện trong lịch sử theo thông báo lưu tin, nhưng không được dùng để xây ngữ cảnh hoặc tự tạo ghi nhớ. Các mục ngữ cảnh/ghi nhớ dưới đây không đồng nghĩa nội dung đang được gửi tới AI.

| Mục | Dùng để làm gì? | Giới hạn cần biết |
| --- | --- | --- |
| **Lịch sử hội thoại** | Tìm theo thành viên, nhóm, ngày; xem tin nhận và phản hồi, thời gian, trạng thái gửi | Chỉ tin bot thực nhận và được phép xử lý từ lúc bật tính năng; không lấy toàn bộ lịch sử Zalo |
| **Ngữ cảnh cuộc trao đổi** | Xem dữ liệu chuẩn bị cho lượt AI tiếp theo; làm mới hoặc xóa khi bot hiểu nhầm | Tách theo người và cuộc trò chuyện; các lệnh không tự trở thành lịch sử chat AI |
| **Thông tin ghi nhớ** | Thêm thủ công hoặc **Tạo ghi nhớ từ tin này**, sửa, ngừng dùng, mở nguồn và phiên bản cũ | Giữ để quản lý; luồng AI phân loại hiện tại không sử dụng kho này |

**Làm mới ngữ cảnh** tạo lại bản tóm tắt bằng cách trích các tin trò chuyện gần đây được phép sử dụng. Đây là trích đoạn có giới hạn, chưa phải tóm tắt ngữ nghĩa bằng AI và không gọi thêm model. **Xóa ngữ cảnh** bỏ các lượt cũ khỏi phiên kế tiếp; giữ lịch sử và ghi nhớ riêng. Làm mới sau khi xóa không khôi phục những lượt đã loại. Thông tin “lần chuẩn bị gần nhất” chỉ cho biết dữ liệu đã chuẩn bị, không chứng minh nhà cung cấp AI đã trả lời thành công.

Ngữ cảnh gửi AI có giới hạn tổng dung lượng: tối đa 12 tin gần đây, tin dài được trích gọn; ghi nhớ được chọn theo dung lượng còn lại. Màn hình ngữ cảnh hiển thị đúng phần được chọn, trong khi Lịch sử hội thoại vẫn giữ bản đã lưu. Giới hạn này giúp một hội thoại dài không khiến các câu hỏi tiếp theo liên tục thất bại.

Khi tạo ghi nhớ, chọn phạm vi:

- **Cá nhân:** gắn mã thành viên cố định, chỉ dùng khi người đó nhắn riêng; không đưa vào nhóm.
- **Cuộc trò chuyện:** chỉ dùng đúng phiên của người đó và cuộc trò chuyện đã chọn. Nếu là phiên trong nhóm, vẫn cần xác nhận chia sẻ vì câu trả lời có thể hiển thị cho nhóm.
- **Nhóm:** có thể dùng cho người hỏi trong nhóm được chọn; cần xác nhận chia sẻ rõ ràng. Nội dung chat riêng không tự chuyển thành ghi nhớ chung.

Có thể đặt hạn sử dụng. **Ngừng dùng** có hiệu lực với lần chuẩn bị câu trả lời tiếp theo, giữ bản ghi để xem lại. Các phiên bản nội dung cũ được lưu có thời hạn và cần cùng quyền đọc hội thoại. Không sửa được tin nhắn gốc đã lưu; chỉ sửa ghi nhớ hoặc làm mới ngữ cảnh.

Hội thoại “mình đã đổi ca rồi” chỉ là lời nhắn, không xác nhận đổi ca thật. Lịch/Workload/Tickets và các bước xác nhận hiện có vẫn quyết định danh tính, quyền và kết quả nghiệp vụ. Luồng lệnh và NLU đã có được giữ nguyên.

## 6. Dữ liệu, thời hạn lưu và sao lưu

Có thể dùng [sao lưu mã hóa hằng ngày vào Google Drive](backup-google-drive.md). Công cụ tạo bản sao SQLite bằng API backup, kiểm tra tính toàn vẹn và chỉ khôi phục vào thư mục thử trống. Lịch chạy cần được chủ thư mục Drive cấp quyền một lần; chỉ báo đã hoạt động sau khi có file thực tế và kiểm tra khôi phục đạt.

- Lần đầu trong mỗi phạm vi người/cuộc trò chuyện, bot gửi thông báo việc lưu tin và quyền xem của quản trị. Hội thoại bị từ chối quyền truy cập không được ghi vào kho này.
- Tin đăng ký `/dangky` được che thông tin; số điện thoại và mẫu bí mật nhận diện được cũng được che trước khi lưu. Việc che là có giới hạn, không bảo đảm phát hiện mọi bí mật viết tự do. Không gửi khóa hoặc dữ liệu không được phép cho bot.
- Mặc định lưu tin **30 ngày**, cấu hình từ 1–365 ngày. Dọn khi có hoạt động và theo lịch trong tiến trình bot; dữ liệu hết hạn được dọn khi bot chạy lại.
- Khi tin nguồn hết hạn, xóa các bản tóm tắt liên quan; xóa nội dung và ngừng dùng ghi nhớ dựa trên nguồn đó; xóa phiên bản có nguồn đã hết hạn. Ghi nhớ thủ công không có nguồn tiếp tục theo hạn/trạng thái riêng. Nhật ký thao tác giữ thông tin thay đổi và dấu kiểm tra nội dung, không giữ bản sao văn bản hết hạn.
- Dữ liệu Admin và hội thoại nằm cạnh trạng thái bot, tên kết thúc bằng `.admin.sqlite3` và `.conversations.sqlite3`. Trên Windows, `AdminCreate` dùng cùng `.runtime/hermes-home/data/zalo` với `Start`; trên Railway, dùng `ZALO_STATE_DIR` trong Volume `/data`. Không commit DB, bản sao lưu hoặc dữ liệu hội thoại lên Git.
- Để sao lưu đơn giản, dừng bot và sao lưu toàn bộ thư mục dữ liệu/Volume, gồm dữ liệu đăng ký và Tickets. Không sao chép riêng file `.sqlite3` của DB đang ghi rồi bỏ các file `-wal`/`-shm`; dùng bản sao nhất quán hoặc dừng tiến trình trước. Bản sao lưu có dữ liệu nội bộ, cần được bảo vệ và dọn theo quy định của nhóm.

Lịch sử Hermes cũ không được tự nhập hoặc tự xóa. Khi bật lưu hội thoại được quản lý, mỗi lượt chat sử dụng một `AIAgent` mới, không nạp lịch sử Hermes cũ, không ghi thêm Session DB/trajectory, không bật công cụ hoặc kho nhớ chung. Adapter chỉ đưa ngữ cảnh và ghi nhớ hiện được phép vào lượt đó. Cơ chế nhúng dùng [Python API chính thức của Hermes](https://hermes-agent.nousresearch.com/docs/guides/python-library/). Model/provider đang cấu hình được giữ nguyên; hội thoại AI vẫn có mức dùng theo nhà cung cấp hiện tại.

## 7. Railway và giao diện Vercel

Theo lựa chọn của người vận hành, **Vercel phục vụ giao diện; Railway chạy bot, API Admin và giữ dữ liệu**. Trình duyệt gọi API cùng địa chỉ Vercel; hàm phía máy chủ Vercel mới gọi Railway. Không mở CORS và không đưa khóa kết nối vào JavaScript trình duyệt.

1. Dùng service bot hiện tại với **một instance** và Volume hiện có. Trang và bot phải chung tiến trình, chung dữ liệu; không tạo tiến trình bot thứ hai để chạy trang.
2. Triển khai mã mới theo [hướng dẫn Railway](deploy-railway.md). Không thay nguyên bộ Variables bằng file mẫu vì có thể mất cấu hình đang dùng. Bản backend phải hỗ trợ `ZALO_ADMIN_PROXY_SECRET` trước khi bật địa chỉ công khai.
3. Trong terminal/SSH tương tác của container, tạo tài khoản trên đúng Volume:

   ```sh
   python -m hermes_zalo.admin_cli create-account
   ```

   Chạy tại `/app` bằng Python hệ thống của image, nơi đã cài gói adapter; môi trường Hermes riêng chỉ nạp adapter qua plugin. Giữ nguyên `ZALO_STATE_DIR` của service để tài khoản được lưu trên đúng Volume. Nhập mật khẩu ẩn tại terminal. Có thể dùng các tham số `--username`, `--permissions`, `--replace` như trên; không gửi token/mật khẩu qua chat.

4. Dùng HTTPS của Railway làm địa chỉ API, rồi đặt các biến:

   ```dotenv
   ZALO_ADMIN_ENABLED=true
   ZALO_CONVERSATIONS_ENABLED=true
   ZALO_CONVERSATION_RETENTION_DAYS=30
   ZALO_ADMIN_HOST=0.0.0.0
   ZALO_ADMIN_PUBLIC_URL=https://TEN-DOMAIN-ADMIN-CUA-BAN
   ZALO_ADMIN_PROXY_SECRET=KHOA_NGAU_NHIEN_RIENG_CHO_HAI_MAY_CHU
   ```

   `ZALO_ADMIN_PUBLIC_URL` là gốc HTTPS **của Railway**, không kèm đường dẫn/query. Khóa proxy dùng 32–256 ký tự ASCII không khoảng trắng; nhập cùng giá trị trong Vercel, không dùng chuỗi ví dụ. Có thể bỏ `ZALO_ADMIN_PORT` để dùng `PORT` Railway cấp; nếu đặt riêng thì Target Port của domain phải khớp. Bản triển khai hiện tại dùng cổng 8787.

5. Trên Vercel, dùng Root Directory `deploy/vercel-admin`, bật **Include source files outside of the Root Directory in the Build Step** và làm theo [hướng dẫn Vercel](../deploy/vercel-admin/README.md). Nhập ba biến phía máy chủ: `LABCOACH_API_URL` (Railway), `LABCOACH_FRONTEND_URL` (địa chỉ chính thức của giao diện) và `LABCOACH_PROXY_SECRET` (cùng khóa Railway). Không thêm tiền tố `NEXT_PUBLIC_` hoặc nhúng chúng vào file tĩnh.

6. Khởi động lại đúng service; kiểm tra đăng nhập qua Vercel, quyền riêng của tài khoản quản lý thành viên, rồi thử các tin Zalo mới. Giữ bot Windows tắt. Truy cập trực tiếp địa chỉ Railway bị từ chối nếu thiếu khóa proxy, kể cả trang đăng nhập; đây là hành vi mong muốn. Khóa proxy đúng vẫn chưa thay thế tài khoản Admin, quyền và kiểm tra CSRF.

Giao diện Vercel chưa triển khai khi chưa có project/địa chỉ do người vận hành cung cấp. Link preview khác địa chỉ chính thức cũng bị chặn API; không tự mở quyền cho mọi domain Vercel. Không lưu SQLite hoặc chạy bot trong hàm Vercel.

**Bản sao trước triển khai:** `/data/maintenance/admin-rollout/pre-admin-20261013-151714.tar.gz` trên Volume Railway, tạo lúc bot ở chế độ chờ. Chưa tải bản sao về máy. Lần triển khai dùng bản mã được lọc và tải qua CLI; thay đổi chưa tự commit/push. Cần đưa các file mã và cấu hình mới vào nhánh GitHub sẽ dùng trước khi dựa vào GitHub autodeploy hoặc import Vercel.

## 8. Kiểm tra và lỗi thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| Không mở được trang | Kiểm tra bot đã kết nối, hai cờ đã bật, đúng cổng; không chạy demo trùng cổng |
| Có tài khoản nhưng đăng nhập không được | Tạo tài khoản trong đúng máy/container và cùng `ZALO_STATE_DIR` của bot; kiểm tra đang mở demo hay hệ thống thật |
| Chỉ thấy Thành viên, không thấy hội thoại | Tài khoản chỉ có `members`; cấp thêm quyền bằng lệnh tạo tài khoản kèm `--replace` nếu được phép |
| Hội thoại trống | Chỉ lưu từ lúc bật; gửi tin mới tới bot đúng phạm vi được cấp quyền |
| Làm mới không có tóm tắt | Chưa có lượt chat AI hoàn thành hợp lệ sau lần xóa ngữ cảnh |
| Thao tác báo dữ liệu vừa thay đổi | Tải lại chi tiết rồi tạo bản xem trước/xác nhận mới; không gửi lại thao tác cũ một cách tự động |
| Không sửa được liên kết | Đọc lý do trên trang; đối chiếu Workload, quyền nguồn và yêu cầu Tickets liên quan trước |
| Model lỗi nhưng trang vẫn mở | Dùng lệnh công việc; kiểm tra provider/key đang cấu hình. Chất lượng trả lời thật cần thử riêng |

`run.ps1 Test` kiểm tra tự động bằng dữ liệu giả, gồm phân quyền, đồng thời, tách phạm vi, lưu sau khởi động lại, giới hạn lưu, lỗi model và tác dụng của reset/ngừng ghi nhớ. `run.ps1 TestHermes` kiểm tra với Hermes đã cài nhưng không dùng model/Zalo thật. Không sửa lịch thật trong các kiểm thử này. Kiểm thử mô phỏng không thay cho thử nhận–gửi Zalo, đăng nhập HTTPS và quyền trên Railway sau triển khai.
