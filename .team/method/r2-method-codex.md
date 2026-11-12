# Vòng 2 — method-codex

## 1. Đồng ý

Đồng ý: MVP hai tầng, logistic, bỏ entropy, harness ổn định, kiểm toán nhãn, chia tập theo họ, skill có duyệt.

## 2. Bất đồng và quyết định

**a. Chốt plugin middleware, proxy dự phòng.** Sửa lựa chọn Vòng 1 sau khi mở mã tại `H=/home/duy/.hermes/hermes-agent`:

- `H/agent/turn_api_request.py:145` áp `llm_request`, dùng payload mới ở dòng 151; `H/hermes_cli/middleware.py:88` cho thay request; `H/hermes_cli/plugins.py:937` đăng ký middleware. Plugin mới sửa `request.model`, không cần sửa core cho MVP. Proxy dự phòng nếu runtime thiếu middleware; phải truyền state/ID.
- Giới hạn cùng endpoint/provider/api_mode, model tương thích tool/schema/reasoning, context chung nhỏ nhất; các tùy chọn được dựng trước middleware (`H/agent/turn_api_request.py:121`). Ghim runtime, kiểm thử model thực nhận. Model gốc đặt Frontier để lỗi middleware quay về cấu hình này, không sửa `agent.model` toàn cục.
- Bác cascade gọi hai lần trong `llm_execution`: `next_call` single-use (`H/hermes_cli/middleware.py:179`). Chốt lỗi tool/schema quan sát được → Frontier ở bước agent kế, tối đa một lần cứu. Không bao phủ mọi lỗi JSON/ngữ nghĩa.
- Hook vẫn báo `agent.model` (`H/agent/turn_response_intake.py:75`), nhưng có `response_model:88`. Ghi thêm chosen-model theo request-ID; tính phí model thực nhận. `src/assistant_core/reporting.py:213` đã ưu tiên response_model; không phải mọi view đều vậy.

**b. Chốt ngưỡng q_F và biên δ.** Argmin của Claude cần C_fail và xác suất cứu lỗi chưa đủ dữ liệu; λ có thể đổi chất lượng lấy tiền thiếu kiểm soát. Công thức đó còn tính input không-cache rồi cộng mất-cache lần nữa. Dùng C_k tổng USD dự báo từ token uncached/cache-read/cache-write/output không trùng nhau; chuyển model/prefix đã phản ánh trong cache-hit. δ là biên USD chống dao động, không phải biên TSR; τ,δ chọn trên dev. Không cần L_remain.

**c. Chốt nhị phân.** q_F=P(checker cục bộ đạt|state,harness); không giả định ba tầng lồng nhau. Replay tiền tố gồm messages, file/sandbox, skill/tool phiên bản; lấy mẫu từ quỹ đạo Fast lẫn Frontier. Không bắt cùng tool Frontier; AST/exit 0 chưa đủ. Checker kiểm bất biến, số liệu/artifact với sai số theo tác vụ; bước không kiểm chứng được để nhãn unknown. Kiểm toán ngẫu nhiên phân tầng cả nhãn đạt/trượt: chạy tiếp hai nhánh Fast/Frontier với cùng chính sách hậu tố, ghi xác suất lấy mẫu, báo sai lệch nhãn và khoảng tin cậy. Gold checker chỉ gán nhãn/chấm; runtime chỉ dùng tín hiệu quan sát được, không đáp án test.

**d. Harness đầu lượt.** `pre_llm_call` được thu ở `H/agent/turn_context.py:1172`, tiêm context vào user message (`:797`). Chọn Skill/Toolset một lần theo lượt; chỉ Tier đổi từng bước, veto giữ nguyên schema. User-message vẫn thuộc tiền tố cache: không bảo đảm “không phá cache”. Nếu cần mở toolset để cứu, ghi một chuyển harness rõ ràng và tính lại cache; MVP dùng tập đủ cho cả hai tầng.

**e. Skill ngoại tuyến có duyệt.** `src/assistant_core/settings.py:12` chặn skill_manage. Chỉ rút skill từ train, kiểm thử/duyệt, khóa kho trước dev/test; không học online. Router nhận lỗi từ hook riêng đọc result (`H/agent/tool_executor.py:289`); assistant-core hiện loại result (`src/assistant_core/hermes_plugin.py:142`).

## 3. Pseudo-code chốt

```text
input: request, state[session,turn,request_id], kho skill khóa, budget
đầu lượt: h := chọn skill/toolset; giữ schema/guardrail/sandbox
mỗi request mới:
  x := pha đo được, token, lỗi trước, skill-match/lịch sử, cur
  q := logistic_L2(x) học trên train; thiếu trạng thái => Frontier
  C_F,C_R := chi phí dự báo theo cache/provider/prefix và output theo pha
  nếu lỗi cần cứu hoặc q<τ: k := Frontier
  ngược lại nếu cur=Fast hoặc C_F+δ<C_R: k := Fast
  còn lại: k := Frontier
  giữ lựa chọn khi retry; gọi một lần qua request.model
  ghi chosen/response-model, token/cache, latency, lỗi, budget
  lỗi Fast => cứu bước kế; cứu thất bại hoặc hết lượt/tiền => dừng
sau train: cập nhật q/skill ngoại tuyến; sau test: chỉ báo cáo
```

## 4. Thực nghiệm chốt

100 tác vụ: 20 train/pilot, 20 dev, 60 test gồm dữ liệu công khai; tách họ/dataset trước tạo biến thể. Warm-start thử riêng biến thể họ train, không gộp kết quả tổng quát. Thêm checker số/artifact vì `src/assistant_core/bench.py:68` chưa có.

Bốn cấu hình chung harness: Fast, Frontier, luật+cascade, đề xuất. Ba ablation riêng: bỏ harness-features, tiêm-skill, chi phí-cache; dùng 20 test chọn trước. Chính: USD/thành-công, TSR, p95; phụ: Brier, recovery, số chuyển. Tổng tiền gồm thất bại/retry/nhãn/học-skill. Bootstrap ghép cặp theo họ/tác vụ; lặp ba lần Frontier/đề xuất, không coi lần lặp độc lập. Báo khoảng tin cậy, không kết luận tương đương nếu thiếu lực thống kê.

Trần thiết kế 100 USD, dự phòng 20%; pilot dự toán trước mở test: 540 lượt đánh giá chính/ablation/lặp, cộng gán nhãn. Nếu vượt, giảm số lượt theo kế hoạch khóa trước; Jev/random chỉ thêm khi còn ngân sách. Tuần 1–2 checker/chia tập; 3–4 nhãn; 5–6 plugin; 7 dev; 8–9 test; 10 báo cáo/demo.
