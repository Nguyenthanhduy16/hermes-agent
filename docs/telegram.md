# Chạy trợ lý trên Telegram (kênh phát triển)

Telegram dùng **adapter có sẵn của Hermes**, không cần viết transport riêng. Những gì phải giống nhau trên mọi kênh nằm trong plugin dùng chung **`assistant-core`** (`src/assistant_core/`, `plugins/assistant-core/`). Zalo ở chế độ `assistant` cũng đi qua plugin này.

| Lõi dùng chung | Hook Hermes | Ghi chú |
|---|---|---|
| Hạn mức lượt hỏi theo người/ngày và toàn bot/ngày | `pre_gateway_dispatch` | Lệnh `/…`, tin hệ thống và người chưa được cấp quyền không bị tính. Hết lượt thì bot báo lại |
| Chặn tool chưa tách dữ liệu theo người | `pre_tool_call` | Mặc định chặn `session_search`, `skill_manage`, `memory` |
| Chặn toolset cấp máy chủ | khi nạp plugin | Nếu cấu hình có `computer_use`, `connections`… thì mọi lượt bị từ chối |
| Telemetry từng lần gọi API và từng tool | `pre/post_api_request`, `api_request_error`, `post_tool_call` | Dữ liệu cho router SCER-AH. Không lưu nội dung tin nhắn |

## Các bước chạy

1. **Tạo bot mới** qua [@BotFather](https://t.me/BotFather) bằng `/newbot`, rồi lấy token.
   - Không dùng lại token của gateway Hermes cá nhân (`~/.hermes`). Hai tiến trình dùng chung một token sẽ tranh nhau nhận tin.
2. **Lấy user ID** của bạn bằng cách nhắn [@userinfobot](https://t.me/userinfobot).
3. Mở `.env`. Nếu file chưa có thì chạy `scripts/run.sh Setup` để tạo. Điền:
   ```dotenv
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_ALLOWED_USERS=<user_id>
   TELEGRAM_HOME_CHANNEL=<user_id>
   ```
   Có thể để trống `ZALO_BOT_TOKEN`. Khi đó chỉ kênh Telegram chạy.
4. Chạy `scripts/run.sh Update`. Lệnh này cài `assistant-core`, bật kênh Telegram và thêm toolset từ `config/hermes.assistant.example.yaml`.
5. Chạy `scripts/run.sh Model` để chọn provider/model và nhập API key. Cấu hình lưu trong `.runtime/hermes-home`, không đưa vào Git.
6. (Tùy chọn, nên làm) Chạy `docker pull nousresearch/hermes-sandbox:desktop`. Code do model sinh ra chạy trong container. Nếu không kéo trước, lần chạy code đầu tiên sẽ phải chờ tải image.
7. Chạy `scripts/run.sh Start`, rồi nhắn bot `/start` và một câu bất kỳ.

## Câu thử

- `Xin chào, bạn làm được gì?`
- `Tìm 3 tin AI mới nhất hôm nay, kèm link`
- Gửi file `sales.csv` kèm chú thích `vẽ doanh thu theo tháng`. Telegram nhận được file, Zalo thì không.
- Gửi ảnh chụp một bảng số liệu, kèm `tóm tắt bảng này`.
- Gửi tin nhắn thoại. Hermes tự chuyển giọng nói thành chữ.

## Xem telemetry

Dữ liệu nằm ở `.runtime/hermes-home/assistant-core/telemetry.sqlite3`. Trang quản lý và các script phân tích nên đọc qua các hàm trong `assistant_core.reporting`, vì các hàm này mở SQLite ở chế độ chỉ đọc:

```bash
PYTHONPATH=src .runtime/venv/bin/python -c "
from pathlib import Path; from assistant_core import reporting as r
db = Path('.runtime/hermes-home/assistant-core/telemetry.sqlite3')
print(r.runs(db)[:3]); print(r.models(db)); print(r.tools(db))"
```

Muốn đánh dấu một lần chạy là baseline hoặc ablation thì đặt `ASSISTANT_RUN_LABEL` (ví dụ `B1-frontier`) trước khi `Start`. Mỗi lần chạy cũng ghi lại commit Hermes và hash của `config.yaml`.

## Phiên bản Hermes

`scripts/run.sh` mặc định dùng `HERMES_SRC=~/.hermes/hermes-agent`, là bản cài trên máy (`scripts/run.sh Status` cho biết commit). Bản cloud trong `Dockerfile` vẫn ghim `93c9360a`. Test của `assistant-core` đã chạy trên `dce1e9b375`. Các hook dùng ở đây (`pre_gateway_dispatch`, `pre_tool_call`, `pre/post_api_request`) cũng có ở commit ghim, nhưng test chưa chạy trên commit đó.

## Chưa làm (P0 tiếp theo)

- Danh tính dùng chung giữa các kênh (`person_id`).
- Task bền vững và lệnh hủy.
- Bộ nhớ riêng từng người.
- Duyệt hành động.
- Harness benchmark chạy ngoài kênh cho các baseline B1–B3.
