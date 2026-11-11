# Thí nghiệm và benchmark (Experiments & Benchmarks)

Tài liệu cho chủ bot khi nghiên cứu định tuyến model theo bước (SCER-AH, xem
[nghien-cuu-tro-ly.md](nghien-cuu-tro-ly.md) và
[nghien-cuu-quan-ly-cloud.md](nghien-cuu-quan-ly-cloud.md)). Mục tiêu: mỗi lần
chạy gateway hay benchmark đều gắn với **đúng cấu hình đã dùng**, số liệu lưu
bền, và so sánh được các cấu hình với nhau (ví dụ B1 rẻ, B2 có định tuyến, B3
model mạnh).

Có hai trang trong Agent Console, nhóm **Observe**:

| Trang | Dữ liệu | Dùng để |
|---|---|---|
| **Experiments** | `telemetry.sqlite3` do plugin assistant-core ghi khi gateway chạy với người dùng thật | So sánh lưu lượng thật giữa các lần khởi động gateway / các nhãn |
| **Benchmarks** | `bench.sqlite3` do bộ chạy benchmark ghi | Chạy cùng một bộ tác vụ lặp lại được với các cấu hình khác nhau, chấm đạt/không đạt tự động |

Cả hai file nằm trong thư mục dữ liệu assistant-core
(`ASSISTANT_DATA_DIR`, mặc định `<HERMES_HOME>/assistant-core`).

## 1. Cấu hình được ghi lại như thế nào

Mỗi lần gateway khởi động, plugin assistant-core tạo một **run** mới:

- `config_hash` = 16 ký tự đầu của sha256 nội dung `config.yaml`. Đổi bất kỳ
  dòng nào trong `config.yaml` là ra hash mới.
- `hermes_rev` = commit Hermes đang chạy.
- `label` = giá trị `ASSISTANT_RUN_LABEL` (chữ, số, khoảng trắng, `. _ : + -`,
  tối đa 60 ký tự; ký tự khác bị bỏ đi, không làm tắt plugin).
- Một **ảnh chụp cấu hình** (bảng `config_snapshots`) theo danh sách cho phép:
  model mặc định, provider, *chỉ tên máy* của `base_url`, temperature,
  reasoning effort, max_tokens, `agent.max_turns`, định tuyến model
  (`smart_model_routing`, `auxiliary.*`, `fallback_providers`, `delegation`),
  `compression`, toolset theo nền tảng, terminal backend, cờ memory, số skill,
  plugin đang bật, số MCP server.

Ảnh chụp **không bao giờ** chứa API key, token, mật khẩu, URL có thông tin
đăng nhập/đường dẫn/query, prompt, personality hay header tùy chỉnh. Giá trị
trông giống khóa (ví dụ `sk-...`, chuỗi ngẫu nhiên dài) bị thay bằng
`[redacted]`.

Lưu ý: run cũ (trước khi có tính năng này) hiển thị "Not captured" và không
so sánh được phần cấu hình.

## 2. Đặt nhãn cho một thí nghiệm

Quy trình cho mỗi cấu hình cần đo:

1. Sửa `config.yaml` của Hermes home (model, định tuyến, compression...).
2. Đặt nhãn trong môi trường của gateway, ví dụ trong `.env`:

   ```
   ASSISTANT_RUN_LABEL=B2-routing
   ```

3. **Khởi động lại gateway** (`scripts/run.sh` hoặc dịch vụ trên cloud). Cấu
   hình và nhãn chỉ được đọc khi khởi động.
4. Để bot phục vụ như bình thường, hoặc chạy benchmark (mục 4).

Gợi ý đặt nhãn: `B1-cheap`, `B2-routing`, `B3-strong`, `ablation-no-compress`,
`B2-routing-v2`. Cùng một nhãn có thể gồm nhiều lần khởi động; trang
Experiments gộp chúng lại và cảnh báo nếu một nhãn trộn nhiều cấu hình khác nhau.

## 3. Trang Experiments

- Bảng **Gateway runs**: nhãn, thời điểm bắt đầu, thời gian hoạt động, số lượt
  (turn), số lần gọi API, token, lỗi API, lỗi công cụ, `config_hash`.
- Chọn 2–4 run hoặc nhãn rồi bấm **Compare** để xem cạnh nhau: số lượt, API
  calls/lượt, token/lượt, độ trễ p50/p95 của từng lần gọi, tỉ lệ lỗi, tỉ lệ lỗi
  công cụ, chi phí ước tính, các model được dùng và **bảng khác biệt cấu hình**
  (chỉ những thiết lập khác nhau).
- Trang chỉ đọc, mở SQLite ở chế độ `mode=ro`.

So sánh lưu lượng thật chỉ có ý nghĩa khi khối lượng và loại câu hỏi tương tự
nhau. Để so sánh công bằng, dùng benchmark.

## 4. Benchmark

### Kết nối tới Hermes API server

Benchmark gửi từng tác vụ tới `/v1/chat/completions` của Hermes API server
(giống trang Chat của console). Đặt trong môi trường của **console**:

```
HERMES_BENCH_API_URL=http://127.0.0.1:8642/v1     # hoặc dùng HERMES_CONSOLE_CHAT_API_URL
API_SERVER_KEY=...                                 # hoặc HERMES_BENCH_API_KEY / HERMES_CONSOLE_CHAT_API_KEY
HERMES_BENCH_MODEL=hermes                          # tùy chọn
```

Quy tắc: `https://` bắt buộc trừ khi là localhost; không chứa user/mật khẩu,
query hay đường dẫn khác `/v1`. Khóa chỉ đọc từ biến môi trường, không bao giờ
lưu vào kết quả. Nếu chưa cấu hình, trang Benchmarks hiện hướng dẫn và không có
nút chạy.

Benchmark đo **gateway đang chạy**. Trang sẽ cảnh báo nếu `config.yaml` đã đổi
sau khi gateway khởi động — khi đó hãy khởi động lại gateway trước khi chạy.

### Chạy trên console

**Benchmarks → Start benchmark run**: chọn suite, nhãn (nên trùng nhãn gateway),
số lần lặp (1–5) và số request song song (1–4). Mỗi tác vụ là **một yêu cầu
model thật, tốn quota và tiền**. Không tự động thử lại: request lỗi được ghi là
lỗi. Mỗi lúc chỉ có một run; có thể **Cancel** (request đang chạy sẽ hoàn tất,
không gửi request mới). Run chạy trong luồng nền của console; nếu console khởi
động lại giữa chừng, run đó được đánh dấu `failed` kèm lý do.

### Chạy bằng dòng lệnh

```bash
export HERMES_HOME=.runtime/hermes-home
export HERMES_BENCH_API_URL=http://127.0.0.1:8642/v1 API_SERVER_KEY=...
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench suites
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench run --suite assistant-basics --label B1-cheap --repeats 2 --concurrency 2
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench list
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench show <RUN_ID>
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench compare <RUN_A> <RUN_B> [<RUN_C> <RUN_D>]
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench cancel <RUN_ID>
```

Tùy chọn chung: `--home` (mặc định `HERMES_HOME` hoặc `~/.hermes`),
`--data-dir` (mặc định `ASSISTANT_DATA_DIR` hoặc `<home>/assistant-core`).
`run --api-url` ghi đè URL (khóa vẫn lấy từ môi trường); `--no-responses` không
lưu đoạn trích câu trả lời. Ctrl+C khi đang chạy = hủy run.

### Mỗi run lưu gì

`bench_runs`: suite (kèm mã băm nội dung suite), nhãn, trạng thái
(`queued|running|done|cancelled|failed`), thời điểm, `config_hash` của
`config.yaml` lúc bắt đầu, `config_hash` của gateway đang chạy, ảnh chụp cấu
hình, máy đích, số lần lặp, song song, tổng đạt/lỗi/token.

`bench_results` (mỗi tác vụ × lần lặp): mức L1–L3, đạt/không, chi tiết từng
check, độ trễ, token theo `usage` của phản hồi, model trả về, mã lỗi
(`http_500`, `timeout`, `connection_error`, `invalid_response`, `empty_reply`),
độ dài câu trả lời, **đoạn trích tối đa 2000 ký tự** (đã che chuỗi giống khóa),
và `session_id` Hermes của tác vụ.

Nhờ `session_id`, nếu telemetry bật, trang chi tiết lấy thêm **số lần gọi API
mỗi tác vụ** và model của từng bước từ `telemetry.sqlite3` — đúng số liệu cần
cho nghiên cứu định tuyến theo bước.

### So sánh

Chọn 2–4 run đã xong → **Compare**: tỉ lệ đạt tổng và theo L1/L2/L3, độ trễ
trung vị và p95, token/tác vụ, API calls/tác vụ, chi phí/tác vụ, số lỗi và bảng
khác biệt cấu hình. Giá trị tốt nhất mỗi hàng được tô xanh. Trang cảnh báo nếu
các run dùng phiên bản suite khác nhau.

Gợi ý: chạy mỗi cấu hình với `--repeats 2` trở lên để thấy độ dao động; so sánh
L3 (tác vụ dài, nhiều bước) là nơi định tuyến khác biệt rõ nhất.

## 5. Viết suite riêng

Suite có sẵn: `config/bench/assistant-basics.yaml` (14 tác vụ tiếng Việt và
tiếng Anh, L1–L3, không cần công cụ) và `config/bench/tool-use.yaml` (7 tác vụ
cần web search và chạy code). Suite riêng đặt ở `<HERMES_HOME>/benchmarks/*.yaml`
(tên không được trùng suite có sẵn). Trên server có thể đổi thư mục suite có sẵn
bằng `HERMES_BENCH_BUNDLED_DIR`.

```yaml
name: my-suite                  # a-z, 0-9, '-' ; tối đa 48 ký tự
description: Mô tả ngắn         # tùy chọn, tối đa 500 ký tự
tasks:                          # 1..300 tác vụ
  - id: tom-tat-email           # duy nhất trong suite
    level: L2                   # L1 đơn giản, L2 nhiều bước, L3 dài/khó/nhiều công cụ
    category: writing           # tùy chọn
    timeout_s: 120              # tùy chọn, 5..1800, mặc định 120
    prompt: >-
      Tóm tắt email sau thành 3 gạch đầu dòng ...
    checks:                     # tùy chọn, tối đa 10; tất cả phải đạt
      - {type: contains, value: "Hà Nội"}            # không phân biệt hoa thường
      - {type: contains, value: ["7", "bảy"]}        # đạt nếu chứa một trong các chuỗi
      - {type: not_contains, value: "xin lỗi"}
      - {type: regex, value: "\\d{4}"}                # Python re.search
      - {type: max_latency_s, value: 30}
      - {type: min_length, value: 200}                # số ký tự
      - {type: json_valid}                             # cả câu trả lời là JSON (cho phép ```json)
      - {type: answers, value: {mean_fare: "34.65"}}   # đáp án dạng @ten[giá trị], xem mục 9
```

Giới hạn: file tối đa 1 MB, tối đa 50 file mỗi thư mục, prompt tối đa 8000
ký tự, mỗi run tối đa 500 request (số tác vụ × số lần lặp). Suite sai định dạng
được liệt kê kèm lỗi và bị bỏ qua. Câu trả lời trống luôn tính là không đạt.

Check nên lỏng: bắt câu trả lời sai/thiếu chứ không chấm văn phong. Với tác vụ
web, kiểm tra cấu trúc (có link nguồn, có số liệu) thay vì sự kiện cụ thể vì
nội dung web thay đổi theo thời gian.

## 6. Chi phí ước tính

Tạo `<ASSISTANT_DATA_DIR>/pricing.yaml` (USD cho mỗi 1 triệu token):

```yaml
anthropic/claude-sonnet-4.5: {input: 3.0, output: 15.0, cache_read: 0.3}
google/gemini-2.5-flash: {input: 0.3, output: 2.5}
```

`cache_read` mặc định bằng giá input. Model không có giá hiển thị "No price",
không bao giờ hiển thị $0. Tên model khớp chính xác, không phân biệt hoa thường,
hoặc theo phần sau dấu `/` cuối cùng.

## 7. Chế độ demo

`hermes_zalo.admin_cli demo` hiển thị dữ liệu giả: run, ảnh chụp cấu hình,
suite. "Start benchmark run" trong demo chỉ mô phỏng tiến độ trong bộ nhớ,
không gọi model, không ghi file.

## 8. Giới hạn hiện tại

- Benchmark gửi kèm một system message ngắn chứa mã run/tác vụ/lần lặp để mỗi
  tác vụ có phiên Hermes riêng (API chat-completions suy ra phiên từ system
  prompt + tin nhắn đầu). Phần này nối thêm vào system prompt của Hermes.
- Khi hủy, request đang chạy vẫn chạy tới khi xong hoặc hết `timeout_s`.
- Token theo tác vụ lấy từ `usage` của API server (tổng của cả phiên). Số lần
  gọi API và chi phí chính xác theo model cần telemetry bật trên cùng thư mục
  dữ liệu.
- Check `regex` do chủ bot tự viết; tránh mẫu quá phức tạp (chạy trên tối đa
  20.000 ký tự đầu của câu trả lời).

## 9. InfiAgent-DABench

[InfiAgent-DABench](https://github.com/InfiAgent/InfiAgent/tree/main/examples/DA-Agent)
(Hu và cộng sự, ICML 2024) gồm 257 câu hỏi phân tích dữ liệu trên 52 file CSV,
chia ba mức dễ / trung bình / khó, đáp án dạng đóng `@ten_dap_an[giá trị]`.
Dữ liệu không nằm trong repo này; tải về rồi tạo suite bằng bộ chuyển đổi.

### Tạo suite

```bash
git clone --depth 1 https://github.com/InfiAgent/InfiAgent ~/datasets/InfiAgent
export HERMES_HOME=.runtime/hermes-home
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.dabench build \
  --data ~/datasets/InfiAgent/examples/DA-Agent/data
```

Lệnh ghi vào `<HERMES_HOME>/benchmarks`:

| File | Nội dung |
|---|---|
| `dabench-dev.yaml` | Tập phát triển (mặc định 40%): dùng để xây và chỉnh bộ định tuyến |
| `dabench-test.yaml` | Tập kiểm tra: chỉ dùng cho đánh giá cuối |
| `dabench-pilot.yaml` | 9 câu lấy từ tập phát triển, 3 câu mỗi mức, để ước tính chi phí trước |
| `dabench-manifest.json` | Seed, file và mã câu hỏi của từng tập, mã băm dữ liệu gốc, câu bị bỏ qua |

- **Chia theo file CSV.** Mọi câu hỏi về cùng một file nằm chung một tập, nên
  những gì rút ra từ một file ở tập phát triển không lọt sang tập kiểm tra.
  Trong ràng buộc đó, số câu mỗi mức được giữ cân bằng.
- **Lặp lại được.** Cùng `--seed` (mặc định 2026) cho ra đúng cùng bộ câu.
- Mức dễ / trung bình / khó thành `L1` / `L2` / `L3`; `category` là khái niệm
  đầu tiên của câu hỏi; mã tác vụ là `da-<id gốc>`.
- Lệnh không ghi đè suite đã có, vì kết quả các run cũ đang tham chiếu tới
  chúng. Thêm `--force` nếu thật sự muốn tạo lại.

Tùy chọn: `--dev-fraction 0.4`, `--per-level N` (lấy mẫu tối đa N câu mỗi mức
trong mỗi tập), `--pilot 9` (0 để bỏ), `--timeout 900` (giây cho mỗi câu),
`--container-dir /data/dabench`, `--out DIR`.

### Cho sandbox đọc được file CSV

Prompt của mỗi câu chỉ tới `/data/dabench/<tên file>`. Gắn thư mục bảng ở chế
độ chỉ đọc trong `config.yaml` của Hermes rồi khởi động lại gateway:

```yaml
terminal:
  docker_volumes:
    - "/home/<bạn>/datasets/InfiAgent/examples/DA-Agent/data/da-dev-tables:/data/dabench:ro"
```

Mặc định nền tảng `api_server` của Hermes có gần đủ bộ công cụ, gồm cả web.
Khi chạy benchmark nên cố định `platform_toolsets.api_server` (ví dụ
`code_execution`, `file`, `todo`) và **bỏ `web`**: đề và đáp án DABench công
khai trên GitHub, agent có thể tìm thấy thay vì tự tính. Giữ nguyên danh sách
này cho mọi cấu hình cần so sánh.

### Chạy và đọc kết quả

```bash
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench run --suite dabench-pilot --label luon-manh
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench show <RUN_ID>
```

`show` in thêm ba chỉ số của bài báo, tính trên các câu đã được chấm:

| Chỉ số | Cách tính |
|---|---|
| by question | Tỷ lệ câu đúng **mọi** đáp án con |
| proportional by sub-question | Trung bình tỷ lệ đáp án con đúng của từng câu |
| by sub-question | Tổng đáp án con đúng / tổng đáp án con |

### Cách chấm và điểm khác với bài báo

Check `answers` làm đúng như `eval_closed_form.py` của tác giả: đọc mọi cụm
`@ten[giá trị]` trong câu trả lời (tên lặp lại thì lấy cụm cuối), so khớp chính
xác chuỗi, hoặc so số với sai số dưới 1e-6. Chữ được phân biệt hoa thường;
thêm `ignore_case: true` nếu muốn nới.

Ba điểm khác với quy trình gốc cần ghi rõ khi báo cáo số liệu:

- Định dạng đáp án nằm ngay trong prompt và câu trả lời được chấm nguyên
  trạng. Quy trình gốc không đưa định dạng cho agent mà dùng một LLM
  (`reformat.py`) viết lại câu trả lời trước khi chấm.
- Agent đọc file CSV từ đường dẫn trong sandbox thay vì file tải lên.
- Request lỗi hoặc quá thời gian tính là sai trong `Pass rate`. Ba chỉ số ở
  trên bỏ qua các câu không có câu trả lời, giống script gốc; nên báo cáo kèm
  số câu đã chấm.
