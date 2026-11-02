# Chế độ trợ lý: nối Hermes Agent vào Zalo

`ZALO_BOT_MODE` chọn cách bot xử lý tin nhắn:

| Chế độ | Lệnh Lab (`/lich`, `/doica`, `/issue`, `/dangky`, `/nhac`…) | Câu tự nhiên, ảnh |
|---|---|---|
| `lab` (mặc định) | Chạy trực tiếp trong adapter | Chỉ nhận diện ý định Lab; câu ngoài phạm vi trả `/menu` |
| `assistant` | Chạy trực tiếp như cũ, không tốn lượt AI | Đưa vào **vòng lặp Hermes Agent** đầy đủ: tìm web, đọc ảnh, chạy code trong sandbox, vẽ biểu đồ, dùng SKILL.md |

## Luồng xử lý ở chế độ `assistant`

```
Zalo getUpdates ─► parse (text | image) ─► kiểm tra quyền ─► chống trùng
   │
   ├─ lệnh /…, mẫu câu Lab đã biết ("lịch hôm nay", "mình muốn đổi ca"),
   │  "nhắc mình…", "đăng ký…", hoặc đang trong hộp thoại đổi ca
   │      └─► WorkService (như chế độ lab)
   │
   └─ còn lại ─► MessageEvent (ảnh tải về cache Hermes) ─► Hermes gateway
                    └─► agent loop (model + toolsets) ─► send() / send_image_file()
                                                          └─► Zalo sendMessage / sendPhoto
```

Code chính:

- `src/hermes_zalo/adapter.py`: `_agent_route`, `_start_agent_turn`, `_agent_send`, `send_image_file`, `send_typing`.
- `src/hermes_zalo/work.py`: `WorkService.owns_turn` quyết định tin nào vẫn thuộc về lệnh Lab.
- `src/hermes_zalo/api.py`: thêm `send_photo` (URL hoặc upload multipart), `send_chat_action`, `download`.
- `config/hermes.assistant.example.yaml`: toolset và sandbox cho Zalo.

## Ai được dùng agent

Chỉ những người sau mới đến được model:

- người trong `ZALO_ALLOWED_USERS`;
- thành viên đã duyệt đăng ký, trong chat riêng đã liên kết;
- thành viên của nhóm có trong `ZALO_ALLOWED_GROUPS` khi bật `ZALO_GROUP_ACCESS=members`.

Người chưa duyệt chỉ được dùng `/dangky`. Tài khoản bị khóa hoặc thu hồi bị chặn ngay cả với câu trả lời đang gửi dở.

Bot chỉ gửi tin vào cuộc trò chuyện vừa nhận một lượt agent hợp lệ. Gửi tới chat khác, kể cả từ cron, đều bị từ chối.

## Bật thử trên máy (Linux)

1. Trong `.env`, đặt `ZALO_BOT_MODE=assistant`, rồi chạy `scripts/run.sh Update`. Bước này ghép `hermes.assistant.example.yaml` vào `.runtime/hermes-home/config.yaml`.
2. Chạy `scripts/run.sh Model` để chọn model hoặc provider. Lúc thử nghiệm nên dùng model rẻ. Bộ định tuyến SCER-AH sẽ thay phần này sau.
3. Cài Docker và kéo image sandbox `nousresearch/hermes-sandbox:desktop`. Code do model sinh ra chạy trong container, không chạy trên máy đang giữ token.
4. Chạy `scripts/run.sh Start`, rồi nhắn bot những câu sau:
   - `Xin chào, bạn làm được gì?`
   - `Tìm giúp mình 3 tin AI mới nhất hôm nay`
   - Gửi ảnh chụp một bảng số liệu kèm chú thích `Tóm tắt bảng này`
   - `Tải CSV ở <link công khai> và vẽ biểu đồ doanh thu theo tháng`
   - `/lich homnay`: lệnh này vẫn chạy trực tiếp, không gọi model.

## Giới hạn của Zalo Bot Platform (tài liệu chính thức, 10/2026)

- **Không nhận được file.** Chỉ có sự kiện `text`, `image`, `sticker`, `voice` và `unsupported`. Kịch bản phân tích dữ liệu vì vậy nhận **link công khai** (Google Sheets export CSV, Drive, GitHub raw) hoặc **ảnh chụp bảng**. Prompt nền tảng đã hướng dẫn model hỏi link.
- **Gửi ảnh:** tài liệu chỉ mô tả `sendPhoto.photo` là URL. Adapter thử **upload multipart** cho ảnh cục bộ như biểu đồ. **Cần thử với bot thật.** Nếu Zalo từ chối, cần thêm một endpoint công khai phục vụ ảnh, ví dụ qua trang Admin, rồi gửi URL đó.
- Mỗi tin văn bản và chú thích ảnh tối đa 2000 ký tự. Adapter tự chia nhỏ tin dài.
- `sendChatAction` chỉ có `typing`. `upload_photo` mới ghi "sắp ra mắt".
- Nhóm vẫn đang Beta. Bot chỉ thấy tin @nhắc bot hoặc tin trả lời bot.

## An toàn và việc còn mở

- **Bộ nhớ Hermes vẫn tắt.** Hermes lưu `MEMORY.md`/`USER.md` chung cho cả home nên mọi người dùng Zalo sẽ dùng chung. Muốn bật thì phải tách bộ nhớ theo người hoặc nhóm trước; xem đề xuất P0-3 trong [nghiên cứu sản phẩm](nghien-cuu-tro-ly.md).
- **Railway không chạy được Docker.** Nếu bật `assistant` trên Railway với backend `local`, code của model chạy chung container với token và khóa. Vì vậy `container_runtime` hiện vẫn ghép cấu hình `lab`, và adapter sẽ dừng với lỗi cấu hình nếu thiếu `platform_toolsets.zalo`. Muốn chạy trên cloud thì dùng VPS có Docker, hoặc backend `ssh`/`modal` của Hermes.
- Hermes không có cơ chế duyệt lệnh nguy hiểm qua Zalo (`allow_gateway_control=False`). Toolset mặc định cố ý không có `terminal`, `browser`, `delegation` hay `cronjob`. Thêm những toolset này chỉ khi đã có sandbox và luồng duyệt.
- Tin đã nhận nhưng vẫn ở trạng thái `admitted` trong `StateStore` (Hermes gộp vào lượt sau) sẽ không được gửi lại khi khởi động lại bot. Đây là lựa chọn cố ý, vì Zalo không có khóa idempotency.
