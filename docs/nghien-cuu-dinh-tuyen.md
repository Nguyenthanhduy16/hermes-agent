# Gói C: các công trình định tuyến mô hình liên quan

Cập nhật 12/11/2026. Ghi chú cho gói C trong [kế hoạch đồ án](ke-hoach-do-an.md):
ai đã làm định tuyến mô hình, họ nhận gì, trả gì, học từ dữ liệu nào, đánh giá
ra sao, và so với thiết kế của đồ án ([khung định tuyến](dinh-tuyen.md)).

**Mức kiểm chứng.** Đã đọc toàn văn 11 bài trong thư mục `papers/` (RouteLLM,
FrugalGPT, RouterBench, Router-R1, MTRouter, EvoRoute, AgentRouter, TRIM,
Agentic Routing, Budget-Aware Agentic Routing, InfiAgent-DABench), và đã đọc mã
nguồn RouteLLM, MTRouter, RouterBench. Các con số dưới đây là số tác giả tự báo
cáo trong bài. Chưa chạy lại mã của bài nào. Chưa có: DA-Code.

## 1. Bức tranh chung

Các công trình chia theo **đơn vị được định tuyến**:

| Nhóm | Định tuyến cái gì | Công trình |
|---|---|---|
| Theo câu hỏi | Cả một câu hỏi đi tới một mô hình | RouteLLM, FrugalGPT, RouterBench |
| Theo lượt / nhiều vòng | Mỗi lượt trong một tác vụ nhiều lượt | Router-R1, MTRouter |
| Theo bước của agent | Mỗi lần gọi mô hình hoặc mỗi sub-task trong vòng lặp agent | EvoRoute, AgentRouter, Agentic Routing, Budget-Aware Agentic Routing, StepRouter (shipit_agent) |
| Theo bước suy luận | Mỗi bước trong chuỗi suy luận toán | TRIM |

Đồ án thuộc nhóm thứ ba. **Định tuyến theo từng bước trong vòng lặp agent đã có
người làm và đã đăng ở hội nghị lớn năm 2026**, nên đó không còn là đóng góp
mới. Đóng góp cần đặt vào cách làm và điều kiện làm (mục 4).

## 2. Từng công trình

### 2.1 RouteLLM (Ong và cộng sự, 2024)

- **Đầu vào:** nội dung câu hỏi. **Đầu ra:** xác suất mô hình mạnh thắng mô
  hình yếu, so với ngưỡng α.
- **Bộ định tuyến:** phân rã ma trận (`mf`, cần API embedding), BERT (`bert`,
  chạy cục bộ), LLM nhỏ làm bộ phân loại, xếp hạng theo độ tương tự (`sw_ranking`).
- **Dữ liệu huấn luyện:** cặp so sánh của Chatbot Arena, thêm dữ liệu tăng
  cường có nhãn bằng LLM chấm.
- **Đánh giá:** MT-Bench, MMLU, GSM8K, cặp GPT-4 / Mixtral. Câu trả lời của cả
  hai mô hình được **tính sẵn**; router chỉ chấm câu hỏi rồi tra kết quả.
  - **PGR** = (acc − acc_yếu) / (acc_mạnh − acc_yếu).
  - **APGR**: diện tích dưới đường PGR theo % gọi mạnh.
  - **CPT(x%)**: % gọi mạnh tối thiểu để đạt PGR x%, báo cáo 20/50/80%.
  - Mốc: router ngẫu nhiên (trung bình 10 lần) và đường oracle.
  - Kiểm tra trùng giữa dữ liệu huấn luyện và benchmark bằng độ giống embedding.
- **Kết quả đáng chú ý:** trên MMLU, router chỉ học từ Arena **ngang router
  ngẫu nhiên**; phải thêm dữ liệu tăng cường mới hơn ngẫu nhiên.
- **Mã:** `lm-sys/RouteLLM`.
- **Dùng cho đồ án:** cách đo (PGR, APGR, CPT, đường ngẫu nhiên, oracle); mốc
  theo câu hỏi chạy ngoại tuyến (E3 trong [thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md)).
  Kết quả MMLU cho thấy router có sẵn có thể chỉ ngang ngẫu nhiên trên DABench;
  đó vẫn là một kết quả báo cáo được.

### 2.2 FrugalGPT (Chen và cộng sự, 2023)

- **Cách làm:** cascade từ mô hình rẻ tới đắt; bộ chấm DistilBERT; đạt ngưỡng
  thì dừng. Học thứ tự mô hình và ngưỡng với ràng buộc ngân sách.
- **Phân tích đáng lấy: MPI** (maximum performance improvement): tỷ lệ câu mà
  mô hình đắt sai nhưng mô hình rẻ đúng, cho từng cặp mô hình. Ví dụ COQA: 13%
  câu GPT-4 sai mà GPT-3 đúng.
- **Dùng cho đồ án:** cascade không làm được trong Hermes (một lần gọi mỗi bước).
  Lấy bảng **bổ trợ 2×2 giữa S và C** (cả hai đúng / chỉ S đúng / chỉ C đúng /
  cả hai sai) làm phân tích khoảng trống, không tốn lượt gọi.

### 2.3 RouterBench (Hu và cộng sự, 2024)

- Benchmark ngoại tuyến cho router theo câu hỏi, 11 mô hình.
- **Nội suy tuyến tính** giữa hai router bất kỳ; **bao lồi không giảm** trên mặt
  phẳng chi phí ↔ chất lượng; **zero router** = bao lồi của các mô hình đơn;
  **AIQ** = diện tích dưới bao lồi; oracle.
- **Dùng cho đồ án:** cách vẽ H1 (nối các điểm mô hình đơn làm mốc). AIQ trùng
  ý với APGR nên không dùng.

### 2.4 Router-R1 (NeurIPS 2025)

- Router là LLM (Qwen2.5-3B/7B) huấn luyện bằng PPO, xen kẽ suy nghĩ và gọi mô
  hình qua nhiều vòng; phần thưởng = định dạng + đúng kết quả (exact match) +
  phạt chi phí. 7 bộ hỏi đáp thường và nhiều bước.
- **Dùng cho đồ án:** không dùng; router tốn thêm một lần gọi LLM mỗi bước. Chỉ
  trích dẫn như một hướng.

### 2.5 MTRouter (ACL 2026)

- **Bài toán:** tối đa điểm cuối với ngân sách chi phí mỗi tập (`$2`) và giới hạn
  lượt (50 ScienceWorld, 30 HLE).
- **Đầu vào:** lịch sử (đề + các cặp hành động/quan sát, tối đa 8192 token, mã
  hóa bằng Qwen3-Embedding-0.6B cố định) và embedding mô hình (giá, ngữ cảnh,
  ngày cắt kiến thức + phần dư học được). **Đầu ra:** điểm cuối dự đoán nếu chọn
  mô hình đó ở lượt này; chọn mô hình có điểm cao nhất (Q = điểm, không trừ chi
  phí; chi phí đã nằm trong điểm cuối nhờ ngân sách).
- **Nhãn:** điểm cuối của cả tập, trừ phạt lỗi (AEC) cho các lỗi từ lượt t trở
  đi, lỗi càng muộn phạt càng nặng. Lỗi phát hiện bằng luật trên quan sát (lỗi
  định dạng, lỗi Python, lỗi công cụ).
- **Dữ liệu:** quỹ đạo của **router ngẫu nhiên đều mỗi lượt** cộng quỹ đạo **mô
  hình đơn**. Tổng 29.693 quỹ đạo, 515.221 lượt, khoảng **$1.620**.
- **Đánh giá:**
  - ID và ngoài phân phối (giữ lại cả loại tác vụ / nhóm môn).
  - Chia biến thể 60/20/20, seed 42, ghi "DO NOT MODIFY after data collection
    begins".
  - **Lặp 3 lần, báo trung bình**; ablation có khoảng tin cậy 95%.
  - Mốc: 6 mô hình đơn; router **một lần ở đầu tập** (RouterDC, EmbedLLM,
    AvengersPro, giữ mô hình cả tập); router nhiều lượt (ngẫu nhiên, LLM-router,
    Router-R1, OpenRouter).
  - Kết quả: ScienceWorld 53,8 vs GPT-5 48,4, chi phí −58,7%; HLE 26,0% vs
    25,1%, chi phí −43,4%. Các router một lần ở đầu tập đều kém MTRouter.
- **Ablation:** Ridge thay MLP (−4,7), bỏ dữ liệu router ngẫu nhiên (−6,6), bỏ
  phạt lỗi (−5,3), bỏ lịch sử (−9,2), mã hóa mô hình cố định (−12,5) trên
  ScienceWorld. Thêm: độ nhạy ngân sách 0,5×/1×/2×.
- **Phân tích "vì sao chạy được"** (đáng làm theo):
  - Chi phí tích lũy theo số lần đổi mô hình: MTRouter đổi khoảng 5 lần mỗi tập,
    Router-R1 khoảng 20.
  - **Sau một lỗi**: MTRouter giữ nguyên mô hình 80–90% số lần, Router-R1 chỉ
    38–66%; mà xác suất lượt sau hồi phục của MTRouter **cao hơn**. Tác giả kết
    luận chiến lược "lỗi thì nâng lên mô hình mạnh" thường không đáng.
  - Tỷ lệ dùng mô hình theo vị trí lượt; mức chuyên môn hóa mô hình theo công cụ.
- **Router luật trong mã** (`routers/heuristic.py`): bước đầu mạnh, lỗi công cụ
  thì mạnh, giai đoạn cuối dùng mô hình "giá trị", còn lại rẻ. Gần như trùng
  chính sách `rule` của đồ án.
- **Mã:** `ZhangYiqun018/MTRouter`.
- **Dùng cho đồ án:** công thức gói F (nhãn từ kết quả cuối, dữ liệu ngẫu nhiên
  + mô hình đơn, mô hình hồi quy), mốc theo câu hỏi, cách lặp 3 lần, các phân
  tích sau lỗi. Quy mô dữ liệu của họ lớn hơn đồ án hàng trăm lần, nên đồ án
  dùng mô hình nhỏ hơn nhiều.

### 2.6 EvoRoute (ACL 2026)

- **Cách làm**, trước mỗi bước của hệ nhiều agent:
  1. **Truy xuất** bản ghi cũ khớp ít nhất một trong ba: cùng vai trò agent;
     chỉ dẫn giống về ngữ nghĩa (MiniLM, cosine ≥ 0,85); công cụ dự đoán trùng
     (từ khóa, không khớp thì hỏi Qwen3-14B).
  2. Với mỗi mô hình, tính trung bình hiệu năng, chi phí, độ trễ trên các bản
     ghi đó; **lọc Pareto**.
  3. **Thompson sampling** với hậu nghiệm Normal-Inverse-Gamma, tiện ích
     `1,0·P − 0,1·C − 0,05·D`.
  4. Ghi kết quả bước mới vào kho.
- **Nhãn của bước** (câu hỏi trước đây chưa rõ): hiệu năng của bước =
  **thành công của cả tác vụ P(τ) × σt**, với σt = 1 nếu lệnh gọi công cụ ở bước
  đó chạy không lỗi.
- **Khởi động lạnh:** 50 tác vụ TaskCraft, **chọn mô hình ngẫu nhiên đều ở mỗi
  bước**, khoảng 480 bản ghi bước, tốn **$28,8** và 3,6 giờ. Đây là thành phần
  quan trọng nhất theo ablation.
- **Đánh giá:**
  - Ba khung agent: ReAct, Smolagents, CK-Pro. Nhóm 6 mô hình (Qwen3-14B tới
    Claude-4, Gemini-2.5-Pro).
  - 5 bộ: GAIA, BrowseComp+, và DS-1000 (khoa học dữ liệu), HotpotQA, DDXPlus
    từ StreamBench.
  - Mốc: cấu hình mô hình gốc và từng mô hình đơn; router PromptLLM,
    GraphRouter, MasRouter.
  - Chỉ số: hiệu năng %, tổng chi phí $, tổng độ trễ (giờ). **Một lần chạy,
    không có khoảng tin cậy.**
  - GAIA + CK-Pro: 63,19% vs Claude-4 58,28%, chi phí $85,40 vs $359,32.
    DS-1000 + ReAct: 56,5% vs Gemini-2.5-Pro 54,3%, chi phí $23,5 vs $68,3.
- **Ablation** (chỉ trên GAIA Level 1, 53 câu): bỏ khởi động lạnh −13,21; chỉ
  dùng truy xuất ngữ nghĩa −11,22; tham lam thay Thompson −6,52; bỏ Pareto −1,52.
- **Phân tích:** phân bố mô hình theo vai trò agent (planner dùng mô hình mạnh,
  file agent dùng mô hình rẻ).
- **Dùng cho đồ án:** bài gần nhất về ý tưởng, bắt buộc trích dẫn. Lấy: cách gán
  nhãn bước (kết quả cuối × bước không lỗi công cụ), khởi động lạnh bằng chọn
  ngẫu nhiên đều (quy mô gần với tập phát triển của đồ án), ablation bỏ từng
  thành phần. Khác đồ án: đọc chỉ dẫn của bước, nhiều agent có vai trò, học trực
  tuyến, không có khoảng tin cậy.

### 2.7 AgentRouter (Paul và Nandy, 2026)

- **Nơi đăng:** PDF dùng mẫu ICML 2026 (PMLR 306); trang hội nghị liệt kê ở
  workshop AgenticUQ. Ghi là "ICML 2026" và kiểm tra lại khi trích dẫn.
- **Cách làm:** MLP 2 lớp (256, 128), 4 mức mô hình. **5 đặc trưng trước khi
  gọi**: loại tác vụ của bước (từ khóa + BERT tagger 2M **đọc chỉ dẫn bước**), độ
  sâu suy luận (đếm mệnh đề, từ như "compare", "synthesize"), số lệnh gọi công
  cụ, định dạng đầu ra, tỷ lệ dùng cửa sổ ngữ cảnh.
- **Nhãn:** chạy **mọi bước với cả 4 mức**; nhãn = mức rẻ nhất có chất lượng
  trong 2% của mức mạnh nhất; gán nhãn bước t với giả định các bước trước đã chạy
  ở mức nhãn của chúng. 50.000 bước từ 6.250 quỹ đạo. Hàm mất mát phạt chọn
  thiếu nặng hơn chọn thừa.
- **Giả định quan trọng:** biết trước hoặc ước lượng được **chuỗi bước** (mẫu quỹ
  đạo hoặc kế hoạch); tác giả ghi rõ agent hoàn toàn trực tuyến nằm ngoài phạm vi.
  Có bước dự phòng chạy lại bằng mô hình mạnh khi chất lượng cuối thấp, và khi
  đánh giá thì dùng nhãn thật để quyết định bước này.
- **Đánh giá:** 2.400 quỹ đạo giữ lại, 4 miền (tài liệu, mã, nghiên cứu, **phân
  tích dữ liệu**); chất lượng tương đối so với chỉ dùng mức mạnh nhất, % giảm chi
  phí, độ trễ định tuyến. Một lần chạy. Kết quả: chất lượng 97,3%, chi phí −71,8%;
  RouteLLM theo bước −31,4%, FrugalGPT −44,1%; phân tích dữ liệu −74,1%.
- **Ablation:** thay từng đặc trưng bằng phân phối biên; loại tác vụ quan trọng
  nhất. Trung vị 3,2 lần đổi mức mỗi quỹ đạo.
- **Dùng cho đồ án:** ý "không gọi LLM khi định tuyến", cách ablation đặc trưng.
  Cách gán nhãn bằng chạy mọi bước với mọi mức **quá đắt** cho đồ án, và giả định
  biết trước chuỗi bước **không đúng** với Hermes.

### 2.8 TRIM (ICLR 2026)

- **Cách làm:** mô hình rẻ (Qwen2.5-3B) sinh từng bước; PRM (Qwen2.5-Math-PRM-7B)
  chấm; chính sách chọn tiếp tục hay để mô hình mạnh (Claude 3.7 Sonnet) **sinh
  lại bước đó**, rồi trả quyền về mô hình rẻ.
  - TRIM-Thr: ngưỡng trên điểm PRM của bước.
  - TRIM-Agg: RL trên đặc trưng rút gọn (điểm bước hiện tại, điểm thấp nhất các
    bước trước, độ dài bước, **vị trí bước**).
  - TRIM-POMDP: coi đúng/sai của bước là ẩn.
- **Chi phí:** số token **mô hình mạnh sinh ra**, chuẩn hóa theo khi chỉ dùng mô
  hình mạnh.
- **Chỉ số:** CPT(50/80/95%), PGR, **ΔIBC** (hiệu quả cải thiện trên mỗi token
  mạnh so với luôn dùng mô hình mạnh, trung bình trên 100 vùng hiệu năng), độ
  chính xác ở ngân sách cố định.
- **Mốc:** RouteLLM (BERT, MF, SW), Smoothie, AutoMix (và bản AutoMix-PRM mạnh
  hơn). Chuyển giao: huấn luyện trên AIME, thử trên OlympiadBench, Minerva Math.
- **Nhận xét của tác giả:** router theo câu hỏi kiểu RouteLLM khớp vào đặc điểm
  bộ dữ liệu và **chuyển giao kém**.
- **Dùng cho đồ án:** CPT(95%), token mô hình mạnh làm chỉ số chi phí phụ, đặc
  trưng vị trí bước. Cơ chế "sinh rồi chấm" cần hai lần gọi một bước, Hermes
  không cho.

### 2.9 Agentic Routing: The Harness-Native Data Flywheel (TokenRhythm, 2026)

- **Loại bài:** báo cáo kỹ thuật của công ty (OpenSquilla, `opensquilla/opensquilla`),
  chưa qua bình duyệt.
- **Cách làm:** định tuyến theo bước, **dựa trên trạng thái harness**: áp lực ngữ
  cảnh, lịch sử công cụ, lỗi công cụ, trạng thái hồi phục, các quyết định định
  tuyến gần đây, tín hiệu kiểm tra. Bốn lớp: lọc yêu cầu đơn giản, xây nhu cầu
  năng lực, định giá rủi ro, ghép mô hình bằng **LightGBM trên đặc trưng tự
  thiết kế**. Có thêm chế độ nhiều mô hình + tổng hợp.
- **Dữ liệu:** mỗi quyết định ghi thành bản ghi (trạng thái, lựa chọn, vết thực
  thi, kết quả, chi phí), nhãn do môi trường cấp; dùng để huấn luyện các thế hệ
  router sau.
- **Đánh giá:** PinchBench, DRACO; **một lần chạy đầy đủ mỗi chính sách**, chỉ báo
  cáo tổng (điểm, chi phí, token, độ trễ); không có phân tích từng quyết định,
  không có khoảng tin cậy. PinchBench: 93,14 vs Opus 93,35 với chi phí 9%; DRACO:
  chi phí −43%. Có so với cấu hình MoA của Hermes Agent.
- **Dùng cho đồ án:** **gần nhất về đặc trưng** (trạng thái harness thay vì nội
  dung) và về mô hình nhỏ dễ giải thích. Khác đồ án: vẫn dùng độ dài và loại tác
  vụ của prompt, harness riêng, đánh giá chỉ ở mức tổng.

### 2.10 Budget-Aware Agentic Routing (Zhang và cộng sự, Cambridge + Microsoft, 2026)

- **Mã arXiv đúng là 2602.21227** (bản PDF). AgentRouter trích sai thành 2512.21227.
- **Thiết lập: đúng như đồ án.** Ở mỗi bước, chọn giữa **một mô hình rẻ và một
  mô hình đắt** (GPT-4.1 mini và GPT-4.1, giá chênh 5 lần; thêm cặp Llama-3.1
  8B/72B). Agent kiểu ReAct, trung bình khoảng 20 bước mỗi tác vụ. Hai chế độ:
  - **ngân sách mềm**: tối đa `thành công − λ · chi phí`, quét λ để ra đường;
  - **ngân sách cứng**: tối đa K lần gọi mô hình đắt mỗi tác vụ.
- **Router:** Qwen2.5-1.5B **đọc toàn bộ lịch sử** (đề, hành động, kết quả công
  cụ), sinh một token rẻ/đắt.
- **Phân loại độ khó** từ hai chính sách biên, mỗi câu chạy luôn rẻ và luôn đắt
  **5 lần**:
  - Dễ: luôn rẻ đúng ít nhất 4/5 lần;
  - Khó: luôn rẻ không chắc đúng nhưng luôn đắt làm được;
  - Không giải được: luôn đắt sai cả 5 lần. Chính sách đúng ở nhóm này là "thua
    rẻ": dùng mô hình rẻ.
- **Huấn luyện** hai giai đoạn:
  1. **BoSFT**: câu dễ và câu không giải được lấy quỹ đạo luôn rẻ làm mẫu. Câu
     khó chạy **10–20 quỹ đạo ngẫu nhiên với xác suất chọn mô hình đắt quét từ
     0 tới 1**, lấy **quỹ đạo thành công rẻ nhất** làm mẫu.
  2. **BoPO** (biến thể GRPO): phần thưởng tính tương đối theo hai biên, có
     thưởng thêm khi giải được câu khó; ưu thế so với cả trung bình nhóm và quỹ
     đạo mẫu để tránh sụp về "luôn rẻ".
  - Ngân sách cứng thực thi lúc chạy bằng cách cấm mô hình đắt khi hết ngân sách.
- **Đánh giá:**
  - Ba bộ: SciWorld, ALFWorld, AppWorld. Chỉ số: tỷ lệ thành công, **chi phí
    trung bình mỗi tác vụ ($)**; ngân sách cứng thêm % số lần gọi đắt đã dùng.
    **Trung bình 3 seed.** Chi phí của chính router không tính vào.
  - Khoảng cách luôn rẻ / luôn đắt: ALFWorld 40,0 / 63,5; SciWorld 43,5 / 65,4;
    AppWorld 53,0 / 66,7.
  - **Mốc**:
    - luôn rẻ, luôn đắt;
    - **ngẫu nhiên với p quét ra một đường**;
    - GPT-5 làm router không huấn luyện;
    - cascade kiểu FrugalGPT;
    - router kiểu RouteLLM áp tham lam cho từng bước;
    - chỉ SFT; RL thường (DAPO);
    - ngân sách cứng: **First-Large** (dùng mô hình đắt K bước đầu rồi chuyển rẻ).
  - **Hình chính:** đường Pareto tỷ lệ thành công theo chi phí mỗi tác vụ.
- **Kết quả đáng chú ý:**
  - BoPO đẩy đường Pareto lên ở cả ba bộ.
  - **RL thường chỉ ngang ngẫu nhiên** ở ALFWorld và AppWorld.
  - GPT-5 làm router thường **sụp về một lựa chọn cố định cho cả quỹ đạo**.
  - Ở ngân sách cứng, **First-Large rất mạnh**, gần các phương pháp phức tạp:
    bước đầu đúng hướng thường quyết định kết quả.
- **Ablation** (SciWorld): bỏ lấy mẫu phân tầng, bỏ ưu thế tham chiếu, bỏ thưởng
  câu khó. **Phân tích**: tỷ lệ chi phí mỗi router tiêu vào nhóm dễ / khó / không
  giải được (BoPO tiêu 52% vào câu khó, ngẫu nhiên tiêu đều, RL thường phí 37,5%
  vào câu không giải được). Độ trễ thêm của router dưới 2%.
- **Dùng cho đồ án:**
  - **phân loại độ khó** từ run luôn rẻ / luôn mạnh;
  - **phân tích tiêu chi phí theo độ khó**;
  - **mốc First-Large** (gần chính sách `rule` với bước đầu mạnh);
  - đường ngẫu nhiên quét p;
  - **cách gán nhãn bằng quỹ đạo thành công rẻ nhất** trong các run ngẫu nhiên.

  Khác đồ án: router là LLM 1,5B đọc toàn bộ nội dung, huấn luyện SFT + RL; không
  tính cache.
- Bài này trích thêm: xRouter (arXiv 2510.08439); AgentRouter trích DAAO
  (arXiv 2509.11079).

### 2.11 InfiAgent-DABench (ICML 2024)

- 257 câu, 52 file CSV; mức dễ / trung bình / khó: 82 / 87 / 88 câu; 461 câu
  con, tối đa 8 câu con mỗi câu.
- Chỉ số ABQ (đúng mọi câu con), PSAQ (trung bình tỷ lệ câu con đúng), UASQ
  (tổng câu con đúng).
- Mô hình tốt nhất trong bài là **GPT-4 với 78,99%** (đầu 2024).
- Quy trình gốc có bước **dùng GPT-3.5 viết lại câu trả lời** theo định dạng
  trước khi chấm. Ablation của tác giả: bỏ bước này thì GPT-4 giảm khoảng 6
  điểm, **mô hình yếu giảm tới 32 điểm**.
- **Hệ quả cho đồ án:** bộ chấm của đồ án đưa định dạng vào prompt và không viết
  lại. Mô hình rẻ có thể thua vì định dạng chứ không vì phân tích, làm khoảng
  cách mạnh/rẻ lớn giả tạo. Cần đo riêng tỷ lệ câu trả lời thiếu cụm `@ten[...]`
  ở pilot. Mô hình mạnh hiện nay có thể gần bão hòa ở mức dễ, nên khoảng cách
  có thể dồn vào mức khó.

### 2.12 Cài đặt mã nguồn mở

- **StepRouter** (`shipiit/shipit_agent`, PR #33): lập kế hoạch, khôi phục sau
  công cụ lỗi và câu trả lời cuối dùng mô hình mạnh; bước đọc kết quả công cụ
  dùng mô hình nhanh. Nếu mô hình nhanh bắt đầu trả lời thay vì gọi công cụ thì
  bỏ đi và chạy lại bằng mô hình mạnh. Không có số liệu đánh giá.
- Một bài viết thực nghiệm (dev.to) cho thấy router có thể tốn gấp 8 lần mà chỉ
  đạt độ chính xác của mô hình rẻ. Vì vậy hai mốc luôn rẻ và luôn mạnh là bắt buộc.

## 3. So sánh với thiết kế của đồ án

| | RouteLLM | MTRouter | EvoRoute | AgentRouter | TRIM | Agentic Routing | Budget-Aware | Đồ án |
|---|---|---|---|---|---|---|---|---|
| Đơn vị | Câu hỏi | Lượt | Bước / sub-task | Bước (chuỗi bước biết trước) | Bước suy luận | Bước | Bước | Lần gọi mô hình, hoàn toàn trực tuyến |
| Số mô hình | 2 | 6 | 6 | 4 mức | 2 | 3 | **2** | **2** |
| Đọc nội dung | Có | Có (nhúng lịch sử) | Có (nhúng chỉ dẫn) | Có (chỉ dẫn bước) | Có (PRM) | Một phần (prompt + trạng thái harness) | Có (LLM đọc toàn bộ lịch sử) | **Không** |
| Thời điểm quyết định | Trước | Trước | Trước | Trước, có chạy lại cuối | **Sau khi sinh** | Trước | Trước | Trước |
| Gọi thêm mô hình để định tuyến | Tùy loại | Không | Đôi khi (dự đoán công cụ) | Không | Có (PRM) | Không | Có (LLM 1,5B mỗi bước) | Không |
| Học | Có giám sát | Có giám sát từ log | Trực tuyến | Có giám sát, nhãn từ chạy mọi mức | Ngưỡng / RL | LightGBM từ log | SFT + RL | Hồi quy hoặc ngưỡng từ log, khóa trước tập kiểm tra |
| Chi phí dữ liệu | Arena có sẵn | ~$1.620 | $28,8 khởi động | Chạy 4 mức mọi bước | Huấn luyện RL | Không rõ | 5 run mỗi biên + 10–20 run ngẫu nhiên mỗi câu khó + RL | Nằm trong ngân sách gói E |
| Tính chi phí cache | Không | Không | Không | Không (chỉ nhắc số lần đổi) | Không | Không | Không | **Có** (quán tính) |
| Lặp / khoảng tin cậy | Ngoại tuyến | 3 lần; CI ở ablation | Một lần | Một lần | Không rõ | Một lần | 3 seed, trung bình | 2 lần cho cấu hình chính; CI bootstrap |

## 4. Hệ quả cho đồ án

**Đóng góp nên đặt ở:**

1. Bộ định tuyến **không đọc nội dung tin nhắn**, chỉ dùng tín hiệu hành vi của
   agent. Mọi bài ở bảng trên đều đọc nội dung ở mức nào đó; Agentic Routing gần
   nhất nhưng vẫn dùng prompt. Budget-Aware có cùng thiết lập nhị phân theo bước
   nhưng router là một LLM đọc toàn bộ lịch sử và cần SFT + RL; đồ án hỏi được gì
   với một router nhỏ, không đọc nội dung, không huấn luyện RL.
2. **Chi phí mất prompt cache** khi đổi mô hình được đưa vào quyết định và đo
   riêng. Không bài nào làm.
3. **Hoàn toàn trực tuyến trong một agent thật** (Hermes), một lần gọi mỗi bước,
   không biết trước chuỗi bước, chỉ cắm qua middleware. AgentRouter cần biết
   trước chuỗi bước; TRIM cần hai lần gọi.
4. **Quy trình đánh giá chặt**: tách tập phát triển/kiểm tra theo file, khóa
   router trước tập kiểm tra, lặp lại, khoảng tin cậy, mốc ngẫu nhiên và oracle.
   EvoRoute, AgentRouter, Agentic Routing chỉ báo cáo một lần chạy.
5. **Kiểm tra giả thuyết "lỗi thì nâng lên mô hình mạnh"**: MTRouter cho thấy nó
   thường không đáng; chính sách `rule` của đồ án và StepRouter dựa vào nó. Dữ
   liệu chính sách ngẫu nhiên cho phép đo trực tiếp hiệu quả của việc nâng lên
   sau lỗi.
6. Miền **phân tích dữ liệu** (DABench) và một bộ **tiếng Việt**.

**Mốc so sánh cần có** (chi tiết trong [thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md)):

- luôn mạnh, luôn rẻ, ngẫu nhiên (theo RouteLLM, MTRouter, EvoRoute);
- luật đơn giản (`rule`, trùng router luật của MTRouter) và **First-Large** (mô
  hình mạnh K bước đầu rồi rẻ; mạnh bất ngờ trong Budget-Aware);
- theo câu hỏi: router RouteLLM và oracle, ngoại tuyến (MTRouter cũng dùng mốc
  "một lần ở đầu tập");
- nếu kịp: bản rút gọn của EvoRoute cho hai mô hình.

**Việc còn lại của gói C:**

- [x] Đọc toàn văn 10 bài trong `papers/`.
- [x] Đọc mã MTRouter, RouteLLM, RouterBench.
- [x] Đọc Budget-Aware Agentic Routing (arXiv 2602.21227), gần nhất về thiết lập
  nhị phân theo bước.
- [ ] Chạy router của RouteLLM ngoại tuyến trên prompt DABench, tra kết quả từ
  hai lần chạy luôn mạnh và luôn rẻ.
- [ ] Gửi Thầy ghi chú và bảng so sánh mục 3.

## 5. Phần thực nghiệm của các bài

| Bài | Benchmark | Mốc so sánh | Chỉ số | Hình chính | Ablation, phân tích thêm | Lặp, thống kê |
|---|---|---|---|---|---|---|
| RouteLLM | MT-Bench, MMLU, GSM8K | Ngẫu nhiên (trung bình 10 lần), oracle, router thương mại | Độ chính xác, % gọi mạnh, **APGR**, **CPT(20/50/80%)** | Độ chính xác theo % gọi mạnh, kèm đường mạnh, yếu, oracle | Tăng cường dữ liệu; kiểm tra trùng dữ liệu; đổi cặp mô hình | Ngoại tuyến trên kết quả tính sẵn |
| FrugalGPT | HEADLINES, OVERRULING, COQA… | Từng mô hình đơn | Độ chính xác, chi phí USD | Đường chi phí ↔ độ chính xác theo ngân sách | **MPI**: % câu mô hình đắt sai mà rẻ đúng | Một lần |
| RouterBench | 8 bộ, 11 mô hình | Từng mô hình, oracle, zero router | Chất lượng, chi phí, AIQ | Mặt phẳng chi phí ↔ chất lượng với bao lồi | Tần suất oracle chọn từng mô hình | Ngoại tuyến |
| MTRouter | ScienceWorld, HLE; ngoài phân phối | 6 mô hình đơn; router một lần ở đầu tập; ngẫu nhiên, LLM-router, Router-R1, OpenRouter | Điểm / độ chính xác, tổng chi phí, % giảm so với mô hình mạnh nhất | Bảng chính; chi phí theo số lần đổi | 5 ablation; ngân sách 0,5–2×; **đổi mô hình sau lỗi và hồi phục**; dùng mô hình theo vị trí lượt | **3 lần, trung bình; CI 95% ở ablation** |
| EvoRoute | GAIA, BrowseComp+, DS-1000, HotpotQA, DDXPlus | Cấu hình gốc, mô hình đơn; PromptLLM, GraphRouter, MasRouter | Hiệu năng %, tổng chi phí $, tổng độ trễ | Bảng chính; radar | 4 ablation trên 53 câu; phân bố mô hình theo vai trò | Một lần |
| AgentRouter | 2.400 quỹ đạo, 4 miền | Chỉ mức mạnh nhất; RouteLLM, FrugalGPT theo bước; DAAO; Budget-Aware | Chất lượng tương đối, % giảm chi phí, độ trễ định tuyến, độ chính xác gán mức | — | Ablation đặc trưng; theo miền; số lần đổi mức | Một lần |
| TRIM | MATH-500, AIME, OlympiadBench, Minerva, GPQA | RouteLLM, AutoMix, AutoMix-PRM, Smoothie | **Token mô hình mạnh**, PGR, CPT(50/80/95%), ΔIBC, độ chính xác theo ngân sách | Độ chính xác theo token mô hình mạnh | Chính sách ngưỡng / RL / POMDP; chuyển bộ; độ trễ thực | Không rõ |
| Agentic Routing | PinchBench, DRACO | Opus cố định, OpenRouter Auto, Hermes MoA | Điểm, chi phí mỗi tác vụ, token, độ trễ p50/p95 | Bảng điểm làm việc | — | Một lần |
| Budget-Aware | SciWorld, ALFWorld, AppWorld | Luôn rẻ/đắt; ngẫu nhiên quét p; GPT-5 router; cascade; RouteLLM theo bước; SFT; RL thường; First-Large | Tỷ lệ thành công, chi phí mỗi tác vụ; ngân sách cứng: % gọi đắt | Đường Pareto thành công theo chi phí | 3 ablation; **chi phí theo nhóm độ khó**; cặp mô hình khác; độ trễ | 3 seed, trung bình |

Điểm chung, dùng để chọn thực nghiệm cho đồ án trong
[thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md#2-chọn-từ-các-bài-liên-quan):

- **Bài nào cũng có:** bảng chính gồm từng mô hình đơn, các router mốc và router
  đề xuất, với độ chính xác và chi phí.
- **Hình chính** là đường hoặc mặt phẳng chi phí ↔ chất lượng, có mốc mô hình
  mạnh, mô hình yếu, ngẫu nhiên và thường cả oracle.
- **Lặp lại ít.** MTRouter và Budget-Aware lặp 3 lần; các bài khác một lần.
- **Ablation** bỏ từng thành phần hoặc từng đặc trưng, thường trên một tập nhỏ.
- **Phân tích hành vi** (MTRouter, EvoRoute, AgentRouter): số lần đổi mô hình,
  mô hình dùng ở vị trí nào hoặc vai trò nào, phản ứng sau lỗi.

## 6. Nguồn

Toàn văn: thư mục `papers/` trong repo.

- RouteLLM: <https://arxiv.org/abs/2406.18665>, mã <https://github.com/lm-sys/RouteLLM>
- FrugalGPT: <https://arxiv.org/abs/2305.05176>, mã <https://github.com/stanford-futuredata/FrugalGPT>
- RouterBench: <https://arxiv.org/abs/2403.12031>, mã <https://github.com/withmartian/routerbench>
- Router-R1: <https://arxiv.org/abs/2506.09033>, mã <https://github.com/ulab-uiuc/Router-R1>
- MTRouter: <https://aclanthology.org/2026.acl-long.2045/>, <https://arxiv.org/abs/2604.23530>, mã <https://github.com/ZhangYiqun018/MTRouter>
- EvoRoute: <https://aclanthology.org/2026.acl-long.1771/>, <https://arxiv.org/abs/2601.02695>
- AgentRouter: <https://arxiv.org/abs/2609.22951>, <https://icml.cc/virtual/2026/68040>
- TRIM: <https://arxiv.org/abs/2601.10245>
- Agentic Routing: <https://arxiv.org/abs/2607.11399>, mã <https://github.com/opensquilla/opensquilla>
- InfiAgent-DABench: <https://arxiv.org/abs/2401.05507>
- Budget-Aware Agentic Routing: <https://arxiv.org/abs/2602.21227>
- StepRouter: <https://github.com/shipiit/shipit_agent/pull/33>
- Bài viết dev.to: <https://dev.to/priyansh_singhal_5975e7d3/my-llm-router-spent-8x-more-and-matched-the-cheap-model-exactly-18d9>
