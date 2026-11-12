# Thảo luận phương pháp — Đồ án 2 SCER-AH (2026-11-02)

Bạn là một trong hai chuyên gia (Claude = `method-claude`, Codex = `method-codex`) được mời phản biện và hoàn thiện
**phương pháp/thuật toán** cho đồ án của sinh viên, để đưa vào báo cáo tiến độ gửi thầy hướng dẫn xin góp ý.
Trả lời bằng **tiếng Việt có dấu**. Chỉ ĐỌC repo; KHÔNG sửa code, không git, không chạy server. Chỉ ghi file trong `.team/method/`.

## Tài liệu bắt buộc đọc
- Đề xuất gốc (PDF 15 trang): `docs/Đề xuất Đồ án 2 – SCER-AH cho Hermes Agent.pdf`
  Tóm tắt: SCER-AH = Skill-Conditioned Entropy Routing & Adaptive Harness. Tại mỗi bước t của vòng lặp Hermes Agent,
  router đọc trạng thái s_t = [f_query ⊕ f_skill ⊕ f_harness] và trả a_t = (Tier, Skill, Toolset, Guardrail).
  Ba tầng Fast/Specialist/Frontier. Pha 1 luật chặn (lỗi lần 2 → Frontier; yêu cầu mới không có skill → Frontier;
  bước định dạng → Fast). Pha 2: Jev Decisions API (Score trên thang thứ tự) → P_k tích lũy → hiệu chỉnh
  p̂ = σ((logit P_k + α s_skill − β e_{t−1})/T), chọn tầng rẻ nhất có p̂ ≥ τ_p, nếu entropy chuẩn hóa H_t > τ_H
  thì nâng 1 tầng. Luật chi phí chuyển (mất prompt cache): chỉ hạ tầng khi (c_cur − c_k)·L̂_remain > c_in_k · N_ctx.
  Harness thích ứng: tiêm SKILL.md, cắt tỉa tool, AST gate, ép kiểu Pydantic, rút gọn traceback. Cascade theo lỗi
  sandbox + vòng học kỹ năng (thành công qua Frontier → nén quỹ đạo → SKILL.md → lần sau Fast dùng lại).
  Baseline B1–B6 (B4 = Jev Router), ablation −Skill/−Prune/−AST/−Cascade/−Entropy/−SwitchCost/Jev→LightGBM.
  Benchmark 100 tác vụ phân tích dữ liệu bảng (L1 35, L2 40, L3 25), metric TSR, Pass@1, cost/successful task,
  latency p95, ECE/Brier. Pilot 20–30 tác vụ, ~300 bước gán nhãn "tầng tối thiểu đủ".
- Trạng thái hiện có trong repo: `docs/benchmark.md`, `docs/observe.md`, `docs/nghien-cuu-tro-ly.md`,
  `docs/nghien-cuu-quan-ly-cloud.md`, `docs/tro-ly-zalo.md`, `plugins/assistant-core/` (plugin Hermes: hạn mức, chặn tool,
  telemetry từng lần gọi model), `config/bench/*.yaml`, `src/hermes_zalo/admin_bench.py`, `admin_observe.py`.
  Mã nguồn Hermes (nếu cần xem hook vòng lặp): `.runtime/hermes-agent/`.

## Nhiệm vụ VÒNG 1 (độc lập, đừng đọc file của người kia)
Viết `.team/method/r1-<tên-bạn>.md` (≤ 900 từ), gồm:
1. **Điểm yếu/lỗ hổng** của phương pháp hiện tại (về thuật toán, thống kê, khả năng cài đặt trong Hermes, chi phí,
   tính mới so với RouteLLM/FrugalGPT/Jev Router). Xếp theo mức nghiêm trọng.
2. **Đề xuất chỉnh sửa cụ thể** cho thuật toán: công thức/luật/pseudo-code nếu cần (ví dụ: cách gán nhãn
   "tầng tối thiểu đủ" rẻ hơn, cách ước lượng L̂_remain, có cần Jev không, thay bằng gì, cách học α/β/T,
   cách đưa chi phí chuyển vào quyết định, chống overfit benchmark tự xây...).
3. **Điểm móc vào Hermes**: dựa trên code thực tế (plugin hook nào, proxy OpenAI-compatible hay sửa core) — dẫn
   `file:line` khi khẳng định.
4. **Thiết kế thực nghiệm tối thiểu khả thi** cho sinh viên trong ~10 tuần, ngân sách API nhỏ.
5. **3–5 câu hỏi nên xin ý kiến thầy hướng dẫn.**
Khi xong, in đúng dòng `R1 DONE` rồi dừng.

## Nhiệm vụ VÒNG 2 (phản biện chéo và hội tụ)
Đọc `r1-method-claude.md` và `r1-method-codex.md`. Viết `.team/method/r2-<tên-bạn>.md` (≤ 800 từ):
1. **Đồng ý** với người kia ở điểm nào (liệt kê ngắn).
2. **Bất đồng còn lại và cách giải quyết**, bắt buộc xử lý các điểm sau, có kiểm chứng trong code khi liên quan:
   a. Móc vào Hermes: middleware `llm_request` trong plugin (Claude dẫn mã Hermes ở `~/.hermes/hermes-agent`, ví dụ
      `agent/turn_api_request.py:143-153`, `hermes_cli/middleware.py:88-99`, `hermes_cli/plugins.py:937`) hay proxy
      OpenAI-compatible (Codex)? Hãy MỞ các file đó để xác minh rồi chốt một phương án chính + phương án dự phòng.
   b. Luật quyết định: argmin chi phí kỳ vọng (Claude) hay ngưỡng q_F ≥ τ + chênh chi phí δ (Codex)? Chốt một.
   c. Nhãn: thứ tự (ordinal) 3 tầng hay nhị phân q_F; cách gán nhãn replay-tiền-tố + kiểm toán phản thực tế.
   d. Harness: chọn Skill/Toolset một lần mỗi lượt hay mỗi bước (ảnh hưởng prompt cache).
   e. Học kỹ năng: online hay ngoại tuyến có duyệt (`skill_manage` đang bị chặn).
3. **Thuật toán chốt** dạng pseudo-code ngắn (đầu vào, đặc trưng, ước lượng, luật quyết định, cascade, cập nhật).
4. **Thiết kế thực nghiệm chốt** (cấu hình, ablation, chia tập, kiểm định, ngân sách).
Khi xong in đúng dòng `R2 DONE` rồi dừng.
