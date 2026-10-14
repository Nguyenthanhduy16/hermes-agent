# Model LabCoach qua LiteLLM

Model đã chọn: `deepseek/deepseek-v4-flash`. Máy chủ LiteLLM cung cấp API tương thích OpenAI; **không dùng khóa này làm `OPENROUTER_API_KEY`**.

## Cấu hình Railway

Trong **Variables** của service bot, đặt các giá trị dưới đây rồi triển khai lại. Điền khóa từ file bàn giao trực tiếp vào `OPENAI_API_KEY`; không commit file đó, không gửi khóa vào chat. Giữ nguyên Volume, Bot Token, Google và quyền thành viên.

```dotenv
HERMES_PROVIDER=custom
HERMES_MODEL=deepseek/deepseek-v4-flash
HERMES_BASE_URL=https://litellm.vlearn.dev/v1
OPENAI_BASE_URL=https://litellm.vlearn.dev/v1
OPENAI_API_KEY=<điền riêng trong Railway Variables>
HERMES_REASONING_EFFORT=none
ZALO_CHAT_MAX_TOKENS=4096
ZALO_NLU_ENABLED=true
ZALO_NLU_TIMEOUT_SECONDS=30
ZALO_NLU_RESPONSE_FORMAT=json_object
ZALO_NLU_THINKING=disabled
ZALO_NLU_MAX_TOKENS=2048
ZALO_NLU_MAX_RETRIES=2
```

Hai URL cần giống nhau: Hermes dùng `HERMES_BASE_URL` để chọn máy chủ và đối chiếu `OPENAI_BASE_URL` trước khi dùng khóa. Provider phải là `custom` để cả hội thoại và nhận diện ý định hoạt động.

Đã kiểm tra ngày 14/10/2026: máy chủ liệt kê model đã chọn; câu chào và JSON mode có phản hồi. JSON Schema trả HTTP 400, nên chọn `json_object` rồi kiểm tra đủ trường, kiểu dữ liệu, ý định được phép và thông tin gốc tại code. Không tự hạ yêu cầu kiểm tra khi model trả sai.

`none`/`disabled` đã được máy chủ chấp nhận trong thử nghiệm; dùng cho câu ngắn để tránh mất token cho suy nghĩ trước phần trả lời. Giới hạn 4.096 token hội thoại và 2.048 token phân loại là mức trần, không phải lượng tiêu thụ cố định. Không đặt quá thấp. Nếu bật lại suy nghĩ cần kiểm thử lại câu trả lời rỗng và thời gian chờ.

## Hạn mức và lỗi

- Theo thông tin người cấp khóa: **30 USD mỗi 30 ngày, 300 request/phút, 500.000 token/phút**. Máy chủ LiteLLM quản lý hạn mức và tự gia hạn khóa; đây không phải cam kết miễn phí hay ngân sách được bộ đếm trong bot đảm bảo.
- HTTP 429: phân loại thử lại tối đa 2 lần sau lần đầu, chờ theo `Retry-After` hoặc 1 rồi 2 giây, cùng giới hạn 30 giây. Nếu thời gian chờ vượt giới hạn hoặc vẫn lỗi, tạm ngừng gọi và hướng dẫn dùng lệnh.
- Hội thoại dùng cơ chế thử lại sẵn có của Hermes, tối đa 3 lần gọi khi gặp HTTP 429 thông thường; tắt streaming và các vòng khôi phục kéo dài. Không chạy thêm một vòng thử lại ở adapter. Ngân sách lượt chạy 60 giây, timeout ngoài 90 giây; lỗi được đổi thành thông báo ngắn, không đưa lỗi máy chủ hoặc khóa ra Zalo.
- Hết ngân sách 30 ngày không nhất thiết hết lỗi sau vài giây. Kiểm tra Usage/ngân sách với người cấp khóa; bot không tự tăng hạn mức hoặc chuyển model.
- 401/403: kiểm tra cấu hình/quyền khóa. Nghi lộ khóa: liên hệ người cấp để đổi; không dán khóa lên nhóm.

## Kiểm tra sau cập nhật

Trên container: `/opt/hermes/venv/bin/python /app/scripts/nlu_check.py --runtime --live`. Lệnh gửi một câu giả lập tới model, không gọi Zalo hoặc Google Sheets.

Sau đó thành viên đã đăng ký thử `Xin chào`, `Giúp kiểm tra lịch của mình ngày kế tiếp nhé` và `/menu`; trong nhóm cần @bot. Giữ bot Windows tắt khi Railway đang chạy. Chỉ xác nhận luồng Zalo thật sau khi người dùng nhận được câu trả lời.
