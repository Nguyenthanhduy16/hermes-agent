# R2 — method-claude

## 1. Đồng ý với Codex

- **Năng lực các tầng không lồng nhau.** Bỏ P tích lũy; nếu giữ logit(P) thì phải clip.
- **MVP 2 tầng; bỏ entropy, PCA, LightGBM.** Dùng logistic L2. Jev chỉ là đối chứng.
- **Chi phí theo các nhóm token rời nhau:** uncached, cache-read, cache-write, output. Chi phí chuyển đã nằm sẵn trong C_k.
- **Tách ablation −Skill** thành −SkillFeature và −SkillInject.
- **§4.3 mâu thuẫn §4.5.** Chốt: lỗi schema thì sửa cục bộ 1 lần; lỗi thực thi/checker thì leo thang ngay. Không ép Fast cho bước "định dạng". AST không thay sandbox.
- **Thống kê:** chia theo họ tác vụ; bootstrap ghép cặp theo họ; không tuyên bố tương đương khi khoảng tin cậy rộng.
- **Tổng tiền tính đủ** lượt thất bại, router, gán nhãn, học skill.
- **Telemetry thiếu** trường router/feature/skill-version/nhãn (`src/assistant_core/telemetry.py:23`).

## 2. Bất đồng và cách chốt

**a. Móc vào Hermes: chốt middleware `llm_request` trong plugin; proxy là dự phòng.** Tôi đã mở lại mã (`~/.hermes/hermes-agent`, rev `dce1e9b375`). Codex chọn proxy chỉ vì `.runtime/` vắng, không tìm thấy mã Hermes.
- Mỗi lần gọi provider, kwargs đi qua `apply_llm_request_middleware` (`agent/turn_api_request.py:143-153`).
- Callback trả `{"request":{...}}` thì kwargs bị thay và các callback nối tiếp nhau (`hermes_cli/middleware.py:63-99`, `hermes_cli/plugins_dispatch.py:605-616`).
- Đăng ký không cần capability (`hermes_cli/plugins.py:937-942`).
- kwargs đã sửa đi thẳng tới lời gọi thật (`agent/turn_api_call.py:88-100,132-138`). Model được đọc từ kwargs (`agent/chat_completion_helpers.py:590`). Như vậy đổi `model` ở đây có hiệu lực.
- **Ràng buộc:**
  - Các tầng cùng provider/api_mode, vì reasoning config và max_tokens dựng theo `agent.model`.
  - Callback lỗi thì fail-open, kwargs giữ nguyên (`plugins_dispatch.py:617-619`). Vì vậy đặt main model = Frontier và ghi log mọi lần fail-open.
  - Tính phí theo `response_model` (`agent/turn_response_intake.py:75,88`).
- **Dự phòng:** proxy OpenAI-compatible ở `base_url`, nhận state qua header, cho trường hợp các tầng khác provider hoặc bản cập nhật Hermes làm hỏng hook.
- Cascade trong bước dùng `llm_execution` (`turn_api_call.py:121-138`).

**b. Luật quyết định: argmin chi phí kỳ vọng, viết dưới dạng ngưỡng thích ứng.** Với 2 tầng, chọn Fast ⇔ q_F ≥ τ_t, với `τ_t = 1 − (C_Fr,t − C_F,t)/C_fail`.
- τ_t tự tăng khi đang ở Frontier có cache nóng, nên δ của Codex thành hệ quả, không phải tham số tay.
- C_fail = chi phí một bước Frontier cứu lỗi + λ. Quét λ trên dev để vẽ Pareto.
- "τ cố định + δ" giữ làm ablation −SwitchCost.

**c. Nhãn: nhị phân y_F.**
- Nếu thêm Specialist thì học q_S, q_F độc lập, không dùng thang thứ tự.
- Replay tiền tố, lấy trạng thái từ cả quỹ đạo Fast lẫn Frontier (tránh lệch phân phối), có snapshot thư mục làm việc của sandbox.
- Kiểm toán phản thực tế: chọn ngẫu nhiên phân tầng ~15% số bước, chạy hết tác vụ, đo AUC giữa y_F và thành công cuối. AUC kém thì ghi là giới hạn.

**d. Harness cố định mỗi lượt.**
- Skill tiêm một lần qua `pre_llm_call`, vào tin nhắn user (`agent/turn_context.py:793-799`).
- Toolset cố định; tool không được phép thì veto bằng `pre_tool_call`, không gỡ khỏi `tools` (gỡ sẽ đổi tiền tố prompt, mất cache).
- Mỗi bước chỉ đổi Tier.

**e. Học kỹ năng ngoại tuyến có duyệt.** `skill_manage` đang bị chặn (`src/assistant_core/settings.py:12`).
- SKILL.md chỉ sinh từ họ train, được duyệt rồi đóng băng kèm hash trước test.
- Vòng online chỉ dùng cho demo/warm-start trên họ riêng.

## 3. Thuật toán chốt

```
TURN START(query, files):
  skill ← argmax cos(emb(query), kho đóng băng) nếu ≥ σ_s; inject qua pre_llm_call
  toolset ← skill.allowed_tools ∪ core            # cố định cả lượt
STEP t (llm_request):
  x_t ← [pha, N_ctx, n_cols/rows, s_skill, skill_succ(làm trơn), loại lỗi e_{t−1},
         số lần lỗi, tier_{t−1}, cache_hot]
  q_F ← σ(wᵀx_t / T)                              # logistic L2, T hiệu chỉnh trên dev
  if e_{t−1} ∈ {runtime, checker}: tier ← Frontier
  elif q_F ≥ τ_t(N_ctx, cache, λ) and switches < 2: tier ← Fast
  else: tier ← Frontier
  log(x_t, q_F, τ_t, tier)
CASCADE (llm_execution):
  r ← call(tier); if tier=Fast and (schema hỏng or ast.parse lỗi): r ← call(Frontier)
POST: e_t ← post_tool_call.status; dừng nếu vượt trần bước/tiền
OFFLINE: refit w trên train; skill mới qua duyệt → bản đóng băng mới
```

## 4. Thực nghiệm chốt

- **Dữ liệu:** pilot 20, dev 20, test 60 tác vụ. Chia theo họ; test là họ chưa thấy. Thêm ~40 tác vụ InfiAgent-DABench nếu kịp.
- **Cấu hình:** B1 Frontier, B2 Fast, B5 luật + cascade, B6 ngẫu nhiên khớp tỷ lệ, B4 Jev (nếu khả dụng), SCER.
- **Ablation** (trên tập con định trước): −HarnessFeatures, −SkillFeature, −SkillInject, −SwitchCost, −Cascade.
- **Lặp:** 3 lần cho B1 và SCER; 1–2 lần cho cấu hình khác.
- **Kiểm định:** bootstrap ghép cặp theo họ + McNemar. Báo cáo TSR, USD/thành công, tổng tiền, p95, Brier. Biên không-kém-hơn δ để thầy chốt.
- **Ngân sách:** pilot tuần 3 chốt trần (dự kiến 100–250 USD, có 20% dự phòng). Vượt trần thì cắt theo thứ tự: tập ngoài → lần lặp → ablation phụ.
- **Lịch:** tuần 1–2 checker + ghim model/giá; 3–4 pilot + nhãn; 5–6 plugin + skill; 7–8 chạy test khóa kín; 9–10 phân tích + báo cáo.
