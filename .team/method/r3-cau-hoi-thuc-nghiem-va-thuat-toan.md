# Câu hỏi thực nghiệm và thuật toán định tuyến — SCER-AH (2026-11-05)

Tài liệu này chốt **câu hỏi thực nghiệm (RQ)** trước, rồi mới chọn **thuật toán** cho từng câu hỏi.
Dựa trên: đề xuất gốc (`docs/Đề xuất Đồ án 2 – SCER-AH cho Hermes Agent.pdf`), thảo luận phương pháp
vòng 2 (`.team/method/r2-method-claude.md`), báo cáo tiến độ gửi thầy ngày 04/11/2026, và tra cứu tài liệu 2025–2026.

> Lưu ý: các bài 2026 (TwinRouterBench, R2V-Agent, Conformal Cascade) mới đọc tóm tắt/trích đoạn,
> chưa đọc toàn văn. Cần kiểm tra lại số liệu trước khi trích dẫn trong báo cáo.

---

## 1. Đóng góp chính là gì?

**Đóng góp chính: giảm chi phí và độ trễ, không phải tăng độ chính xác.**

- Bộ định tuyến chỉ chọn giữa mô hình rẻ (Fast) và mô hình mạnh (Frontier). Vì vậy tỷ lệ hoàn thành tác vụ (TSR) gần như không vượt được cấu hình "luôn Frontier" (B1). Hứa "tăng độ chính xác" cho bộ định tuyến sẽ khó bảo vệ.
- Có hai cách phát biểu bài toán, tương đương nhau. Đường Pareto chi phí–TSR trả lời được cả hai:
  - (a) Chi phí nhỏ nhất, với điều kiện TSR ≥ TSR(B1) − δ (kiểm định không-kém-hơn).
  - (b) TSR lớn nhất, với điều kiện tổng chi phí ≤ ngân sách B.
- Chỉ nói "tăng độ chính xác" cho **mô hình rẻ khi có harness hỗ trợ** (tiêm kỹ năng, cắt tỉa công cụ). Đây là đóng góp phụ: Fast làm đúng nhiều hơn thì giao được nhiều bước hơn cho Fast.

---

## 2. Câu hỏi thực nghiệm

| RQ | Câu hỏi | Giả thuyết đo được | Metric chính | So với |
|---|---|---|---|---|
| **RQ0** (pilot, quyết định có đi tiếp không) | Bao nhiêu bước mô hình rẻ làm được mà kết quả cuối không đổi? | Bộ định tuyến lý tưởng (oracle) tiết kiệm ≥ 30% chi phí | Tỷ lệ bước Fast đủ dùng; chi phí oracle so với B1 | B1, B2 |
| **RQ1** (chính) | Định tuyến từng bước có giảm chi phí ở mức TSR không kém hơn không? | USD/tác vụ thành công giảm ≥ 30%; TSR ≥ B1 − δ | USD/tác vụ thành công, TSR, latency p95 | B1, B2, luật đơn giản, ngẫu nhiên, **định tuyến theo câu hỏi** |
| **RQ2** (điểm mới 1) | Tín hiệu bên trong tác tử (lỗi bước trước, pha, kỹ năng) có giúp quyết định tốt hơn so với chỉ đọc văn bản không? | AUC cao hơn, đường Pareto nằm trên | AUC/Brier của q_F, diện tích dưới đường Pareto | Bộ định tuyến chỉ dùng embedding văn bản |
| **RQ3** (điểm mới 2) | Tính chi phí mất prompt cache có làm giảm chi phí thật không? | Ít lần chuyển hơn, tổng tiền thật thấp hơn | Tiền thật (đã tính cache), số lần chuyển/tác vụ | Ablation −SwitchCost |
| **RQ4** | Leo thang khi lỗi có cứu tác vụ rẻ hơn thử lại không? | Recovery rate cao hơn, ít bước sửa lỗi hơn | Recovery rate, số bước sửa lỗi | Thử lại cùng tầng (k = 0/1/2) |
| **RQ5** (phụ, độ chính xác) | Cắt tỉa công cụ và tiêm SKILL.md có tăng tỷ lệ đúng của Fast không? | Lỗi gọi công cụ giảm, tỷ lệ giao Fast tăng | Tỷ lệ lỗi schema/tên tool, tỷ lệ bước Fast đúng | −Prune, −SkillInject |

**Hai điểm then chốt**

1. **RQ1 phải có đối chứng định tuyến theo câu hỏi.** Đối chứng này theo kiểu RouteLLM: quyết định một lần cho cả tác vụ. Nếu không thắng được nó thì "theo từng bước" không có giá trị. Báo cáo 04/11 chưa có đối chứng này, cần bổ sung (B7).
2. **RQ0 làm trước tiên.** Nếu oracle chỉ rẻ hơn B1 khoảng 10% thì mọi thuật toán phía sau đều không còn ý nghĩa.

---

## 3. Thuật toán cho từng câu hỏi

### RQ0 — Gán nhãn "bước này Fast làm được không"

- **Giao thức phát lại tiền tố: hạ tầng rồi leo thang** (theo TwinRouterBench). Tại bước t, Fast làm bước t, các bước sau chạy Frontier. Nếu tác vụ vẫn đúng thì gán y_F = 1, ngược lại y_F = 0.
- Lấy trạng thái từ cả quỹ đạo Fast lẫn quỹ đạo Frontier để tránh lệch phân phối. Mỗi lần phát lại cần snapshot thư mục làm việc của sandbox.
- Để tiết kiệm tiền, chỉ lấy mẫu phân tầng khoảng 15–30% số bước (phân tầng theo pha và theo bộ benchmark).
- TwinRouterBench cho thấy bộ định tuyến logistic học trên nhãn kiểu này đạt đánh đổi chi phí–thành công tốt nhất trong các chính sách được thử. Đây là lý do nên giữ mô hình đơn giản.

### RQ1 — Luật quyết định (phần lõi)

| Lựa chọn | Nguồn | Ưu | Nhược với đồ án |
|---|---|---|---|
| Phân loại + ngưỡng | RouteLLM, TwinRouterBench | Đơn giản, ít nhãn | Ngưỡng chỉnh tay, không có bảo đảm |
| Chi phí kỳ vọng nhỏ nhất (Bayes) | Đã chốt ở r2 | Đưa chi phí cache vào tự nhiên | Cần q_F được hiệu chỉnh tốt |
| Contextual bandit (LinUCB) | MixLLM, PILOT, LinUCB tuần tự (AAAI-26) | Học trực tuyến, cần ít nhãn | Tốn tiền khám phá, khó tái lập |
| Học tăng cường | Router-R1 | Tối ưu cả quỹ đạo | Quá tốn so với ngân sách sinh viên |
| Cascade + verifier | FrugalGPT, R2V-Agent (verifier từng bước + CVaR) | Hợp với agent | Cần verifier; R2V phải huấn luyện SLM |
| **Conformal / kiểm soát rủi ro** | Conformal Cascade (2026), Clopper–Pearson | **Bảo đảm thống kê** tỷ lệ lỗi ≤ α | Cần tập hiệu chỉnh riêng |

**Đề xuất chốt:** logistic có hiệu chỉnh nhiệt độ để ra q_F, rồi chọn tầng theo chi phí kỳ vọng nhỏ nhất, với ngưỡng τ chọn bằng kiểm soát rủi ro.

- Với 2 tầng, chọn Fast ⇔ q_F ≥ τ_t, trong đó τ_t = 1 − (C_Fr,t − C_F,t) / C_fail. τ_t tự điều chỉnh theo trạng thái cache.
- Trên tập hiệu chỉnh, chọn ngưỡng sàn τ* nhỏ nhất sao cho cận trên Clopper–Pearson của tỷ lệ "giao Fast mà hỏng" ≤ α, với độ tin cậy 1 − δ. Khi chạy dùng max(τ_t, τ*).
- Cách này biến "TSR không kém hơn" từ một hy vọng thành **một bảo đảm có thể phát biểu trong báo cáo**. Đây là điểm mạnh hơn RouteLLM và FrugalGPT.
- LinUCB giữ làm ablation hoặc hướng mở rộng (học trực tuyến), không làm phương án chính.

### RQ2 — Tín hiệu bên trong tác tử

- Dùng cùng một mô hình logistic, chỉ đổi tập đặc trưng:
  - **Chỉ văn bản:** embedding tin nhắn cuối.
  - **Văn bản + harness:** pha (lập kế hoạch/viết code/sửa lỗi/trả lời), loại lỗi e_{t−1}, số lần lỗi, s_skill, tỷ lệ thành công của skill, tier_{t−1}, N_ctx, số cột/dòng dữ liệu.
- Đánh giá bằng AUC/Brier trên họ tác vụ chưa thấy và bằng đường Pareto. Báo cáo thêm hệ số logistic để giải thích.
- Có thể thêm tín hiệu verifier rẻ, không cần mô hình: `ast.parse`, kiểm tra schema tool-call, exit code.
- Ablation của R2V-Agent cho thấy định tuyến chỉ dựa vào entropy không hơn chạy toàn SLM, còn verifier cứu được phần lớn. Điều này ủng hộ việc dùng tín hiệu thực thi thay cho entropy.

### RQ3 — Chi phí chuyển mô hình (prompt cache)

**Sửa mô hình chi phí:** theo dõi **trạng thái cache của từng mô hình** (còn ấm hay không, theo TTL), không chỉ "đang ở tầng nào".

- LiteLLM báo rằng khi quay lại mô hình cũ trong TTL, phần lớn cache vẫn ấm. Số liệu này do vendor tự báo, cần đo lại trong pilot.
- Nếu đúng như vậy, chi phí thật chủ yếu nằm ở **lần đầu** chuyển sang một mô hình lạnh. Công thức trong đề xuất gốc, `(c_cur − c_k)·L̂_remain > c_in_k·N_ctx`, đang tính quá tay.

**Thuật toán:**

```
C_k,t = uncached_k·p_in,k + cache_read_k·p_read,k + cache_write_k·p_write,k + E[out_k]·p_out,k
        # uncached/cache_read/cache_write phụ thuộc cache_warm[k] (thời điểm gọi cuối < TTL)
Hysteresis: đã chuyển tầng thì giữ tối thiểu m bước (trừ khi gặp lỗi bắt buộc leo thang)
Lợi ích khi chuyển xuống Fast được nhân với L̂_remain (ước lượng theo pha, từ dữ liệu pilot)
```

**Ablation:** (i) luật một bước, (ii) có L̂_remain, (iii) bỏ chi phí chuyển (τ cố định).

### RQ4 — Leo thang khi gặp lỗi

So sánh các chính sách:

- Leo thang ngay khi gặp lỗi.
- Fast thử lại k lần rồi mới leo thang (k = 0/1/2).
- **Theo loại lỗi (đề xuất):**
  - Lỗi schema/JSON: Fast sửa cục bộ 1 lần.
  - Lỗi runtime hoặc checker: lên Frontier ngay.

**Cần kiểm chứng:**

- Báo cáo 04/11 viết rằng không thể gọi lại mô hình mạnh trong cùng một bước.
- Bản r2 viết rằng hook `llm_execution` (`agent/turn_api_call.py:121-138`) cho phép cascade ngay trong bước.
- Phải mở mã Hermes để chốt, vì điều này quyết định cascade xảy ra trong bước hay ở bước kế tiếp.

### RQ5 — Harness giúp mô hình rẻ

- **Cắt tỉa công cụ:**
  - RAG-MCP truy hồi top-k tool, đưa độ chính xác chọn tool từ 13,6% lên 43,1% và giảm hơn 50% token prompt.
  - **Xung đột cần chốt:** gỡ tool khỏi `tools` làm đổi tiền tố prompt và mất cache (đụng RQ3). Bản r2 chọn veto bằng `pre_tool_call`, nhưng khi đó mô hình vẫn thấy đủ tool nên **mất tác dụng giảm nhiễu**.
  - Phương án dung hòa: chốt toolset một lần khi bắt đầu lượt.
  - RQ5 chính là thí nghiệm đo đánh đổi này: veto, cắt theo lượt, và không cắt.
- **Tiêm kỹ năng:**
  - SkillWeaver cho thấy kỹ năng do mô hình mạnh viết giúp mô hình yếu (tới +54,3% trên WebArena).
  - Skill Transfer (NeurIPS 2026) và OpenSkill cho thấy chuyển kỹ năng **có thể gây hại** tùy cặp mô hình.
  - Vì vậy phải đo, không giả định có lợi. Tách hai ablation: −SkillFeature (bỏ s_skill khỏi đặc trưng) và −SkillInject (không tiêm SKILL.md).

---

## 4. Ánh xạ RQ → thuật toán → thí nghiệm

| RQ | Thuật toán chốt | Thí nghiệm |
|---|---|---|
| RQ0 | Phát lại tiền tố, hạ tầng rồi leo thang; lấy mẫu phân tầng | Pilot 20 tác vụ: B1, B2, oracle |
| RQ1 | Logistic + chi phí kỳ vọng nhỏ nhất + ngưỡng kiểm soát rủi ro | So với B1, B2, B5 luật, B6 ngẫu nhiên, B7 theo câu hỏi, (B4 Jev) |
| RQ2 | Cùng logistic, hai tập đặc trưng | Ablation −HarnessFeatures; AUC/Brier, Pareto |
| RQ3 | Chi phí theo cache từng mô hình + hysteresis + L̂_remain | Ablation −SwitchCost, −L̂_remain |
| RQ4 | Leo thang theo loại lỗi | So với k = 0/1/2 và −Cascade |
| RQ5 | Toolset theo lượt; tiêm SKILL.md | Ablation −Prune, −SkillFeature, −SkillInject |

**Thống kê:** chia tập theo họ tác vụ (test là họ chưa thấy); bootstrap ghép cặp theo họ + McNemar; báo cáo khoảng tin cậy. Không tuyên bố tương đương khi khoảng tin cậy rộng. Biên δ của kiểm định không-kém-hơn để thầy chốt.

---

## 5. Thứ tự thực hiện

1. **Pilot đo RQ0** trên khoảng 20 tác vụ: chạy B1, B2 và oracle trên mẫu các bước. Đo thật hành vi cache khi chuyển mô hình qua OpenRouter.
2. Nếu khoảng tiết kiệm tiềm năng đủ lớn: cài logistic + chi phí kỳ vọng nhỏ nhất + kiểm soát rủi ro (RQ1), kèm ablation RQ2 và RQ3.
3. RQ4 dùng luật theo loại lỗi, không cần học.
4. RQ5 và vòng học kỹ năng chỉ làm nếu còn thời gian.

## 6. Câu hỏi xin ý kiến thầy

1. Chốt đóng góp chính là "giảm chi phí ở TSR không kém hơn" (thay vì tăng độ chính xác) có phù hợp không? Biên δ bao nhiêu là chấp nhận được?
2. Có cần bảo đảm thống kê (kiểm soát rủi ro/conformal) hay chỉ cần đường Pareto thực nghiệm?
3. Nếu RQ0 cho thấy khoảng tiết kiệm nhỏ (< 20%), có nên chuyển trọng tâm sang RQ5 (harness giúp mô hình rẻ) không?

---

## Nguồn

- TwinRouterBench (arXiv 2605.18859): https://arxiv.org/abs/2605.18859
- R2V Agent: Teaching SLMs When to Ask for Help (arXiv 2605.16604): https://arxiv.org/pdf/2605.16604
- AgentOpt v0.1 (arXiv 2604.06296): https://arxiv.org/pdf/2604.06296
- Conformal Cascade (arXiv 2607.25018): https://arxiv.org/pdf/2607.25018
- Prune 'n Predict (arXiv 2501.00555): https://arxiv.org/pdf/2501.00555
- PILOT — Adaptive LLM Routing under Budget Constraints (arXiv 2508.21141): https://arxiv.deeppaper.ai/papers/2508.21141v1
- MixLLM (arXiv 2502.18482): https://arxiv.deeppaper.ai/papers/2502.18482v1
- Sequential LinUCB LLM selection (arXiv 2506.17670, AAAI-26): https://arxiv.org/pdf/2506.17670v1
- The Hidden Cost of LLM Routing: https://towardsdatascience.com/the-hidden-cost-of-llm-routing-why-switching-models-costs-more/
- LiteLLM — Prompt Caching với auto router: https://docs.litellm.ai/docs/auto_router/prompt_caching
- RAG-MCP (arXiv 2505.03275): https://arxiv.org/abs/2505.03275
- SkillWeaver (arXiv 2504.07079): https://arxiv.org/abs/2504.07079v1
- Skill Transfer (NeurIPS 2026): https://neurips.cc/virtual/2026/165609
- OpenSkill (arXiv 2606.06741): https://arxiv.org/pdf/2606.06741
- Dynamic Agent Skills survey (arXiv 2607.10113): https://arxiv.org/pdf/2607.10113
- Awesome-Routing-LLMs: https://github.com/MilkThink-Lab/Awesome-Routing-LLMs
- Đã có trong đề xuất gốc: RouteLLM (ICLR 2025), FrugalGPT (2023), Jev Router (2026)
