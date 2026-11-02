# Nghiên cứu sản phẩm: Grok Bot, Dots, Muse và hướng phát triển trợ lý Zalo

Báo cáo này ghi lại kết quả nghiên cứu ngày 31/10/2026. Nguồn chủ yếu là báo chí và blog bên thứ ba từ tháng 8 đến tháng 10/2026. Một số trang chính thức của xAI, OpenAI và Meta không đọc được (lỗi 403 hoặc trang không có nội dung). Chỗ nào nguồn mâu thuẫn hoặc chưa kiểm chứng đều được ghi rõ.

## A. Ba sản phẩm tham chiếu

### 1. "Grokbot" có hai nghĩa

**(a) @grok trên X.** Người dùng nhắc @grok trong phần trả lời của một bài đăng công khai (có từ 03/2025). Grok đọc bài gốc, chuỗi trả lời, ảnh/video, cộng thêm tìm kiếm thời gian thực trên X và web, rồi trả lời công khai ngay trong chuỗi.

- Nghiên cứu "Grok in the Wild" (ICWSM 2026) phân tích 41.735 lượt tương tác. Kết quả: Grok trả lời khoảng 62% yêu cầu, chủ yếu với vai trò **cung cấp thông tin** hoặc "trọng tài sự thật" trong tranh cãi. https://arxiv.org/abs/2602.11286
- Tính năng sửa ảnh ngay trong phần trả lời (12/2025) gây bê bối deepfake. Đến 01/2026 tính năng này bị giới hạn cho người trả phí; EU yêu cầu X lưu giữ dữ liệu, Indonesia và Malaysia chặn Grok. https://www.ky3.com/2026/01/11/musks-grok-chatbot-restricts-image-generation-after-global-backlash-sexualized-deepfakes
- **Bài học:** bot hoạt động trong nhóm hoặc chuỗi công khai phải đọc được ngữ cảnh chuỗi. Rủi ro lớn nhất là tạo hoặc sửa ảnh người thật rồi đăng công khai.

**(b) Grok Bot của xAI (beta 11/08/2026).** Đây là "đồng đội AI" luôn chạy.

- Mỗi tài khoản có một **máy tính đám mây bền vững** (trình duyệt, file, dòng lệnh) và kết nối dịch vụ ngoài qua connector/MCP (Gmail, Calendar, Drive, GitHub, Notion).
- Có thể đưa tối đa 6 bot vào một nhóm chat, gọi bằng `@`. Các bot tự chia việc, với một bot "chief of staff" điều phối.
- Có **Routine** (chạy theo lịch, hoặc học quy trình sau khi xem người dùng làm một lần). Quy trình thành công được lưu thành **skill** tái sử dụng. Người dùng đặt được ranh giới phải duyệt trước khi bot gửi, đăng, xóa hoặc mua.
- Không chọn được model cho từng tác vụ; giá khoảng $200/tháng.
- Nguồn: https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/ , https://mem0.ai/blog/grok-bot-guide

> Cần xác nhận lại nhóm muốn tham chiếu (a) hay (b). Bản (b) cùng thế hệ với Dots và Muse.

### 2. Dots (OpenAI, DevDay 29/09/2026)

- Agent "always-on" theo đuổi mục tiêu ở nền, chạy trên GPT-6 Astra. Người dùng bắt đầu với một dot chính có tên riêng; sau này sẽ có dot chuyên trách với danh tính và công cụ riêng.
- Kênh: ChatGPT, Codex, **Slack, Microsoft Teams**; SMS "sắp có". Bộ nhớ **giữ ngữ cảnh xuyên kênh**: việc bắt đầu ở Slack có thể làm tiếp trong ChatGPT.
- Khi rảnh, dot tự nghiên cứu với quyền chỉ đọc. Mọi hành động chạm vào tài khoản phải qua bước kiểm tra. Bị phê bình vì người dùng **không xem được bộ nhớ**.
- Nguồn: https://siliconangle.com/2026/09/29/openai-launches-dots-always-on-ai-agents-in-chatgpt-with-their-own-cloud-computers/ , https://www.dsebastien.net/2026-10-03-openai-dots-always-on-agents-with-a-memory-you-cant-inspect/

### 3. Muse (Meta, 08/09/2026)

- Kênh: app iOS/Android, muse.ai, **WhatsApp**. Chưa có nguồn nói về nhóm chat, Messenger hay Threads.
- Kết nối email, lịch, thanh toán. Mở trình duyệt để điền form, đặt chỗ, theo dõi ngân sách. Lập và thực hiện dần các mục tiêu dài hạn.
- Mỗi người có một **VM riêng** và xem được trình duyệt khi agent đang làm.
- Người dùng **xem, sửa, xóa được file Memory**, có tab Goals và lệnh "quên" một chủ đề.
- Tự nhắn trước, mức độ chủ động chỉnh được. Hỏi trước khi gửi tin thay người dùng hoặc chi tiền.
- Nguồn: https://www.pbs.org/newshour/nation/meta-launches-personal-ai-agent-muse-to-help-with-everyday-tasks , https://builtin.com/articles/meta-muse

### Mẫu số chung

1. Gặp người dùng ở kênh nhắn tin họ đã dùng sẵn.
2. Có bộ nhớ lâu dài, tốt nhất là người dùng xem và sửa được.
3. Có mục tiêu, routine, cron, với mức chủ động chỉnh được.
4. Có sandbox hoặc máy riêng để chạy code, trình duyệt và file.
5. Học quy trình thành skill.
6. Phải duyệt trước mọi hành động có tác dụng ra bên ngoài.
7. Chia việc được cho nhiều agent.

## B. Định hướng: trợ lý cá nhân, không còn là bot Lab

Từ 31/10/2026, bot là **trợ lý cá nhân** theo kiểu Grok Bot, Muse, Dots: chạy trên Zalo trước, sau đó mở sang X, Messenger, Threads. Các chức năng nghiệp vụ Lab (đăng ký bằng số điện thoại trong Workload, `/lich`, `/doica`, nhắc ca, Issue theo vai trò) **không còn là sản phẩm**. Chúng được tắt bằng cấu hình và giữ lại trong repo để tham khảo, không phát triển thêm.

### Hạ tầng giữ lại để dùng chung

| Thành phần hiện có | Dùng lại cho trợ lý |
|---|---|
| `api.py`, polling, chống trùng, chia tin 2000 ký tự, không gửi lại khi chưa chắc đã gửi | Kênh Zalo (giữ nguyên) |
| Allowlist, khóa/thu hồi người dùng | Kiểm soát ai được dùng bot |
| `ai_usage.py` (hạn mức theo người/ngày) | Ngân sách chi phí theo người, theo tầng model |
| `conversations.py` (lưu có đồng ý, thời hạn lưu) | Lịch sử hội thoại; nguồn dữ liệu pilot |
| Trang Admin | Quản lý người dùng, xem bộ nhớ, duyệt skill, dashboard router |
| Mã xác nhận 4 số của luồng đổi ca | **Cơ chế duyệt** trước hành động ra bên ngoài |
| `reminders.py` (hàng đợi gửi bền vững) | Đường gửi tin chủ động cho routine/cron |
| `pilot.py` | Log từng bước cho SCER-AH |

Đăng ký bằng số điện thoại trong Workload được thay bằng **mã mời** hoặc Admin duyệt.

## C. Bộ chức năng trợ lý

Ràng buộc của Zalo Bot Platform:

- chỉ có text, ảnh, sticker, voice; **không nhận được file**;
- trong nhóm, bot chỉ thấy tin @bot hoặc tin trả lời bot;
- tin chủ động gửi bằng `sendMessage` như tin thường (giới hạn tần suất chưa công bố).

### P0: lõi trợ lý (Đồ án 2)

| # | Chức năng | Học từ | Cách làm trên Hermes | Giá trị cho SCER-AH |
|---|---|---|---|---|
| 1 | **Hội thoại + định tuyến model theo bước** | cả 3 | Đã nối (`ZALO_BOT_MODE=assistant`); router đặt giữa agent loop và LLM | Đối tượng nghiên cứu chính |
| 2 | **Bộ nhớ cá nhân xem/sửa/xóa được** (`/nho`, `/nho them …`, `/quen …`) | Muse (memory file, Goals), Dots (bị chê vì không xem được bộ nhớ) | Kho bộ nhớ riêng theo `user_id` (và theo nhóm), adapter tiêm vào `channel_prompt`; tool `user_memory` có phạm vi theo người đang nhắn. Không dùng memory chung của Hermes | Tín hiệu ngữ cảnh: độ dài, mức cá nhân hóa |
| 3 | **Phân tích dữ liệu → biểu đồ** | Grok Bot (máy riêng), Muse (VM) | Link CSV/Sheets hoặc ảnh bảng → `execute_code` trong Docker → `MEDIA:` PNG → `sendPhoto` | Kịch bản benchmark L1–L3 của đề xuất |
| 4 | **Tìm kiếm, tóm tắt, kiểm chứng có nguồn** | @grok trên X | `web_search`/`web_extract`, bắt buộc kèm link nguồn | Tóm tắt rẻ, kiểm chứng đắt: phân bố độ khó rõ |
| 5 | **Hiểu ảnh và tin thoại** | Grok, Muse | Ảnh: đã có (vision). Voice: thêm `message.voice.received` → tải `voice_url` → STT của Hermes | Bước rẻ (Fast) chen giữa các bước khó |
| 6 | **Nhắc việc và routine bằng câu tự nhiên** ("7h mỗi sáng gửi tin AI", "nhắc mình 3h chiều mai gọi mẹ") | Grok Routine, Muse scheduled tasks | Toolset `cronjob` của Hermes + mở `send()` cho cron đến đúng chat của chủ job, qua hàng đợi `reminders` | Tác vụ lặp lại: đo hiệu ứng tái dùng skill (warm-start) |
| 7 | **Duyệt trước hành động ra ngoài** | cả 3 | Mã xác nhận 4 số tái dùng từ đổi ca; ranh giới: gửi tin cho người khác, tạo/xóa dữ liệu, tiêu tiền | Chốt chặn (guardrail) trong a_t |

### P1: trợ lý chủ động và có kết nối

| # | Chức năng | Học từ | Ghi chú |
|---|---|---|---|
| 8 | **Mục tiêu dài hạn + mức chủ động** (`/muctieu`, `/chudong tat\|it\|nhieu`) | Muse Goals, Dots proactive research | Cron check-in theo mục tiêu; mặc định ít |
| 9 | **Kết nối Google Calendar/Gmail/Drive** | Grok Bot, Muse, Dots | OAuth theo từng người qua MCP; đọc trước, ghi phải duyệt (#7) |
| 10 | **Trợ lý trong nhóm** kiểu @grok: trả lời, tóm tắt, kiểm chứng trong luồng được @ | @grok | Bot chỉ thấy tin @bot hoặc tin trả lời bot, nên **không tóm tắt được cả nhóm**; chỉ tóm tắt những gì bot thấy. Không tạo hay sửa ảnh người thật |
| 11 | **Học skill có duyệt** | Grok Bot skills | Sau tác vụ thành công: agent đề xuất SKILL.md, người dùng hoặc Admin duyệt; kèm metadata tool cho phép và tầng tối thiểu | Vòng học kỹ năng của SCER-AH |

### P2: mở rộng

| # | Chức năng | Học từ |
|---|---|---|
| 12 | Nhiều agent chuyên trách (nghiên cứu, dữ liệu, lịch) phối hợp qua `delegate_task`, mỗi agent một tầng model | Grok Bot team, Dots chuyên trách |
| 13 | Danh tính xuyên kênh: liên kết Zalo + Telegram/Messenger/X vào một bộ nhớ | Dots |
| 14 | Trình duyệt tự động (đặt chỗ, điền form) trong sandbox riêng theo người | Muse, Grok Bot |
| 15 | Adapter X/Threads (trả lời khi được @) và Messenger, dùng chung lớp sự kiện chuẩn | @grok |

### Không làm

- Sửa hoặc tạo ảnh người thật rồi đăng công khai (bài học deepfake của Grok).
- Bộ nhớ ẩn mà người dùng không xem được (điểm Dots bị chê).
- Hành động thay người dùng mà không duyệt.

**Thứ tự gợi ý cho 15 tuần:** 1 → 3 → 4 → 2 → 6 → 7 → 5. Các mục P1 làm nếu còn thời gian; mục 11 nên làm nếu đề tài cần phần học kỹ năng.
