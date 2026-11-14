# Định tuyến mô hình theo từng bước

Khung định tuyến nằm trong plugin `assistant-core`
(`src/assistant_core/router.py`). Trước mỗi lần Hermes gọi mô hình trong vòng
lặp của tác tử, khung chọn mô hình **mạnh** hoặc **rẻ** cho đúng bước đó và ghi
lại quyết định. Mặc định tắt: không bật thì không có gì thay đổi.

Đây là phần khung để chạy các cấu hình đối chứng và thu dữ liệu. Thuật toán
đề xuất của đồ án sẽ cắm vào sau, cùng chỗ với các chính sách dưới đây.

## 1. Bật

Đặt trong `.env` rồi khởi động lại gateway:

```
ASSISTANT_ROUTER_POLICY=rule
ASSISTANT_ROUTER_STRONG_MODEL=<tên mô hình mạnh>
ASSISTANT_ROUTER_CHEAP_MODEL=<tên mô hình rẻ>
ASSISTANT_RUN_LABEL=luat
```

- Đặt **mô hình mặc định của Hermes bằng mô hình mạnh**. Khi khung gặp lỗi,
  bước đó chạy bằng mô hình mặc định.
- Mặc định hai mô hình phải cùng nhà cung cấp và cùng giao thức với phiên, vì
  khung chỉ đổi tên mô hình trong request. Mức rẻ ở nhà cung cấp khác: xem
  [mục 1b](#1b-mức-rẻ-ở-nhà-cung-cấp-khác).
- Giá trị sai (thiếu tên mô hình, tỷ lệ ngoài 0..1...) làm plugin dừng nạp và
  ghi lỗi vào log, thay vì âm thầm chạy sai cấu hình.

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `ASSISTANT_ROUTER_POLICY` | `off`, `always-strong`, `always-cheap`, `random`, `rule` | `off` |
| `ASSISTANT_ROUTER_STRONG_MODEL`, `ASSISTANT_ROUTER_CHEAP_MODEL` | Tên hai mô hình, bắt buộc khi bật | — |
| `ASSISTANT_ROUTER_STRONG_SHARE` | `random`: xác suất dùng mô hình mạnh ở mỗi bước | `0.5` |
| `ASSISTANT_ROUTER_SEED` | `random`: cùng seed và cùng mã bước cho cùng kết quả | `0` |
| `ASSISTANT_ROUTER_STRONG_FIRST_STEPS` | `rule`: số bước đầu của mỗi lượt dùng mô hình mạnh | `1` |
| `ASSISTANT_ROUTER_PROVIDER` | Chỉ định tuyến request của nhà cung cấp này | mọi nhà cung cấp |
| `ASSISTANT_ROUTER_CHEAP_PROVIDER` | Nhà cung cấp Hermes của mức rẻ, khi khác nhà cung cấp của phiên (ví dụ `deepseek`) | cùng nhà cung cấp |
| `ASSISTANT_ROUTER_PLATFORMS` | Chỉ định tuyến các nền tảng này, ví dụ `api_server` | mọi nền tảng |
| `ASSISTANT_ROUTER_STRONG_REQUEST`, `ASSISTANT_ROUTER_CHEAP_REQUEST` | JSON các trường request đặt thêm cho từng mức; `null` để bỏ một trường | không |

Ví dụ khi mô hình rẻ không nhận mức suy luận mà Hermes đã chuẩn bị cho mô hình
mặc định:

```
ASSISTANT_ROUTER_CHEAP_REQUEST={"reasoning": {"effort": "low"}}
```

Các trường `model`, `messages`, `input`, `instructions`, `system`, `tools`,
`stream` không được đặt qua đây.

## 1b. Mức rẻ ở nhà cung cấp khác

Ví dụ mạnh là `gpt-6.1-sol` qua `openai-codex` (gói ChatGPT Plus), rẻ là
`deepseek-flash` qua API DeepSeek:

```
ASSISTANT_ROUTER_STRONG_MODEL=gpt-6.1-sol
ASSISTANT_ROUTER_CHEAP_MODEL=deepseek-flash
ASSISTANT_ROUTER_CHEAP_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
```

Middleware `llm_request` chạy khi request đã dựng xong cho nhà cung cấp của
phiên (endpoint, khóa, định dạng Responses hay Chat Completions đã cố định), nên
không chuyển được một bước sang nhà cung cấp khác. Khi đặt
`ASSISTANT_ROUTER_CHEAP_PROVIDER`, khung quyết định ở **đầu mỗi bước**, ngay
sau `prepare_iteration` của Hermes và trước khi request được dựng, rồi chuyển
runtime của agent bằng chính cơ chế fallback của Hermes:

| Mức | Hàm Hermes | Tác dụng |
|---|---|---|
| rẻ | `agent.route_binding.bind_route_entry` với `{provider, model}` | Hermes tự lấy client và khóa, chọn giao thức, giữ nguyên lịch sử `messages` |
| mạnh | `agent.agent_runtime_helpers.restore_primary_runtime` | Về runtime chính của phiên; Hermes cũng gọi hàm này đầu mỗi lượt |

Hermes không có hook nào ở đầu bước mà đưa đối tượng agent cho plugin, nên
`assistant_core.provider_switch.install` bọc đúng một hàm pha
(`agent.conversation_loop.prepare_iteration`). Phần này kiểm tra trên commit
Hermes `dce1e9b3` (`tests/test_hermes_provider_switch.py`); nâng Hermes thì
chạy lại `scripts/run.sh TestHermes`. Middleware vẫn ghi quyết định cho
telemetry nhưng không sửa request. Khi chuyển lỗi, bước đó chạy bằng runtime
chính; khi mô hình chính đang bị giới hạn lượt, Hermes giữ mức rẻ và lý do ghi
thêm `primary unavailable, stayed cheap`.

Đã chạy thật (12/11/2026, `random` 0,5): các bước xen kẽ `deepseek` ↔
`openai-codex` trong cùng phiên, công cụ chạy bình thường. Cache của mỗi nhà
cung cấp vẫn còn sau một bước ở nhà cung cấp kia (bước Sol sau bước DeepSeek
vẫn đọc khoảng 4.900 token từ cache); chỉ phần hội thoại mới từ lần gọi trước
của nhà cung cấp đó là không có cache.

## 2. Các chính sách

| Chính sách | Cách chọn | Dùng để |
|---|---|---|
| `always-strong` | Mọi bước dùng mô hình mạnh | Mốc chất lượng cao nhất |
| `always-cheap` | Mọi bước dùng mô hình rẻ | Mốc chi phí thấp nhất |
| `random` | Mỗi bước dùng mô hình mạnh với xác suất `STRONG_SHARE` | Đối chứng, và thu dữ liệu "cùng trạng thái, khác mô hình" |
| `rule` | Bước đầu của lượt và bước ngay sau một lần gọi công cụ lỗi hoặc quá giờ dùng mô hình mạnh; còn lại dùng mô hình rẻ | Đối chứng "luật đơn giản" |
| `first-strong` | `STRONG_FIRST_STEPS` bước đầu của lượt dùng mô hình mạnh, sau đó rẻ, không nâng lên khi lỗi | Mốc First-Large của Budget-Aware |

Chính sách nhìn thấy các đặc trưng sau, không có nội dung tin nhắn. Đặc trưng
của bước t chỉ dùng sự kiện đã xong trước bước t, và đều thay đổi theo bước, nên
không đặc trưng nào là hằng số của task. Bộ này được chốt sau thảo luận hai vòng
với Claude và Codex (13/11/2026), xem [thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md#bước-4-xây-và-khóa-bộ-định-tuyến-đề-xuất-gói-f).

| Đặc trưng | Ý nghĩa | Dùng cho |
|---|---|---|
| `step` | Bước thứ mấy trong lượt | Mô hình chính |
| `prev_tool_errors` | Số lần gọi công cụ lỗi từ bước trước (không tính quá giờ), chặn ở 2 | Mô hình chính |
| `prev_tool_timeout` | 1 nếu một lần gọi công cụ từ bước trước bị quá giờ | Mô hình chính |
| `prev_tool_duration_log` | log1p(giây) của lần gọi công cụ lâu nhất từ bước trước | Mô hình chính |
| `short_exec_streak` | Số bước liên tiếp trước đó mà mọi lệnh chạy code (`execute_code`, `terminal`) đều ok và dưới 3 s; chặn ở 5 | Mô hình chính |
| `ctx_growth` | sign(Δ)·log1p(abs(Δ)), Δ = số ký tự request hiện tại trừ số ký tự ở bước đầu của lượt | Mô hình chính |
| `prev_choice` | Mức đã chọn ở bước trước | Mô hình chính |
| `elapsed_log`, `tool_time_cum`, `cum_tool_failures`, `error_streak` | Thời gian từ bước đầu của lượt, tổng thời gian công cụ, tổng lỗi và quá giờ trong lượt, số bước liên tiếp đi sau lỗi | Ablation |
| `prev_tools`, `last_tool`, `messages`, `chars`, `session_step` | Số lần gọi công cụ, tên công cụ cuối, số tin, số ký tự, bước trong phiên | Chỉ ghi lại để phân tích |

Lỗi công cụ lấy từ trạng thái Hermes báo qua hook `post_tool_call` (ví dụ code
chạy lỗi, lệnh thoát khác 0). Công cụ bị chặn cũng tính là lỗi. Trạng thái
`timeout` được đếm riêng.

Thêm chính sách mới:

```python
from assistant_core.router import CHEAP, STRONG, Decision, register_policy

def my_policy(features, settings):
    risky = features["prev_tool_errors"] or features["step"] == 1
    return Decision(STRONG if risky else CHEAP, "lý do ngắn", score=0.8)

register_policy("my-policy", my_policy)   # rồi đặt ASSISTANT_ROUTER_POLICY=my-policy
```

## 3. Dữ liệu ghi lại

Mỗi lần gọi mô hình là một dòng trong bảng `api_calls` của
`<assistant_dir>/telemetry.sqlite3`. Khi bước đó được định tuyến, dòng có thêm:

| Cột | Nội dung |
|---|---|
| `model` | Mô hình thật sự được gọi ở bước này |
| `default_model` | Mô hình mặc định của phiên |
| `route_policy`, `route_choice`, `route_reason`, `route_score` | Chính sách, mức đã chọn (`strong`/`cheap`), lý do, điểm (nếu chính sách có) |
| `route_switched` | 1 nếu khác mức của bước trước |
| `route_features` | JSON các đặc trưng ở bảng trên |

Bảng `router_runs` lưu cấu hình định tuyến của từng lần chạy gateway (chính
sách, hai mô hình, seed, tỷ lệ).

- **Agent Console → Observe**: mỗi bước gọi mô hình hiện tên mô hình đã dùng
  và dòng `routed cheap (routine step)`.
- **Benchmark**: bảng so sánh có thêm hai dòng `Cheap-model steps` (tỷ lệ bước
  dùng mô hình rẻ) và `Model switches per task` (số lần đổi mô hình mỗi tác
  vụ). Chi phí tính theo mô hình thật của từng bước.
- **Truy vấn trực tiếp** cho một phiên:

```bash
PYTHONPATH=src .runtime/venv/bin/python -c "
from pathlib import Path; from assistant_core import reporting as r
for s in r.session_steps(Path('.runtime/hermes-home/assistant-core/telemetry.sqlite3'), '<SESSION_ID>'):
    print(s['api_call_count'], s['model'], s['route_choice'], s['route_reason'], s['input_tokens'], s['cache_read_tokens'])"
```

## 4. Chạy các cấu hình đối chứng

Mỗi cấu hình là một lần khởi động gateway với chính sách và nhãn riêng, rồi
chạy cùng một suite:

| Lần | `ASSISTANT_ROUTER_POLICY` | `ASSISTANT_RUN_LABEL` |
|---|---|---|
| 1 | `always-strong` | `luon-manh` |
| 2 | `always-cheap` | `luon-re` |
| 3 | `rule` | `luat` |
| 4 | `random` | `ngau-nhien` |

```bash
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench run --suite dabench-pilot --label luon-manh
PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench compare <RUN_1> <RUN_2> <RUN_3> <RUN_4>
```

Giữ nguyên mọi thiết lập khác (bộ công cụ, sandbox, mô hình mặc định) giữa các
lần chạy. Dùng `always-strong` thay vì `off` cho mốc "luôn mạnh", để các bước
cũng được ghi quyết định như những cấu hình còn lại.

## 5. Giới hạn

- **Một lần gọi mô hình cho mỗi bước.** Hermes không cho gọi mô hình rẻ rồi
  gọi lại mô hình mạnh trong cùng một bước. Muốn nâng lên mô hình mạnh thì
  thực hiện ở bước kế tiếp.
- **Gọi lại do lỗi mạng giữ nguyên quyết định** của bước đó.
- **Đổi mô hình làm mất prompt cache** của nhà cung cấp. Cột
  `cache_read_tokens` cho thấy mức mất ở từng bước.
- Thống kê riêng của Hermes (dashboard Analytics) vẫn tính theo mô hình mặc
  định của phiên. Dùng telemetry của `assistant-core` cho số liệu đồ án.
- Các lần gọi phụ của Hermes (nén ngữ cảnh, đặt tiêu đề, đọc ảnh) không đi qua
  khung này.
- Trạng thái các bước nằm trong bộ nhớ của gateway. Khởi động lại gateway giữa
  một lượt thì bước kế tiếp được coi là bước đầu.

## 6. Kiểm tra

- `scripts/run.sh Test`: test giả lập cho cấu hình, chính sách, middleware và
  telemetry (`tests/test_assistant_router.py`).
- `scripts/run.sh TestHermes`: nạp plugin bằng Hermes đã cài và gửi request qua
  chuỗi middleware thật của Hermes (`tests/test_hermes_router.py`), không gọi
  mô hình.
- Chưa có lần chạy nào với mô hình thật. Việc cần xác nhận đầu tiên trên máy
  thực nghiệm: nhà cung cấp có nhận tên mô hình rẻ trong cùng phiên không, và
  có báo `cache_read_tokens` không.
