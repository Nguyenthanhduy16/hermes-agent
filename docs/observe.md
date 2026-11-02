# Observe: theo dõi hành vi agent và đo chỉ số

Nhóm **Observe** trong Agent Console có hai trang, chỉ chủ hệ thống (system owner) xem được. Cả hai chỉ **đọc** dữ liệu, không bao giờ ghi vào cơ sở dữ liệu.

## Nguồn dữ liệu

- File `telemetry.sqlite3` do plugin `assistant-core` ghi, nằm trong thư mục dữ liệu của trợ lý (mặc định `$HERMES_HOME/assistant-core/`, hoặc `$ASSISTANT_DATA_DIR`).
- Bật bằng plugin `plugins/assistant-core` với `ASSISTANT_TELEMETRY=true` (mặc định đã bật), khởi động lại gateway rồi nhắn cho trợ lý một tin.
- Telemetry không lưu nội dung tin nhắn hay tham số công cụ, chỉ lưu tên model/công cụ, số token, thời gian và lỗi.
- Nếu chưa có file, trang sẽ báo "No agent telemetry has been recorded yet" kèm hướng dẫn bật.

## Agent activity: xem từng lượt (turn)

- Mỗi dòng là một lượt của agent: thời gian, nền tảng, mã phiên rút gọn, các model đã dùng, số bước gọi API, số lần gọi công cụ, token vào/ra, tổng thời gian, số lần thử lại, lỗi hoặc công cụ bị chặn, và trạng thái:
  - **OK**: không có vấn đề gì.
  - **Issues**: có lỗi nhưng lượt vẫn hoàn tất (ví dụ bị giới hạn tốc độ rồi thử lại thành công, công cụ lỗi hoặc bị chặn).
  - **Failed**: lần gọi model cuối cùng bị lỗi.
- Bộ lọc gồm khoảng thời gian (1/7/30/90 ngày), nền tảng, model, trạng thái (OK hoặc có lỗi) và phần đầu của mã phiên.
- Bấm vào một lượt để xem **từng bước** theo trình tự thời gian. Mỗi lần gọi model và mỗi lần gọi công cụ hiện thành một thanh: phần nhạt là thời gian chờ token đầu tiên (TTFB). Bên cạnh có lần thử lại, finish reason, token, lỗi và chi phí của bước đó. Nút **Export JSON** tải toàn bộ lượt về máy.
- Cách gom lượt: các bản ghi có cùng `session_id` và `turn_id` thuộc cùng một lượt. Công cụ thiếu `turn_id` được gắn vào lượt của lần gọi API (`api_request_id`) đã yêu cầu nó. Bản ghi cũ không có `turn_id` được gom theo phiên: nếu hai bản ghi cách nhau quá 120 giây thì tính là lượt mới.

## Analytics: chỉ số theo khoảng thời gian

- Chọn khoảng 24 giờ, 7, 30 hoặc 90 ngày. Ngày được tính theo giờ Việt Nam (UTC+7).
- Các thẻ chỉ số: số lượt, số lần gọi API (tính cả lần thử lại), tỉ lệ lỗi, độ trễ p50/p95 (chỉ tính các lần gọi thành công), TTFB p50, tổng token, tỉ lệ dùng cache và chi phí ước tính.
- Biểu đồ theo ngày: số lần gọi model và số token.
- Các bảng: theo model, theo công cụ (tỉ lệ lỗi, số lần bị chặn, thời gian trung bình và p95), theo nền tảng, và theo **run** (mỗi lần gateway khởi động, kèm nhãn `ASSISTANT_RUN_LABEL` và mã băm cấu hình). Đặt nhãn khác nhau cho mỗi cấu hình để so sánh baseline với router.
- **Export JSON** tải toàn bộ số liệu đang xem về máy, để lưu lại và đối chiếu khi benchmark.
- Mỗi bảng chỉ đọc tối đa 20.000 bản ghi cho mỗi khoảng thời gian. Nếu vượt quá, trang sẽ báo dữ liệu bắt đầu từ thời điểm nào.

## Chi phí: pricing.yaml

Chép `config/pricing.example.yaml` thành `pricing.yaml` và đặt cạnh `telemetry.sqlite3`, rồi sửa giá cho đúng (đơn vị là USD cho 1 triệu token):

```yaml
models:
  openai/gpt-4.1-mini: {input: 0.40, output: 1.60, cache_read: 0.10}
```

- `input` là token đầu vào không lấy từ cache, `output` là token đầu ra. `cache_read` và `cache_write` không bắt buộc; nếu bỏ trống thì tính theo giá `input`.
- Không có file thì trang hiện "Pricing unavailable" chứ không hiện $0. Nếu chỉ một số model có giá, chi phí được ghi là "partial" và trang liệt kê các model còn thiếu giá.
- File dạng symlink hoặc sai định dạng sẽ bị bỏ qua và trang có thông báo.

## Chế độ demo

`PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo` hiển thị dữ liệu giả lập cố định (Telegram, Zalo, web; 4 model; có thử lại, công cụ bị chặn, lượt thất bại) và không đọc dữ liệu thật.
