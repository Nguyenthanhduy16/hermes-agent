# Gói C: các công trình định tuyến mô hình liên quan

Cập nhật 12/11/2026. Ghi chú cho gói C trong [kế hoạch đồ án](ke-hoach-do-an.md):
ai đã làm định tuyến mô hình, họ nhận gì, trả gì, học từ dữ liệu nào, đánh giá
ra sao, và so với thiết kế của đồ án ([khung định tuyến](dinh-tuyen.md)).

**Mức kiểm chứng.** RouteLLM và FrugalGPT tóm tắt theo bài báo và mã nguồn
công khai. Các bài năm 2026 (EvoRoute, MTRouter, AgentRouter, TRIM, Agentic
Routing) mới tóm tắt theo abstract, trang hội nghị và trang review, **chưa đọc
toàn văn**. Con số của các bài này là số tác giả tự báo cáo. Đọc lại bài gốc
trước khi đưa vào báo cáo cuối. Chưa chạy lại mã của bài nào.

## 1. Bức tranh chung

Các công trình chia theo **đơn vị được định tuyến**:

| Nhóm | Định tuyến cái gì | Công trình |
|---|---|---|
| Theo câu hỏi | Cả một câu hỏi đi tới một mô hình | RouteLLM, FrugalGPT, RouterBench, RouterArena |
| Theo lượt / nhiều vòng | Mỗi lượt trong một tác vụ nhiều lượt | Router-R1, MTRouter |
| Theo bước của agent | Mỗi lần gọi mô hình hoặc mỗi sub-task trong vòng lặp agent | EvoRoute, AgentRouter, Agentic Routing, StepRouter (shipit_agent) |
| Theo bước suy luận | Mỗi bước trong chuỗi suy luận toán | TRIM |

Đồ án thuộc nhóm thứ ba. **Định tuyến theo từng bước trong vòng lặp agent đã có
người làm và đã đăng ở hội nghị lớn năm 2026**, nên đó không còn là đóng góp
mới. Đóng góp cần đặt vào cách làm và điều kiện làm (mục 4).

## 2. Từng công trình

### RouteLLM (Ong và cộng sự, 2024)

- **Đầu vào:** nội dung câu hỏi. **Đầu ra:** xác suất mô hình mạnh thắng mô
  hình yếu, so với ngưỡng để chọn một trong hai mô hình.
- **Bộ định tuyến:** bốn loại: phân rã ma trận, bộ phân loại BERT, LLM nhỏ
  làm bộ phân loại, xếp hạng theo độ tương tự có trọng số.
- **Dữ liệu huấn luyện:** cặp so sánh ưa thích của Chatbot Arena, bổ sung dữ
  liệu có nhãn tự động bằng LLM chấm.
- **Đánh giá:** MT-Bench, MMLU, GSM8K. Chỉ số **PGR** (phần khoảng cách chất
  lượng giữa mô hình yếu và mạnh mà bộ định tuyến lấy lại được), **APGR** (PGR
  trung bình trên mọi mức chi phí) và **CPT(x%)** (tỷ lệ gọi mô hình mạnh tối
  thiểu để đạt x% PGR). Đường cơ sở là bộ định tuyến ngẫu nhiên.
- **Mã:** `lm-sys/RouteLLM`.
- **Dùng cho đồ án:** lấy cách đo (PGR, APGR, CPT, đường ngẫu nhiên) cho
  [thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md). Làm mốc "định tuyến theo
  câu hỏi": quyết định một lần ở bước đầu, giữ cả lượt.

### FrugalGPT (Chen và cộng sự, 2023)

- **Cách làm:** cascade. Gọi lần lượt từ mô hình rẻ tới đắt; một bộ chấm nhỏ
  (DistilBERT) cho điểm câu trả lời; đạt ngưỡng thì dừng.
- **Học:** thứ tự mô hình và ngưỡng của từng tầng, tối ưu trên tập có nhãn với
  ràng buộc ngân sách.
- **Mã:** `stanford-futuredata/FrugalGPT`.
- **Dùng cho đồ án:** không làm theo được trực tiếp, vì cascade cần **gọi nhiều
  lần trong một bước**, điều Hermes không cho (mục 3 của
  [kế hoạch](ke-hoach-do-an.md)). Tương đương gần nhất trong Hermes là nâng lên
  mô hình mạnh ở bước kế tiếp.

### Router-R1 (2025)

- **Cách làm:** bộ định tuyến là một LLM, xen kẽ suy nghĩ và gọi mô hình ứng
  viên qua nhiều vòng, rồi tổng hợp. Huấn luyện bằng RL, phần thưởng gồm đúng
  định dạng, đúng kết quả và phạt chi phí.
- **Đầu vào về mô hình:** chỉ mô tả đơn giản (giá, độ trễ, ví dụ), nên chuyển
  được sang mô hình chưa thấy.
- **Đánh giá:** 7 bộ hỏi đáp thường và nhiều bước.
- **Dùng cho đồ án:** không dùng. Bộ định tuyến tốn thêm một lần gọi LLM mỗi
  bước, trái với mục tiêu giảm chi phí.

### MTRouter (ACL 2026)

- **Đầu vào:** lịch sử tương tác và từng mô hình ứng viên, nhúng chung vào một
  không gian. **Đầu ra:** kết quả dự đoán nếu dùng mô hình đó ở lượt này; chọn
  mô hình có giá trị cao nhất sau khi trừ chi phí.
- **Dữ liệu huấn luyện:** các quỹ đạo đã ghi lại, mỗi lượt gắn mô hình đã dùng
  và kết quả cuối.
- **Đánh giá:** ScienceWorld, HLE; có tập ngoài phân phối. Tác giả báo cáo
  điểm ScienceWorld từ 48,4 (GPT-5) lên 53,8, chi phí giảm 58,7%; và đổi mô
  hình ít hơn Router-R1.
- **Mã:** `ZhangYiqun018/MTRouter`.
- **Dùng cho đồ án:** gần nhất với gói F. Học từ log quỹ đạo, đúng loại dữ liệu
  chính sách `random` sinh ra. Kết quả "đổi ít hơn" ủng hộ ý quán tính của đồ án.
  **Ứng viên chính để chạy lại.**

### EvoRoute (ACL 2026)

- **Cách làm:** mỗi bước hoặc sub-task trong hệ nhiều agent được giao cho một
  mô hình trong nhóm nhiều mô hình:
  1. kho kinh nghiệm lưu bản ghi từng bước (vai trò agent, chỉ dẫn, công cụ,
     chi phí, độ trễ, thành công), lớn dần sau mỗi tác vụ;
  2. gặp bước mới thì truy xuất bản ghi giống theo vai trò, độ giống ngữ nghĩa
     của chỉ dẫn và công cụ dự đoán;
  3. lọc Pareto trên ba trục chính xác, chi phí, độ trễ;
  4. chọn bằng Thompson sampling để còn thăm dò; chưa có kinh nghiệm thì chọn
     đều.
- **Học:** trực tuyến, không huấn luyện bộ phân loại riêng.
- **Đánh giá:** GAIA và các bộ khác, gắn vào hệ agent có sẵn. Tác giả báo cáo
  chi phí giảm đến 80%, độ trễ giảm hơn 70%, chất lượng giữ hoặc tăng; bỏ kho
  kinh nghiệm thì kết quả giảm khoảng 13%.
- **Dùng cho đồ án:** **bài gần nhất về ý tưởng**, bắt buộc trích dẫn và so
  sánh. Chỗ chưa rõ cần đọc bài gốc: họ gán nhãn "thành công" cho từng bước thế
  nào khi benchmark chỉ chấm cả tác vụ.

### AgentRouter (ICML 2026)

- **Cách làm:** bộ phân loại 12M tham số gán mỗi bước của quỹ đạo vào một trong
  4 mức mô hình, dùng 5 đặc trưng lấy được **trước khi gọi LLM**, trễ dưới 5 ms
  mỗi bước.
- **Luận điểm:** RouteLLM và FrugalGPT coi các lần gọi là độc lập; trong agent,
  bước rẻ làm sai sẽ làm hỏng ngữ cảnh của các bước sau.
- **Dữ liệu huấn luyện:** 50.000 bước quỹ đạo đã gán nhãn.
- **Kết quả tác giả báo cáo:** chi phí giảm khoảng 72% so với chỉ dùng mô hình
  mạnh nhất, chất lượng giảm dưới 3%; slide so với RouteLLM và FrugalGPT.
- **Dùng cho đồ án:** gần nhất về **thiết kế đặc trưng**: không gọi LLM khi định
  tuyến, đặc trưng có trước lần gọi. Đồ án không có 50.000 bước có nhãn, nên bộ
  định tuyến phải nhỏ hơn và dễ giải thích hơn.

### TRIM (ICLR 2026)

- **Cách làm:** mô hình rẻ sinh một bước suy luận, mô hình chấm theo quá trình
  (PRM) cho điểm; dưới ngưỡng thì mô hình mạnh sinh lại bước đó. Bản đơn giản
  TRIM-Thr chỉ dùng ngưỡng; bản học dùng RL.
- **Đánh giá:** MATH-500, AIME, OlympiadBench, Minerva Math.
- **Dùng cho đồ án:** ngưỡng giống τ của đồ án, nhưng TRIM **chấm sau khi
  sinh**, cần hai lần gọi trong một bước. Đồ án phải **dự đoán trước khi gọi**.

### Agentic Routing: The Harness-Native Data Flywheel (2026)

- Định nghĩa định tuyến theo bước là chọn mô hình hoặc tập mô hình ở mỗi bước
  khi agent chạy, dựa trên trạng thái của harness. Chưa đọc chi tiết cách làm.

### Cài đặt mã nguồn mở

- **StepRouter** (`shipiit/shipit_agent`, PR #33): luật gần giống chính sách
  `rule` của đồ án: lập kế hoạch, khôi phục sau công cụ lỗi và câu trả lời cuối
  dùng mô hình mạnh; bước đọc kết quả công cụ dùng mô hình nhanh. Thêm một mẹo:
  giữ lại đầu ra của mô hình nhanh, nếu nó bắt đầu trả lời thay vì gọi công cụ
  thì bỏ đi và chạy lại bằng mô hình mạnh. Không có số liệu đánh giá.
- Các dự án nhỏ khác (`salimkt/small-model-agents`, `FlorianBruniaux/agent-router`,
  `DarkLvrd/model-router`) dùng luật hoặc một LLM nhỏ phân loại độ khó; ít số
  liệu đánh giá.
- Một bài viết thực nghiệm (dev.to, "My LLM Router Spent 8x More and Matched
  the Cheap Model Exactly") cho thấy bộ định tuyến có thể tốn gấp 8 lần mà chỉ
  đạt độ chính xác của mô hình rẻ. Vì vậy hai mốc luôn rẻ và luôn mạnh là bắt
  buộc.

## 3. So sánh với thiết kế của đồ án

| | RouteLLM | MTRouter | EvoRoute | AgentRouter | TRIM | Đồ án |
|---|---|---|---|---|---|---|
| Đơn vị | Câu hỏi | Lượt | Bước / sub-task | Bước | Bước suy luận | Lần gọi mô hình trong vòng lặp |
| Số mô hình | 2 | Nhiều | Nhiều | 4 mức | 2 | 2 mức |
| Đọc nội dung | Có | Có (nhúng lịch sử) | Có (chỉ dẫn) | Không rõ | Có (PRM) | **Không** |
| Thời điểm quyết định | Trước | Trước | Trước | Trước | **Sau khi sinh** | Trước |
| Gọi thêm LLM để định tuyến | Tùy loại | Không | Không | Không | Có (PRM) | Không |
| Học | Có giám sát | Có giám sát từ log | Trực tuyến | Có giám sát | Ngưỡng / RL | Chỉnh trên tập phát triển rồi khóa |
| Tính chi phí cache | Không | Không rõ | Không rõ | Không rõ | Không | **Có** (quán tính) |
| Độ trễ là mục tiêu | Không | Không | Có | Có | Không | Đo, không tối ưu |

## 4. Hệ quả cho đồ án

**Đóng góp nên đặt ở:**

1. Bộ định tuyến **không đọc nội dung tin nhắn**, chỉ dùng tín hiệu hành vi của
   agent (vị trí bước, công cụ lỗi, kích thước request). Rẻ, giải thích được,
   có lợi cho quyền riêng tư.
2. **Chi phí mất prompt cache** khi đổi mô hình được đưa vào quyết định (quán
   tính) và đo riêng.
3. Chạy trong **một agent thật** (Hermes) với ràng buộc một lần gọi mỗi bước,
   không sửa lõi agent, chỉ cắm qua middleware.
4. Đánh giá trên **phân tích dữ liệu** (DABench, DA-Code) và một bộ **tiếng
   Việt**, khác GAIA và toán.

**Mốc so sánh cần có**, chi tiết trong [thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md):

- luôn mạnh, luôn rẻ, ngẫu nhiên ở nhiều tỷ lệ (đường cơ sở kiểu RouteLLM);
- luật đơn giản (`rule`, gần StepRouter);
- định tuyến theo câu hỏi: quyết định một lần ở bước đầu, giữ cả lượt;
- oracle theo câu hỏi, tính được từ hai lần chạy luôn rẻ và luôn mạnh, không tốn
  thêm lượt gọi;
- nếu kịp: bản rút gọn của EvoRoute hoặc MTRouter cho hai mô hình, cắm qua
  `register_policy`.

**Việc còn lại của gói C:**

- [ ] Đọc toàn văn EvoRoute, MTRouter, AgentRouter, TRIM; sửa lại các con số và
  chỗ chưa rõ trong ghi chú này (đặc biệt cách gán nhãn từng bước).
- [ ] Chạy lại mã MTRouter trên ví dụ có sẵn của tác giả để xem định dạng log
  quỹ đạo và đặc trưng họ dùng.
- [ ] Chạy thử một router của RouteLLM trên prompt DABench (định tuyến theo câu
  hỏi) để làm mốc.
- [ ] Gửi Thầy ghi chú và bảng so sánh mục 3.

## 5. Nguồn

- RouteLLM: <https://github.com/lm-sys/RouteLLM>
- FrugalGPT: <https://github.com/stanford-futuredata/FrugalGPT>
- Router-R1: <https://arxiv.org/abs/2506.09033>
- MTRouter: <https://aclanthology.org/2026.acl-long.2045/>, <https://arxiv.org/abs/2604.23530>, <https://github.com/ZhangYiqun018/MTRouter>
- EvoRoute: <https://aclanthology.org/2026.acl-long.1771/>, <https://arxiv.org/abs/2601.02695>
- AgentRouter: <https://arxiv.org/abs/2609.22951>, <https://icml.cc/virtual/2026/68040>
- TRIM: <https://arxiv.org/abs/2601.10245>, <https://vansh28kapoor.github.io/trim.github.io/>
- Agentic Routing: <https://arxiv.org/abs/2607.11399>
- AgentOpt: <https://arxiv.org/abs/2604.06296>
- RouterBench: <https://arxiv.org/abs/2403.12031>; RouterArena: <https://arxiv.org/abs/2510.00202>
- StepRouter: <https://github.com/shipiit/shipit_agent/pull/33>
- Bài viết dev.to: <https://dev.to/priyansh_singhal_5975e7d3/my-llm-router-spent-8x-more-and-matched-the-cheap-model-exactly-18d9>
