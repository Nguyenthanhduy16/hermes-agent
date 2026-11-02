# Nghiên cứu: Telegram hay Zalo trước, và lớp trợ lý dùng chung

Ngày 31/10/2026. Đây là tài liệu nghiên cứu, chưa triển khai code.

**Nguồn bằng chứng.** Bản Hermes **đang cài** là `~/.hermes/hermes-agent`, commit `dce1e9b`, 09/10/2026, cây làm việc sạch. Đây là `HERMES_SRC` mặc định của `scripts/run.sh`. Mọi đường dẫn `plugins/…`, `gateway/…`, `tools/…`, `cron/…`, `hermes_cli/…`, `agent/…` tính theo gốc repo đó.

Bản ghim cho cloud là `93c9360a` (`scripts/install-hermes.sh:17`), chưa được cài vào `.runtime/hermes-agent`. Giữa hai commit, adapter Telegram đổi khoảng 440 dòng nhưng không đổi tính năng nào nêu dưới đây, chỉ lệch số dòng. Đường dẫn `src/…` là repo này.

## 1. Adapter Telegram Hermes đã có

Adapter là plugin platform trong core: `plugins/platforms/telegram/adapter.py` (7 318 dòng). Plugin đăng ký tại `register` `:7311-7318` với `allowed_users_env=TELEGRAM_ALLOWED_USERS`, `cron_deliver_env_var=TELEGRAM_HOME_CHANNEL` và tin tối đa 4096 ký tự.

| Khả năng | Có sẵn | Bằng chứng (adapter.py trừ khi ghi khác) |
|---|---|---|
| Nhận document mọi loại | Có. File được lưu cache; file text ≤100 KB được chèn vào prompt | `_cache_inbound_document` `:6696`; `MAX_TEXT_INJECT_BYTES` `:6763` |
| Giới hạn tải về | 20 MB, hoặc 2 GB với Bot API server riêng | `_max_doc_bytes` `:700` |
| Ảnh, album, video, sticker | Có | `_handle_media_message` `:6779`; ảnh `:6806`; album `:6837` |
| Voice → STT | Có, `stt_enabled` mặc định bật | voice `:6820`; `gateway/config.py:600`; `gateway/run_inbound.py:1918, 1952` |
| Vị trí | Có | `:6499` |
| Gửi ảnh, file, video, voice từ máy | Có. Ảnh sai kích thước được gửi lại dạng document | `send_image_file` `:5295`; `send_document` `:5356`; `send_video` `:5368`; `send_voice` `:5173` |
| Nút duyệt lệnh (exec approval) | Có | `:4231`; callback `:4718` |
| Clarify, xác nhận slash | Có | `send_clarify` `:4266`; `send_slash_confirm` `:4248`; callback `:4762`, `:4809` |
| Streaming, sửa tin | Có. Chat riêng dùng draft; nhóm dùng sửa tin | `supports_draft_streaming` `:4065`; `edit_message` `:3819`; `send_or_update_status` `:3771` |
| Nhóm, forum topic, topic trong DM | Có | `_resolve_topic_binding` `:6998`; `ensure_dm_topic` `:2531` |
| Mention gating, quan sát nhóm | Có, quan sát mặc định tắt | `:5756`, `:5760`; `_should_process_message` `:6379`; `:6334` |
| Reaction trạng thái | Có (`TELEGRAM_REACTIONS`) | `:7109`; `:7139-7148` |
| Allowlist, phân quyền người bấm nút | Có | `:822`; `:830`; `:917` |
| Giãn khi bị flood | Có | `:5514` |
| Webhook | Có | `:3096` |
| Phiên riêng từng người trong nhóm | Có, mặc định | `gateway/config.py:602` |
| Cron gửi về `origin`/home/`telegram` | Có | `cron/scheduler_delivery.py:44, 84` |
| Tool `send_message` kèm media | Có | `tools/send_message_tool.py:686, 701` |
| Business/Secretary bot | Không có trong core. Có plugin chính thức riêng `hermes-telegram-business` (mỗi câu trả lời phải được chủ duyệt) | `plugin-catalog/hermes-telegram-business.yaml` |

Phía Zalo hiện tại:

- Adapter tự đặt `allow_gateway_control=False` (`src/hermes_zalo/adapter.py:806`). Gateway bỏ qua câu trả lời cho lời nhắc duyệt từ những event này (`gateway/run_inbound.py:1223-1224`).
- `send()` chỉ cho gửi vào chat vừa có lượt agent (`src/hermes_zalo/adapter.py:823-829, 887-895`).

Vì vậy Zalo **chưa có** duyệt lệnh, cron hay `send_message`.

## 2. Telegram Bot API (tài liệu chính thức, Bot API 10.3, 24/08/2026: https://core.telegram.org/bots/api#august-24-2026)

- **File.**
  - Tải về tối đa 20 MB (https://core.telegram.org/bots/api#getfile).
  - Gửi document tối đa 50 MB (https://core.telegram.org/bots/api#senddocument); gửi ảnh tối đa 10 MB (https://core.telegram.org/bots/api#sendphoto).
  - Bot API server riêng: tải về không giới hạn, gửi tối đa 2000 MB (https://core.telegram.org/bots/api#using-a-local-bot-api-server).
- **Privacy mode.** Khi bật, bot chỉ nhận lệnh gửi cho nó, tin trả lời bot, tin gửi qua bot và service message. Khi tắt privacy hoặc bot là admin, bot nhận mọi tin trừ tin của bot khác. Đổi xong phải thêm lại bot vào nhóm (https://core.telegram.org/bots/faq#what-messages-will-my-bot-get, https://core.telegram.org/bots/features#privacy-mode).
- **Tin chủ động.** Người dùng phải mở chat với bot (`/start`) trước; sau đó bot gửi được bất cứ lúc nào. Nhắc việc và routine dùng được.
- **Rate limit.** Khoảng 1 tin/giây mỗi chat, 20 tin/phút mỗi nhóm, khoảng 30 tin/giây khi gửi hàng loạt (https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).
- **Mini Apps.** Web app chạy trong Telegram (https://core.telegram.org/bots/webapps). Hợp để làm màn hình xem bộ nhớ, duyệt skill, cài OAuth.
- **Business bot.** Bot trả lời thay tài khoản cá nhân qua `business_connection_id`. Cần quyền `can_reply` và chỉ trả lời được chat có tin đến trong 24 giờ (https://core.telegram.org/bots/features#business-bots, https://core.telegram.org/bots/api#businessbotrights). Từ Bot API 10.0 không cần Premium (https://core.telegram.org/bots/api#may-8-2026).
- **Reactions.** Muốn nhận update `message_reaction` thì bot phải là admin và khai báo trong `allowed_updates` (https://core.telegram.org/bots/api#update).
- **Streaming.** `sendMessageDraft` chỉ dùng trong chat riêng (https://core.telegram.org/bots/api#sendmessagedraft).

## 3. Hermes đã có gì, lớp trợ lý đa người dùng còn thiếu gì

Hermes được thiết kế cho **một chủ sở hữu mỗi `HERMES_HOME`**. Gateway đã tách phiên theo người, nhưng phần lớn trạng thái lâu dài vẫn dùng chung cho cả home.

| Mảng | Hermes đã có | Còn thiếu cho trợ lý nhiều người dùng |
|---|---|---|
| **Memory** | `MEMORY.md`/`USER.md` ở `get_hermes_home()/"memories"` (`tools/memory_tool.py:40, 321`) | Bộ nhớ dùng chung mọi người. Repo đang buộc tắt (`src/hermes_zalo/adapter.py:85-86`). Cần kho theo `person_id` (và theo nhóm), xem/sửa/xóa được, tiêm qua `pre_llm_call`, cùng tool `user_memory` có phạm vi theo người |
| **Skills** | Thư viện `HERMES_HOME/skills` (`tools/skill_manager_tool.py:69-81`); lệnh `/refine` lưu bài học (`hermes_cli/commands.py:156`) | `skill_manage` ghi thẳng vào thư viện chung: người dùng A có thể sửa hành vi của B. Cần hàng chờ đề xuất, duyệt bởi Admin, và metadata về tool/tầng model được phép |
| **Sandbox** | Docker/ssh/modal. Mỗi phiên một sandbox **chỉ khi** `container_persistent: false`. Nếu không, gateway của profile mặc định dùng **một container `default`** (`tools/terminal_tool.py:484-498`). Giá trị mặc định là `true` (`hermes_cli/status.py:171`, `tools/file_tools_write_guards.py:420`) | `config/hermes.assistant.example.yaml` chưa đặt `container_persistent: false`, nên hiện người dùng có thể đọc file của nhau trong `/workspace`. Cần cách ly theo người và hạn mức CPU, đĩa |
| **Cron** | `cronjob` tạo, liệt kê, xóa; gửi về `origin` (`cron/scheduler_delivery.py:84`) | `_action_list` trả về **mọi job** của home (`tools/cronjob_tools.py:720-728`, `cron/jobs.py:1982`), và xóa theo `job_id` không kiểm tra chủ (`:731-733`). Cần lọc và kiểm tra quyền theo chủ job, giới hạn số job, chặn `deliver` sang chat người khác |
| **Approval** | Chặn lệnh nguy hiểm (`tools/approval.py:1072, 1128`), chờ duyệt theo `session_key` (`:244`), nút Telegram (§1) | Chỉ áp cho lệnh terminal. Chưa có chính sách cho "hành động ra ngoài" (gửi tin, ghi Calendar, tiêu tiền). Cần hook `pre_tool_call` có danh mục rủi ro; Telegram dùng nút, Zalo dùng mã 4 số |
| **Hủy / tiếp tục task** | `/stop` (`hermes_cli/commands.py:124`), `/queue`, `/steer` (`:143-148`), `/bg` (`:134`), `/goal` có pause/resume (`:149`), `/resume` (`:184`), `/retry`, `/undo` (`:100-103`) | Lệnh có sẵn trên Telegram. Trên Zalo, slash command hiện vào `WorkService`, không vào gateway. Cần ánh xạ, gán task cho chủ và giới hạn task chạy nền theo người |
| **Artifact** | Gửi file qua `MEDIA:/đường/dẫn` (`gateway/platforms/base.py:3319`); file lưu trong cache của home | Chưa có kho artifact theo người: chưa có chủ sở hữu, thời hạn lưu, link xem lại, hay endpoint công khai cho Zalo (`sendPhoto` chỉ nhận URL) |
| **Telemetry router** | Hook `pre_api_request`/`post_api_request` có token đã chuẩn hóa (`hermes_cli/plugins.py:119`, `agent/api_request_hooks.py:35-45`); bảng `sessions` có token và chi phí theo phiên (`hermes_state_maintenance.py:20-21`) | Chưa có log **theo bước** gắn với người, tầng model, tool, kết quả và độ trễ cho SCER-AH. `src/hermes_zalo/pilot.py:46` chỉ đọc tổng phiên `source='zalo'`, còn adapter chỉ đếm `agent ok/uncertain` (`adapter.py:755`) |
| **Hạn mức, danh tính, đồng ý lưu** | Allowlist theo kênh | Hạn mức AI chỉ áp cho NLU Lab (`src/hermes_zalo/work.py:570`); **lượt agent chưa bị tính** (`adapter.py:792` không gọi `reserve`). `conversations.py:71` chỉ nhận khóa `zalo:`. Chưa có `person_id` liên kết các kênh |

## 4. Giữ lõi dùng chung

Hermes chạy nhiều platform trong **một gateway**. Lõi trợ lý nên là **một plugin Hermes chung** (kind: general), không phụ thuộc kênh:

- dùng hook trong `VALID_HOOKS` (`hermes_cli/plugins.py:119, 139`): `pre_gateway_dispatch`, `pre_llm_call`, `pre/post_api_request`, `pre_tool_call`;
- đăng ký tool qua `register_tool` (`:456`).

Adapter chỉ còn là transport.

```
src/assistant_core/          # thuần Python, không import Zalo/Telegram
  identity.py   quota.py   consent.py   conversations.py
  memory_store.py   skills_queue.py   artifacts.py   approvals.py   telemetry.py
src/hermes_assistant/        # plugin "assistant-core"
  pre_gateway_dispatch -> identity, khóa/thu hồi, hạn mức, thông báo lưu
  pre_llm_call         -> tiêm bộ nhớ theo người
  pre/post_api_request -> telemetry từng bước (đúng tầng router SCER-AH)
  pre_tool_call        -> approval; chặn cron/skill/send khác chủ
  tools: user_memory, goals, my_cron (bọc cronjob có lọc chủ)
src/hermes_zalo/ + plugin/   # transport Zalo + lệnh Lab cũ
(Telegram: adapter core, chỉ cấu hình)
```

Mỗi kênh chỉ hiện thực vài điểm khác nhau: giao diện duyệt (nút hay mã 4 số), cách nhận file (file hay link), và cách gửi artifact (đường dẫn hay URL).

**Thứ tự làm:**

1. Telemetry.
2. Danh tính và hạn mức. Bước này cũng vá lỗ hổng lượt agent chưa bị tính hạn mức.
3. Cách ly sandbox (`container_persistent: false`) và cron theo chủ job.
4. Hội thoại, đồng ý lưu.
5. Bộ nhớ.
6. Approval.
7. Artifact.
8. Hàng chờ skill.

Bước 2–3 là điều kiện an toàn **trước khi** mở cho người thứ hai, trên bất kỳ kênh nào.

## 5. Khuyến nghị: Telegram-first, lõi chung từ ngày đầu

Nên làm Telegram trước, cụ thể:

- Phát triển và đo SCER-AH trên Telegram.
- Mọi logic trợ lý nằm trong `assistant_core`; Zalo không bị viết riêng.
- Zalo chạy cùng gateway với vai trò kênh triển khai, port theo từng mốc.

**Lý do:**

- P0 #3 (CSV → biểu đồ), #5 (voice), #6 (cron), #7 (nút duyệt) phía kênh đã có sẵn. Thời gian dồn được vào router và lớp đa người dùng (§3), vốn phải viết cho cả hai kênh.
- Nhận CSV trực tiếp, nên benchmark L1–L3 đúng kịch bản. Zalo bắt buộc dùng link hoặc ảnh chụp bảng.
- Rate limit và giới hạn file có công bố, nên thí nghiệm tái lập được.

**Rủi ro:**

- Người dùng Việt Nam chủ yếu dùng Zalo, nên pilot người thật trên Telegram ít và lệch đối tượng.
- Kết quả đo phải ghi rõ kênh. Zalo cần một bộ benchmark dùng link.

**Trình bày với hội đồng:** Telegram là môi trường thí nghiệm đầy đủ tính năng; Zalo là kênh triển khai. Telemetry ghi ở tầng LLM nên so sánh được giữa hai kênh.

Zalo-first chỉ hợp lý nếu pilot người dùng thật là tiêu chí chấm chính. Ngay cả khi đó, lõi chung ở §4 vẫn là bước làm trước.

## 6. Thử nhanh (chỉ mô tả, không chạy với token thật)

```bash
# .env (launch.py:42 sao env cho gateway); bot thử tạo bằng @BotFather
TELEGRAM_BOT_TOKEN=<token-thử>
TELEGRAM_ALLOWED_USERS=<user_id_của_bạn>      # không bật TELEGRAM_ALLOW_ALL_USERS
TELEGRAM_HOME_CHANNEL=<user_id_của_bạn>
# .runtime/hermes-home/config.yaml:
#   platform_toolsets: { telegram: [web, vision, code_execution, file, skills, session_search, todo] }
#   terminal: { backend: docker, container_persistent: false }
#   memory: { memory_enabled: false, user_profile_enabled: false }
scripts/run.sh Update && scripts/run.sh Start     # HERMES_HOME=.runtime/hermes-home
tail -f .runtime/logs/gateway.log
# Thử: gửi sales.csv + "vẽ doanh thu theo tháng"; gửi voice; yêu cầu lệnh cần duyệt; /stop giữa chừng.
```

Lưu ý:

- Chỉ adapter Zalo kiểm tra toolset (`src/hermes_zalo/adapter.py:71-79`). Telegram chưa có chốt tương đương, và `cronjob` chỉ nên bật sau khi có lọc theo chủ job (§3).
- Mục 3 và 4 nêu lỗ hổng cách ly. Phải xử lý trước khi cho người thứ hai dùng.
