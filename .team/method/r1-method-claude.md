# R1 — method-claude

Mã Hermes: `~/.hermes/hermes-agent` (rev `dce1e9b375`).

## 1. Điểm yếu (nặng → nhẹ)

1. **Nhãn "tầng tối thiểu đủ" là phản thực tế.** Fast ở bước t đổi cả quỹ đạo sau đó, nên chạy song song chỉ so được đầu ra của bước. ~300 bước từ 20–30 tác vụ tương quan theo cụm, không đủ học 3 lớp + α, β, T + 2 ngưỡng.
2. **Phụ thuộc Jev.** API alpha, hộp đen, mỗi bước thêm một lần gọi. P(level) không gắn với model cụ thể của mình. B4 cũng là Jev nên khó tách đóng góp. `logit P_k+αs−βe` chỉ là logistic có Jev làm feature.
3. **Luật entropy là heuristic.** Khi đã có P_k tích lũy và τ_p, luật này gần thừa và không xuất phát từ hàm mất mát nào.
4. **Luật chuyển sai đơn vị.** Vế trái nhân giá/token với số bước. Công thức bỏ qua giá cache-read ở tầng hiện tại, chi phí nâng tầng trở lại và rủi ro thất bại. Thêm nữa, **cắt tỉa tool theo bước cũng làm đổi tiền tố prompt nên mất cache**, như đổi model.
5. **Tính mới bị nói quá.** Jev Router ở chế độ agentic vẫn thấy traceback trong hội thoại. Phần mới thật: hành động gồm cả harness, quyết định có tính cache, vòng học kỹ năng.
6. **Thống kê.** Với n=100, khoảng tin cậy 95% của TSR khoảng ±9 điểm %, nên "vài điểm %" là không phân biệt được. Warm-start trên biến thể cùng khuôn bị rò rỉ. ECE trên 300 bước tương quan không ổn định.
7. **Khối lượng.** ~4.200 lần chạy, quá sức.

## 2. Chỉnh sửa đề xuất

**Ước lượng chính: hồi quy logistic thứ tự** `P(level≤k|s_t)=σ(θ_k−wᵀs_t)`. α, β nằm sẵn trong w. Hiệu chỉnh T trên tập giữ lại, chia GroupKFold theo khuôn tác vụ. Jev chỉ là baseline hoặc feature.

**Luật quyết định: cực tiểu chi phí kỳ vọng**, thay cho τ_p, τ_H và luật chuyển:
```
k* = argmin_k C_step(k|cache) + (1−p̂_k)·C_fail(k) + C_switch(cur→k)
C_step  = c_k^in·N_new + c_k^cr·N_cached + c_k^out·ô     (N_cached=0 nếu k≠cur)
C_switch= N_ctx·(c_k^in−c_k^cr) + q·N_ctx·(c_up^in−c_up^cr)   # q: xác suất phải nâng lại
C_fail  = 1 bước Frontier sửa lỗi + λ·P(fail|lỗi)             # ước lượng từ pilot
```
- Quét λ để vẽ đường Pareto.
- Hysteresis: chỉ đổi tầng ở ranh giới pha, tối đa 2 lần mỗi tác vụ.
- L̂_remain = trung vị số bước còn lại theo pha, lấy từ pilot.

**Gán nhãn rẻ:**
1. Chạy B1 để có quỹ đạo tham chiếu.
2. Ở mỗi bước, phát lại tiền tố và gọi Fast một lần. Gán y=1 nếu đạt đủ: schema hợp lệ, cùng tool, `ast.parse` qua, exit 0, giá trị trung gian lệch ≤1%.
3. Trên ~50 bước, cho Frontier chạy tiếp để kiểm nhãn cục bộ dự báo thành công cuối đến đâu.

**Cascade trong bước:** gọi tầng rẻ → kiểm schema + AST → nếu hỏng thì gọi Frontier ngay trong cùng bước, không tốn thêm vòng agent.

**Harness cố định theo tác vụ:** Skill và Toolset chọn một lần đầu lượt. Mỗi bước chỉ đổi Tier. Muốn chặn tool thì veto, không sửa schema.

**Chống overfit:** chia theo khuôn; chốt siêu tham số trước test; thêm tập con InfiAgent-DABench; warm-start chỉ đo trên khuôn chưa thấy.

## 3. Điểm móc Hermes (plugin, không sửa core, không proxy)

- **Đổi model mỗi bước:** middleware `llm_request` (`agent/turn_api_request.py:143-153`). Middleware trả `{"request":…}` để thay kwargs, kể cả `model` (`hermes_cli/middleware.py:88-99`). Đăng ký bằng `ctx.register_middleware` (`hermes_cli/plugins.py:937`).
  - Ràng buộc: các tầng phải cùng provider/api_mode (ví dụ OpenRouter).
  - Hook ghi `model=agent.model` (`agent/turn_response_intake.py:75`), nên tính phí theo `response_model` (`:88`, `src/assistant_core/telemetry.py:103`).
- **Cascade trong bước:** middleware `llm_execution` (`agent/turn_api_call.py:121-138`).
- **Tiêm SKILL.md:** hook `pre_llm_call`. Nội dung vào tin nhắn user, không vào system prompt, nên không phá cache (`agent/turn_context.py:793-799`).
- **AST gate:** middleware `tool_request` (`hermes_cli/middleware.py:102`) hoặc veto `pre_tool_call` (mẫu ở `src/assistant_core/hermes_plugin.py:133-137`).
- **Rút gọn traceback:** hook `transform_tool_result` (`hermes_cli/plugins.py:110`).
- **e_{t−1}:** `post_tool_call`, đã có trong bảng `tool_calls`.
- **Cần thêm:**
  - Plugin mới `scer-router`, vì assistant-core không đổi model (`hermes_plugin.py:8`).
  - Bộ chấm chỉ kiểm văn bản (`src/assistant_core/bench.py:68`), cần thêm `numeric_close` và `artifact_exists`.

## 4. Thực nghiệm tối thiểu (10 tuần)

- **Phạm vi:** 2 tầng (Fast, Frontier); Specialist để sau.
- **Benchmark:** 20 khuôn × 3 biến thể (8 khuôn dev, 12 khuôn test), cộng 40 tác vụ từ tập ngoài.
- **Cấu hình:** B1, B2, B5, B6, B4 (nếu kịp), SCER.
- **Ablation:** −HarnessFeatures (router chỉ đọc query), −SwitchCost, −Cascade, −Skill.
- **Lặp và kiểm định:** 2 lần/tác vụ; McNemar hoặc bootstrap ghép cặp; không-kém-hơn B1 với δ = 5 điểm %.
- **Metric chính:** USD/tác vụ đúng, p95 độ trễ.
- **Ngân sách:** khoảng 1.200 lần chạy, ước 100–250 USD; phải đo lại ở tuần 3.

**Lịch:**

| Tuần | Việc |
|---|---|
| 1–2 | Benchmark + bộ chấm |
| 3–4 | Pilot + gán nhãn |
| 5–6 | Plugin router + cascade |
| 7 | Huấn luyện trên dev |
| 8–9 | Chạy test + ablation |
| 10 | Báo cáo + demo |

## 5. Câu hỏi cho thầy

1. Đóng góp chính là router có tính cache hay harness + học kỹ năng? Thu hẹp còn 2 tầng được không?
2. Jev có bắt buộc làm lõi, hay chỉ cần làm baseline?
3. Nhãn "đủ cục bộ" kèm kiểm chứng phản thực tế trên mẫu nhỏ có đủ chặt không?
4. Tiêu chí không-kém-hơn với δ = 5 điểm %, kèm USD/tác vụ đúng, có được chấp nhận? Có cần thêm benchmark công khai?
5. Ngân sách API tối đa là bao nhiêu? Demo Zalo có nằm trong phạm vi đánh giá không?
