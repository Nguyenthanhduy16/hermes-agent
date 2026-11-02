# Nghiên cứu: quản lý Hermes Agent trên cloud và phần dùng lại được

Ngày 31/10/2026. Đây là tài liệu nghiên cứu, chưa triển khai code.

Bối cảnh đã chốt: làm Telegram trước; lõi dùng chung (identity, quota, memory cách ly, approvals, telemetry cho router SCER-AH) là một plugin Hermes viết ngay từ đầu; Zalo port sau. Xem [nghien-cuu-telegram.md](nghien-cuu-telegram.md).

**Nguồn.**

- Mã Hermes đang cài: `~/.hermes/hermes-agent`, commit `dce1e9b`. Chỉ đọc; đường dẫn tính theo gốc repo đó.
- Tài liệu chính thức và GitHub (số sao lấy từ GitHub API ngày 31/10/2026).
- Ký hiệu **[chưa kiểm chứng]**: thông tin chỉ có ở nguồn thứ ba hoặc tóm tắt tìm kiếm.

## 1. Dashboard đi kèm Hermes

| Thành phần | Chức năng | Bằng chứng |
|---|---|---|
| `hermes dashboard` (cổng 9119) | SPA Vite + React 19, phục vụ bởi FastAPI. Các trang: Status, Sessions, Chat, Cron, Skills, Memory, Config, Env/API keys, Models, MCP, Plugins, Profiles, Channels, Pairing, Webhooks, Files, Logs, Analytics, System | `web/README.md`; `web/src/pages/*.tsx`; API ở `web/src/lib/api.ts` (`/api/cron/jobs`, `/api/memory`, `/api/logs`, `/api/analytics/usage`, `/api/gateway/restart`…) |
| Analytics | Token, chi phí ước tính/thực tế, số lần gọi API và tool, theo model và theo phiên | `hermes_cli/web_routers/analytics.py:160` |
| Auth | Mặc định chỉ bind `127.0.0.1`. Khi bind ra ngoài hoặc có `dashboard.public_url`, cổng auth bắt buộc bật | `hermes_cli/subcommands/dashboard.py:19`; `hermes_cli/web_server.py:530-541` |
| Nhà cung cấp auth | Ba plugin auth: `basic` (scrypt, token HMAC, không cần DB), `nous` (OAuth Nous Portal), `self_hosted` (OIDC) | `plugins/dashboard_auth/{basic,nous,self_hosted}`; đăng ký client OAuth bằng `hermes dashboard register` (`hermes_cli/dashboard_register.py:1-7`) |
| API server (cổng 8642) | API tương thích OpenAI (`/v1/chat/completions`, `/v1/runs`, `/api/sessions`, `/api/jobs`), xác thực bằng `API_SERVER_KEY` | `gateway/platforms/api_server.py:1-6` |
| Kanban | Bảng task cho nhiều profile cộng tác | `hermes_cli/kanban.py:1-5`; `hermes_cli/commands.py:289` |
| Profiles + multiplex | Một gateway phục vụ mọi profile trên máy. `profile_routes` chuyển người gửi theo `user_id`/`chat_id` sang profile riêng; mỗi profile có memory, skills, cron và phiên riêng | `gateway/config.py:605-612, 643`; `gateway/profile_routing.py:1-11, 174` |
| Docker | `docker-compose.yml` mount `~/.hermes:/opt/data`. Dashboard bật bằng `HERMES_DASHBOARD=1` | Hướng dẫn: https://hermes-agent.nousresearch.com/docs/user-guide/docker |

**Đánh giá:**

- Dashboard chạy headless trên server được. Cách an toàn là để bind loopback rồi mở qua SSH tunnel; hoặc dùng `basic`/OIDC sau HTTPS.
- Đây là **console cho một người vận hành**, không phải trang cho nhiều người dùng: không có tài khoản theo người dùng cuối, không có quota, không có memory theo người (https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard).
- Có thể mở rộng bằng plugin: thêm tab, API route, auth provider (https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard).

## 2. Dịch vụ host và dự án cộng đồng

**Chính thức.**

- **Hermes Cloud** trên Nous Portal (https://portal.nousresearch.com/): "Portal hosts your agent in the cloud". Gói Plus giá $20/tháng có 1 agent (4 GB RAM, 25 GB đĩa); gói Super $100, gói Ultra $200.
- Trang tài liệu Nous Portal (https://hermes-agent.nousresearch.com/docs/integrations/nous-portal) chỉ nói về suy luận model và Tool Gateway (web, ảnh, TTS, sandbox Modal).
- Sandbox riêng cho từng agent và phân quyền nhóm **[chưa kiểm chứng]** (nguồn: https://www.tonyreviewsthings.com/hermes-agent-cloud-nous-portal/).
- Mô hình này là một agent cho mỗi người trả tiền, không hợp với một bot phục vụ nhiều người. Hơn nữa là dịch vụ trả phí, nên không dùng.

**Template và nhà host.**

- Railway có nhiều template (https://railway.com/deploy/hermes-agent-nous-research, https://railway.com/deploy/hermes-official-dashboard). **Chưa xác nhận** template nào do Nous phát hành.
- Ngoài ra: Zeabur, hướng dẫn Vultr (https://docs.vultr.com/how-to-deploy-hermes-agent-open-source-self-hosted-ai-agent), ElfHosted, Flux, RailPush. Chưa thấy template cho Render, Fly.io, Coolify.
- **Railway không chạy được Docker trong Docker**, nên không làm sandbox ở đó được.

**Dự án cộng đồng đáng chú ý** (tính năng lấy từ README, chưa chạy thử):

| Dự án | Sao | Cập nhật | License | Stack | Điểm chính | Nhiều người dùng |
|---|---|---|---|---|---|---|
| [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui) | ~19.3k | 31/10/2026 | MIT | Python | Chat web/di động, phiên, file workspace, profile, token | Không (1 chủ) |
| [Yuan-lab-LLM/ClawManager](https://github.com/Yuan-lab-LLM/ClawManager) | ~1.9k | 31/10/2026 | MIT | Go + React, K8s | Control plane nhiều agent; AI Gateway (usage, cost, audit); Skill Hub có quét; Teams, LDAP | **Có** |
| [joeynyc/hermes-hudui](https://github.com/joeynyc/hermes-hudui) | ~1.9k | 31/10/2026 | MIT | Python | Theo dõi memory và trạng thái | Không |
| [xaspx/hermes-control-interface](https://github.com/xaspx/hermes-control-interface) | ~900 | 28/06/2026 | MIT | JS | CRUD profile, gateway, cron, log, token + cảnh báo ngân sách | **Có** (RBAC 3 vai trò, 20 quyền) |
| [JPeetz/Hermes-Studio](https://github.com/JPeetz/Hermes-Studio) | ~410 | 03/07/2026 | tùy biến | TS | Memory graph, cron, approvals, audit trail, chi phí theo agent | Một phần |
| [praveen-ks-2001/hermes-agent-template](https://github.com/praveen-ks-2001/hermes-agent-template) | ~330 | 21/10/2026 | không có | Python | Admin cho Railway; duyệt người nhắn bot, backup | Chỉ có admin |
| [kwkuh/chathermes](https://github.com/kwkuh/chathermes) | ~11 | 31/07/2026 | tùy biến | TS/Bun | SaaS: mỗi người trả phí một Hermes riêng (Hetzner), admin tenant và billing | **Có** |
| [eggyrooch-blip/hermes-multitenancy](https://github.com/eggyrooch-blip/hermes-multitenancy) | ~26 | 08/10/2026 | MIT | Plugin Python | Một bot Feishu; **mỗi người một profile riêng** (SOUL, memory, phiên, workspace) | **Có** |

Thực tế cộng đồng chia hai hướng: **một instance (hoặc profile) cho mỗi người** (chathermes, hermes-multitenancy, ClawManager), hoặc **console cho một chủ** (webui, hudui). Chưa thấy dự án nào làm trợ lý đa người dùng trên một bot chat kèm quota, approvals và telemetry theo bước như đồ án cần.

## 3. Tham chiếu thiết kế

**Trợ lý cùng loại:**

| | Dots (OpenAI) | Muse (Meta) | Grok Bot (xAI) |
|---|---|---|---|
| Activity | Có view Activity: tiến độ, file, task con; trả lời yêu cầu đang chờ ngay tại đó | Dòng trạng thái dưới avatar, kèm log đầy đủ và quyền đã cấp | Action Recording chỉ có ở gói Teams |
| Dừng / take over | Pause chỉ dừng task chính; "Take over" điều khiển máy | **[chưa thấy]** | "Take over" cho SSO/CAPTCHA; Recover/Reset |
| Memory | Ghi chú riêng, docs không mô tả trình xem | **File memory đọc/sửa trực tiếp**, có tab Goals | Không có màn hình; quản lý bằng chat ("Forget…") |
| Routines | View Scheduled: tắt/xóa | Task theo lịch hoặc theo sự kiện | Tối đa 50 routine, cách nhau ≥5 phút, giữ 20 lần chạy gần nhất, pause/resume |
| Approvals | 4 mức: tự làm / khi được bảo / hỏi trước / giao lại cho người | Thẻ chấp nhận/từ chối trước khi gửi mail, mua hàng | Allow once / Always allow / Deny; thẻ hết hạn sau 30 phút |
| Connectors | Xem tài khoản và quyền từng app | **[chưa thấy]** | Installed/Needs auth/Disconnected; thu hồi ở cả hai phía |

Nguồn:

- Dots: https://learn.chatgpt.com/docs/dots/controls, https://learn.chatgpt.com/docs/dots/tasks-and-memory, https://learn.chatgpt.com/docs/dots/computers-and-apps
- Muse: https://introducing.muse.ai, https://axios.com/2026/09/08/meta-debuts-muse-personal-ai-agent. Không tìm thấy bài trên about.fb.com.
- Grok Bot: https://docs.x.ai/grok-bot/approvals-security-and-privacy, https://docs.x.ai/grok-bot/skills-routines-and-automations, https://docs.x.ai/grok-bot/teams-and-enterprises. Phần memory lấy từ tóm tắt tìm kiếm **[chưa kiểm chứng]**.

**Rút ra cho đồ án:** người dùng cần năm điều khiển — Activity, Stop, Memory, Routines, Approvals. Bộ nhớ nên cho xem và sửa được như Muse, để tránh điểm Dots bị chê.

**Quan sát LLM (telemetry cho router):**

| Công cụ | License | Tự host | Cách gắn | Hợp với VPS nhỏ |
|---|---|---|---|---|
| Langfuse v4 | MIT (trừ `ee/`) | Web, worker, Postgres, ClickHouse, Redis, S3; khuyến nghị 4 core / 16 GiB (https://langfuse.com/self-hosting/deployment/docker-compose) | SDK trên OTel; OTLP HTTP; nhận `gen_ai.*`, `session.id`, `user.id` (https://langfuse.com/integrations/native/opentelemetry) | Không. Dùng Cloud free tier nếu cần |
| Arize Phoenix | Elastic License 2.0 | 1 container + SQLite, hoặc Postgres (https://arize.com/docs/phoenix/self-hosting/architecture) | OTel/OpenInference; chi phí theo span và phiên (https://arize.com/docs/phoenix/tracing/how-to-tracing/cost-tracking) | **Có** (RAM ~1–2 GB **[chưa kiểm chứng]**) |
| OpenLLMetry | Apache-2.0 | Chỉ là SDK, không có backend (https://github.com/traceloop/openllmetry) | OTel tự động | Dùng kèm một backend |
| Helicone | Apache-2.0 | 1 container all-in-one + gateway | Proxy | Không; đang ở chế độ bảo trì **[chưa kiểm chứng]** |

## 4. Đề xuất kiến trúc quản lý

### 4.1 Dùng lại gì

1. **Dashboard Hermes làm console vận hành** cho người phát triển: config, log, cron, skills, phiên, Analytics, restart gateway. Chỉ bind loopback, truy cập qua SSH tunnel. Không viết lại phần này.
2. **Trang Admin hiện có làm trang sản phẩm** cho Admin và giảng viên. Lý do giữ nó:
   - đã có tài khoản quản trị tách khỏi người dùng chat;
   - đã có vai trò `admin/faculty` và quyền `members, conversations, memories…` (`src/hermes_zalo/admin_auth.py:13-14`);
   - đã có proxy Vercel → backend với khóa riêng (`deploy/vercel-admin/README.md`).

   Cần tách Admin khỏi Lab: gọi `assistant_core` thay vì `WorkService`.
3. **Telemetry tách hai lớp:**
   - **Bảng SQLite riêng** (`assistant_core/telemetry.py`): nguồn dữ liệu chính thức của đồ án. Mỗi lượt và mỗi bước ghi: `person_hash`, tầng/model, token, chi phí, tool, độ trễ, kết quả, `router.choice/reason`.
   - **Xuất OTel** (thuộc tính `gen_ai.*`) sang **Phoenix một container** để xem trace. Đổi sang Langfuse sau này chỉ cần đổi endpoint.
   - Dashboard Analytics của Hermes chỉ dùng để đối chiếu tổng.
4. **Cách ly theo người: thử `profile_routes` theo `user_id`** (cùng cách hermes-multitenancy làm) trước khi tự viết.
   - Nếu chạy được, mỗi người có memory, skills, cron và phiên riêng nhờ chính Hermes.
   - Rủi ro: route được khai báo tĩnh trong config, cần tự tạo profile khi có người dùng mới, và tốn tài nguyên cho mỗi profile.
   - Nếu không chạy được, quay về plugin chung lọc theo `person_id`, như [nghien-cuu-telegram.md](nghien-cuu-telegram.md) §3–4.
   - Dù chọn cách nào, quota, approvals và telemetry vẫn nằm ở plugin chung.

### 4.2 Các trang cần có

| Trang | Ở đâu | Nguồn dữ liệu |
|---|---|---|
| Người dùng & quyền (mã mời, khóa, vai trò, liên kết Telegram/Zalo) | Admin | `identity` |
| Hội thoại / task: đang chạy, đang chờ duyệt, **Dừng** | Admin + lệnh chat `/hoatdong`, `/dung` | bảng task + `/stop` của gateway |
| Memory theo người: xem/sửa/xóa | Admin + `/nho`, `/quen` | `memory_store` |
| Approvals & duyệt skill: thẻ "1 lần / luôn / từ chối", hết hạn 30 phút | Nút Telegram; hàng chờ trên Admin | `approvals`, `skills_queue` |
| Routines: tạm dừng/xóa; giới hạn số job, chu kỳ tối thiểu | Admin + `/lichtrinh` | cron lọc theo chủ |
| Chi phí & quota theo người/tầng | Admin | `quota` + `telemetry` |
| Trace router SCER-AH + benchmark L1–L3 (B1–B3) | Phoenix (trace) + trang báo cáo Admin (tổng hợp) | `telemetry`, harness benchmark |
| Vận hành hệ thống | Dashboard Hermes | Hermes |

### 4.3 Mô hình triển khai

- **Một VPS có Docker** (4–8 GB RAM):
  - một gateway Hermes chạy cả Telegram và Zalo;
  - container sandbox theo phiên (`container_persistent: false`);
  - backend Admin;
  - Phoenix.
- Giao diện Admin có thể giữ trên Vercel, proxy về VPS.
- Railway chỉ hợp cho chế độ không có sandbox. Nous Hermes Cloud không dùng (trả phí, một agent cho mỗi người).

### 4.4 Bảo mật

- Token bot, khóa model và khóa Google chỉ nằm trong `.env` trên VPS. Sandbox không mount thư mục chứa `.env`. Vercel chỉ giữ khóa proxy.
- Dashboard Hermes và Phoenix chỉ bind loopback, mở qua SSH tunnel. Nếu buộc phải mở ra ngoài thì dùng OIDC/basic sau HTTPS.
- Admin dùng tài khoản riêng, cookie HttpOnly, kiểm tra quyền theo trang. Mọi thao tác sửa memory, duyệt hay khóa đều ghi audit.
- Telemetry chỉ ghi `person_hash`. Nội dung prompt chỉ được lưu khi người dùng đã đồng ý lưu hội thoại và trong thời hạn lưu.

### 4.5 Thứ tự (15 tuần)

| Tuần | Việc |
|---|---|
| 1–2 | VPS + Docker; gateway Telegram chạy thật; dashboard Hermes qua tunnel; sandbox theo phiên |
| 3–4 | Plugin chung: `telemetry` (SQLite + OTel→Phoenix), `identity`, `quota` |
| 5 | Thử `profile_routes` theo người; chốt cách cách ly memory/cron/skills |
| 6–7 | Memory theo người + `/nho`; trang Memory và Người dùng trên Admin |
| 8–9 | Approvals (`pre_tool_call`), nút Telegram; bảng task + `/dung`; trang Task |
| 10–11 | Router SCER-AH và harness benchmark; trang báo cáo chi phí/trace |
| 12 | Routines có giới hạn, trang Routines; hàng chờ duyệt skill |
| 13–14 | Port Zalo (transport + duyệt bằng mã); pilot người dùng thật |
| 15 | Đóng băng, số liệu, bảo vệ |
