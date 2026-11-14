# Thiết kế thực nghiệm: định tuyến mô hình theo từng bước

Cập nhật 12/11/2026. Tài liệu này chốt **làm gì, theo thứ tự nào, chạy cấu hình
nào, đo gì, vẽ hình gì** cho Đồ án 2. Công cụ chạy đã có: [benchmark](benchmark.md)
và [khung định tuyến](dinh-tuyen.md). Phần thực nghiệm của các bài liên quan
tóm tắt ở [gói C, mục 5](nghien-cuu-dinh-tuyen.md#5-phần-thực-nghiệm-của-các-bài).
Lịch tuần vẫn theo [kế hoạch đồ án](ke-hoach-do-an.md).

Nguyên tắc chung:

1. **Chốt câu hỏi, chỉ số và cách phân tích trước khi chạy** tập kiểm tra.
2. **Tập phát triển dùng để xây và chỉnh; tập kiểm tra chỉ chạy một lần**, sau
   khi đã khóa bộ định tuyến.
3. **Mọi cấu hình chỉ khác nhau ở đúng một biến**: chính sách định tuyến (và τ).
4. **Cái gì tính ngoại tuyến được thì không chạy lại mô hình.**
5. **Mỗi con số đi kèm khoảng tin cậy** và số câu đã chấm.

## 1. Câu hỏi nghiên cứu

| # | Câu hỏi | So sánh |
|---|---|---|
| RQ1 | Định tuyến theo bước giảm được bao nhiêu chi phí mà không làm giảm rõ độ chính xác so với luôn dùng mô hình mạnh? | Đề xuất ↔ luôn mạnh, luôn rẻ |
| RQ2 | Ở **cùng mức chi phí**, bộ định tuyến đề xuất có tốt hơn các cách đơn giản không? | Đề xuất ↔ ngẫu nhiên, luật |
| RQ3 | Định tuyến **theo bước** có lợi hơn định tuyến **theo câu hỏi** không? | Đề xuất ↔ RouteLLM chạy trên DABench, oracle theo câu hỏi |
| RQ4 | Đặc trưng nào và quán tính (chống mất cache) đóng góp bao nhiêu? | Ablation trên tập phát triển |
| RQ5 | Nâng lên mô hình mạnh ngay sau một lỗi công cụ có làm bước sau hồi phục tốt hơn không? MTRouter cho thấy thường là không | Các bước sau lỗi trong run ngẫu nhiên (lựa chọn sau lỗi là ngẫu nhiên, nên so trực tiếp được) |
| RQ6 (nếu kịp) | Bộ định tuyến đã khóa có giữ được trên bộ khác không? | DA-Code mẫu nhỏ, bộ tiếng Việt |

Viết RQ, chỉ số chính và mức chênh chấp nhận được (mục 6) thành một trang, gửi
Thầy duyệt **trước** khi chạy tập kiểm tra.

## 2. Chọn từ các bài liên quan

Chọn từ toàn văn các bài trong `papers/` (tóm tắt ở [gói C](nghien-cuu-dinh-tuyen.md)),
theo hai tiêu chí: **bài nào cũng có** (hội đồng sẽ hỏi nếu thiếu), và
**chi phí** (số lượt gọi mô hình). Ba mức: **bắt buộc**, **nên có** nếu còn
thời gian và quota, **bỏ** kèm lý do để ghi vào phần giới hạn.

### Thực nghiệm

| # | Thực nghiệm | Theo bài | Chi phí | Mức |
|---|---|---|---|---|
| E1 | Mô hình đơn: luôn mạnh (S), luôn rẻ (C) trên tập phát triển và kiểm tra | Mọi bài | Chạy thật | **Bắt buộc** |
| E2 | Router ngẫu nhiên theo bước (R) và router luật (L) | RouteLLM, MTRouter (router luật của MTRouter gần như trùng `rule`), Budget-Aware (ngẫu nhiên quét p) | Chạy thật | **Bắt buộc** |
| E2b | **First-Large** (FL): mô hình mạnh k bước đầu rồi chuyển rẻ, không nâng lên khi lỗi; k = 2 | Budget-Aware (mốc đơn giản nhưng rất mạnh) | 1 run tập phát triển | Nên có; cần thêm một chính sách nhỏ |
| E3 | **Theo câu hỏi, ngoại tuyến:** oracle và router của RouteLLM (`bert` hoặc `mf`) chấm prompt DABench, tra đúng/sai từ lần chạy S và C | RouteLLM (đánh giá ngoại tuyến), RouterBench | **Gần như 0**: không gọi mô hình agent | **Bắt buộc**; đây cũng là phần "chạy lại bài báo" Thầy yêu cầu |
| E4 | Router đề xuất: chọn τ trên tập phát triển, 2 điểm τ trên tập kiểm tra | TRIM-Thr (ngưỡng), MTRouter (học từ log), EvoRoute (nhãn bước) | Chạy thật | **Bắt buộc** |
| E7 | **Bảng bổ trợ S/C và nhóm độ khó**: số câu cả hai đúng, chỉ S đúng, chỉ C đúng, cả hai sai; chia câu thành **dễ** (C đúng mọi lần), **khó** (C không chắc đúng, S đúng ít nhất một lần), **không giải được** (S sai mọi lần) | FrugalGPT (MPI), RouteLLM (oracle), Budget-Aware (phân loại độ khó, 5 lần mỗi biên) | 0 | **Bắt buộc**: cho thấy định tuyến có bao nhiêu chỗ để lấy lại |
| E7b | **Chi phí theo nhóm độ khó**: mỗi chính sách tiêu bao nhiêu % chi phí vào câu dễ / khó / không giải được | Budget-Aware (Hình 3) | 0 | **Bắt buộc**: router tốt tiêu vào câu khó, "thua rẻ" ở câu không giải được |
| E8 | **Phân tích sau lỗi**: tỷ lệ đổi mô hình sau lỗi công cụ, tỷ lệ bước sau hồi phục theo lựa chọn; trên R50, L, P | MTRouter (Hình 4) | 0 | **Bắt buộc**, trả lời RQ5 |
| E9 | **Kiểm tra định dạng** ở pilot: tỷ lệ câu trả lời thiếu cụm `@ten[...]` của từng mô hình | DABench (bỏ bước viết lại làm mô hình yếu giảm tới 32 điểm) | 0 | **Bắt buộc** |
| E5a | Ablation **dự đoán**: bỏ từng nhóm đặc trưng, đo AUC của mô hình rủi ro bằng kiểm chứng chéo trên log tập phát triển | EvoRoute (bỏ từng thành phần) | 0 | Nên có |
| E5b | Ablation **chạy thật**: bỏ quán tính, một lần trên tập phát triển | EvoRoute | 1 run | Nên có, chỉ khi nhà cung cấp báo token cache |
| E6 | Chuyển giao sang DA-Code mẫu nhỏ và bộ tiếng Việt | MTRouter (ngoài phân phối), TRIM (chuyển bộ) | Chạy thật | Nên có; bỏ đầu tiên nếu trễ |
| — | RouteLLM / FrugalGPT áp theo từng bước | AgentRouter | Cần router đọc nội dung trong vòng lặp, hoặc cascade 2 lần gọi một bước | **Bỏ**: Hermes không cho cascade; RQ3 đã có E3 |
| — | Router-R1, tái hiện đầy đủ EvoRoute | — | Huấn luyện RL / nhiều mô hình | **Bỏ**: ngoài phạm vi và ngân sách |
| — | Gán nhãn bằng cách chạy mọi bước với mọi mô hình | AgentRouter | Nhân số lượt gọi theo số bước | **Bỏ**: quá đắt; dùng nhãn từ kết quả cuối như MTRouter, EvoRoute |
| — | Router nhúng nội dung lịch sử hoặc chỉ dẫn | MTRouter, EvoRoute | Cần mô hình embedding | **Bỏ**: trái nguyên tắc không đọc nội dung tin nhắn |
| — | LLM làm router (GPT-5 không huấn luyện), router LLM huấn luyện SFT + RL | Budget-Aware | Thêm một lần gọi LLM mỗi bước; huấn luyện RL | **Bỏ**: tốn kém; Budget-Aware cho thấy GPT-5 router hay sụp về một lựa chọn cố định, RL thường chỉ ngang ngẫu nhiên |
| — | Chế độ ngân sách cứng (tối đa K lần gọi mạnh mỗi câu) | Budget-Aware | Thêm cấu hình | **Bỏ** cho đến hết tháng 12; ghi làm hướng mở rộng |
| — | Nhóm hơn 2 mô hình, tối ưu độ trễ | MTRouter, EvoRoute | — | **Bỏ**: chỉ đo độ trễ, không tối ưu |

**Vì sao E3 rẻ.** Định tuyến theo câu hỏi dùng một mô hình cho cả câu, nên kết
quả của câu đó **chính là** kết quả trong lần chạy S hoặc C. Chỉ cần chấm điểm
prompt rồi tra bảng, đúng cách RouteLLM đánh giá. Quét ngưỡng ra cả một đường
mà không tốn thêm lượt gọi. Router `bert` chạy cục bộ; `mf` cần API embedding,
tốn vài cent.

**Vì sao không quét τ ngoại tuyến được.** Router theo bước làm quỹ đạo thay đổi,
nên kết quả không tra từ S, C được; mỗi giá trị τ là một lần chạy thật. Cách
làm: từ log tập phát triển, tính ngoại tuyến **tỷ lệ bước mạnh dự kiến** theo
τ, chọn τ cho 2 tỷ lệ mục tiêu, rồi chạy thật 2 điểm đó.

### Chỉ số

| Chỉ số | Theo bài | Mức |
|---|---|---|
| Độ chính xác theo câu, **lỗi và quá thời gian tính là sai** | DABench (ABQ), mọi bài | **Bắt buộc**, chỉ số chính |
| Chi phí quy đổi USD mỗi câu và tổng | MTRouter, EvoRoute, FrugalGPT | **Bắt buộc**, chỉ số chính |
| % lần gọi dùng mô hình mạnh | RouteLLM (trục x) | **Bắt buộc** |
| % giảm chi phí so với S | MTRouter, EvoRoute, AgentRouter | **Bắt buộc** |
| PGR ở từng điểm làm việc | RouteLLM | **Bắt buộc** |
| CPT(50%), CPT(80%) | RouteLLM, TRIM | **Bắt buộc** (nội suy trên đường) |
| CPT(95%) | TRIM | Nên có |
| Hai chỉ số con của DABench (PSAQ, UASQ) và số câu đã chấm | DABench | Bắt buộc, có sẵn trong `bench show` |
| APGR | RouteLLM | Nên có: chính xác cho E3; thô cho router theo bước vì ít điểm |
| Token mô hình mạnh sinh ra | TRIM | Nên có; hữu ích khi dùng gói Plus không có hóa đơn |
| Số lần đổi mô hình mỗi câu | MTRouter | Nên có, có sẵn |
| Số bước mỗi câu, độ trễ trung vị, tỷ lệ token đọc từ cache | EvoRoute (độ trễ) | Nên có, có sẵn trong telemetry |
| AIQ | RouterBench | Bỏ: trùng ý với APGR |
| ΔIBC | TRIM | Bỏ: trùng ý với PGR theo chi phí |
| Độ chính xác gán mức từng bước | AgentRouter | Bỏ: không có nhãn mức cho từng bước |

## 3. Biến cố định

Ghi các giá trị dưới đây vào bảng mục 11 trước lần chạy đầu tiên. Giữ nguyên cho
mọi cấu hình; đổi bất kỳ giá trị nào thì phải chạy lại mọi cấu hình.

| Nhóm | Cố định |
|---|---|
| Agent | Commit Hermes (`dce1e9b3`, 2026-10-09; không chạy `hermes update` trong thời gian thực nghiệm), `agent.max_turns`, compression, memory tắt |
| Mô hình | Tên chính xác (có phiên bản) của mô hình mạnh và rẻ; mô hình mặc định của Hermes = mô hình mạnh; temperature, reasoning effort của từng mức |
| Công cụ | `platform_toolsets.api_server` cố định, **không có web**; Docker sandbox; thư mục dữ liệu chỉ đọc |
| Benchmark | Mã băm suite (in trong `bench list`), `timeout_s`, số request song song (ví dụ 2) |
| Chia dữ liệu | `dabench-manifest.json` (seed 2026). **Không đổi sau khi bắt đầu thu dữ liệu**, như MTRouter |
| Mã | Commit của repo này khi chạy; bộ định tuyến đề xuất có tag git riêng khi khóa |
| Giá | `pricing.yaml` theo giá API công bố tại một ngày cố định, kể cả `cache_read` |

Dùng gói ChatGPT Plus thì không có hóa đơn theo token. Chi phí báo cáo là **chi
phí quy đổi theo giá API** trong `pricing.yaml`, kèm token mô hình mạnh; ghi rõ
điều này trong báo cáo.

## 4. Thứ tự việc

### Bước 0. Chốt câu hỏi và kế hoạch phân tích

Mục 1, 2, 6 của tài liệu này; Thầy duyệt. Kết quả: một trang.

### Bước 1. Dựng môi trường và kiểm tra khói (gói A)

Trên máy Linux, trước khi tốn quota:

- [ ] Gateway chạy với API server; telemetry ghi vào `telemetry.sqlite3`.
- [ ] Mỗi mô hình trả lời được một câu DABench khi đặt làm mặc định.
- [ ] Bật `ASSISTANT_ROUTER_POLICY=always-cheap`, chạy một câu: cột `model` của
  `api_calls` là mô hình rẻ, nghĩa là nhà cung cấp **nhận tên mô hình rẻ trong
  cùng phiên**.
- [ ] Bật `random` với `STRONG_SHARE=0.5`: trong một câu nhiều bước thấy cả
  hai mô hình xen kẽ, không lỗi.
- [ ] Cột `cache_read_tokens` có giá trị khác 0 ở các bước sau. Nếu nhà cung
  cấp không báo, bỏ E5b và phần cache của RQ4, ghi vào giới hạn.
- [ ] Sandbox đọc được `/data/dabench/*.csv`; agent không có công cụ web.
- [ ] `bench show` có số bước, chi phí, `cheap_step_share` cho từng câu.

### Bước 2. Pilot và cổng quyết định

Chạy `dabench-pilot` (9 câu, 3 mỗi mức) với S và C, mỗi cấu hình 2 lần. Đo:

| Đo | Dùng để |
|---|---|
| Độ chính xác mỗi mô hình | **Khoảng trống**: mô hình mạnh hơn rẻ bao nhiêu |
| Chi phí, số bước, thời gian mỗi câu | Ước tính ngân sách cho mục 5 |
| Số câu chạy được mỗi ngày trên một tài khoản Plus | Lên lịch chạy |
| Tỷ lệ lỗi, quá thời gian | Chỉnh `timeout_s`, `max_turns` |
| Tỷ lệ câu thiếu cụm `@ten[...]` của từng mô hình (E9) | Biết mô hình rẻ thua vì phân tích hay vì định dạng |

**Cổng quyết định** (kiểm tra lại trên toàn tập phát triển ở bước 3):

- Mô hình mạnh hơn mô hình rẻ **dưới khoảng 10 điểm phần trăm** → định tuyến
  gần như không có gì để lấy lại. Đổi sang cặp mô hình chênh nhau rõ hơn.
- Mô hình rẻ không rẻ hơn ít nhất khoảng 3 lần mỗi câu → tiết kiệm không đáng
  kể. Đổi cặp.
- Ngân sách ước tính cho mục 5 vượt khả năng → lấy mẫu tập kiểm tra theo mức
  (`--per-level`), **trước** khi chạy.
- Mô hình rẻ thua chủ yếu vì **thiếu định dạng đáp án** → sửa prompt định dạng
  cho mọi cấu hình, hoặc thêm bước viết lại như quy trình gốc của DABench. Nếu
  không, router sẽ học "sửa định dạng" thay vì "khó phân tích".
- Tham khảo khi chọn cặp: Budget-Aware dùng GPT-4.1 mini / GPT-4.1 (giá chênh
  5 lần), khoảng cách thành công 13–23 điểm trên ba bộ.
- Mô hình mạnh gần đúng hết ở mức dễ (GPT-4 đã đạt 78,99% toàn bộ từ đầu 2024) →
  khoảng cách dồn vào mức khó; cân nhắc lấy mẫu nhiều câu khó hơn trong tập
  kiểm tra, quyết định trước khi chạy.

### Bước 3. Thu dữ liệu trên tập phát triển (gói E)

Chạy `dabench-dev` (khoảng 100 câu), mỗi cấu hình một lần:

| Cấu hình | Mức | Vai trò |
|---|---|---|
| S `always-strong` | Bắt buộc | Trần chất lượng; một nửa của oracle và E3 |
| C `always-cheap` | Bắt buộc | Sàn chi phí; nửa còn lại |
| R50 `random`, `STRONG_SHARE=0.5` | Bắt buộc | Mốc; **dữ liệu để học** |
| L `rule` | Bắt buộc | Mốc luật |
| R25, R75 | Nên có | Thêm dữ liệu học, đường ngẫu nhiên đủ 5 điểm |

Vì sao `random` quan trọng: lựa chọn ở mỗi bước **không phụ thuộc trạng thái**
và xác suất chọn đã biết (`STRONG_SHARE`, ghi trong `router_runs`). MTRouter thu
dữ liệu đúng theo cách này (chế độ "roulette" có ghi propensity).

Ngay sau bước này, làm E3 (không tốn lượt gọi): oracle theo câu hỏi và đường
RouteLLM trên tập phát triển.

### Bước 4. Xây và khóa bộ định tuyến đề xuất (gói F)

Chỉ dùng dữ liệu tập phát triển. Cách làm theo MTRouter, thu nhỏ cho 2 mô hình
và đặc trưng tự chọn. Dữ liệu học gồm run ngẫu nhiên **và** run S, C (MTRouter
dùng cả hai; bỏ dữ liệu ngẫu nhiên làm họ giảm 6,6 điểm).

1. **Nhãn**: đúng/sai của cả câu, gắn cho mọi bước của câu đó. Biến thể theo
   EvoRoute: nhân thêm "bước đó không có lỗi công cụ". Cách thứ ba theo
   Budget-Aware: với mỗi câu, trong các quỹ đạo **đúng** của R25, R50, R75, S, C,
   lấy **quỹ đạo rẻ nhất**; nhãn của mỗi bước là lựa chọn ở quỹ đạo đó; câu dễ
   và câu không giải được lấy nhãn "rẻ". Router học bắt chước. So ba cách trong
   E5a.
2. **Mô hình**: với mỗi lựa chọn (rẻ, mạnh), một hồi quy logistic trên đặc
   trưng của bước, ước lượng `P(câu đúng | đặc trưng, lựa chọn)`. Đây là bản
   tương tự router Ridge theo từng mô hình của MTRouter. Điểm rủi ro = hiệu hai
   xác suất; chọn mạnh khi điểm rủi ro > τ.
   **Đặc trưng** (chốt 13/11/2026 sau thảo luận Claude + Codex, chi tiết trong
   [định tuyến](dinh-tuyen.md#2-các-chính-sách)): mô hình chính dùng 7 đặc trưng
   `step`, `prev_tool_errors`, `prev_tool_timeout`, `prev_tool_duration_log`,
   `short_exec_streak`, `ctx_growth`, `prev_choice`, mỗi đặc trưng có tương tác với
   lựa chọn và có regularization; 4 đặc trưng khác (`elapsed_log`, `tool_time_cum`,
   `cum_tool_failures`, `error_streak`) dùng cho ablation E5a. Không đặc trưng nào là
   hằng số của task: loại task, task_id và độ dài prompt ban đầu chỉ dùng để phân
   tích. Sau khi học phải kiểm tra router có đổi quyết định bên trong cùng task.
3. **Kiểm chứng chéo theo file CSV** bên trong tập phát triển để chọn đặc
   trưng (đồng thời là E5a).
4. **Chọn τ**: tính ngoại tuyến tỷ lệ bước mạnh theo τ trên log, chọn 2 giá trị
   cho 2 tỷ lệ mục tiêu (ví dụ 30% và 50%). Chạy thật 2 điểm này trên tập phát
   triển, mỗi điểm một lần.
5. **Khóa**: commit, đặt tag (ví dụ `router-v1`), ghi τ vào bảng mục 11. Sau
   thời điểm này không sửa bộ định tuyến.

Dữ liệu nhỏ (khoảng 100 câu × 1–3 run ngẫu nhiên), các bước trong cùng câu
tương quan. Để so sánh: MTRouter dùng 515.221 lượt cho một mạng nơ-ron;
EvoRoute khởi động bằng khoảng 480 bản ghi bước từ 50 tác vụ ($28,8) cho phương
pháp truy xuất. Quy mô của đồ án gần EvoRoute, nên chỉ hợp với mô hình rất nhỏ. Hồi quy không hơn luật có ngưỡng thì dùng luật có ngưỡng, đúng điểm
cắt trong kế hoạch.

### Bước 5. Đánh giá trên tập kiểm tra (gói G)

Chạy ma trận mục 5, **xen kẽ các cấu hình theo thời gian** để thay đổi phía nhà
cung cấp theo ngày không dồn vào một cấu hình. Ghi ngày giờ từng run. Sau đó
làm E3 trên tập kiểm tra.

### Bước 6. Phân tích, hình và bảng

Mục 6, 7.

### Bước 7. Chuyển giao (E6, nếu kịp)

Bộ định tuyến đã khóa chạy trên mẫu DA-Code và bộ tiếng Việt với S, C và P1, mỗi
cấu hình một lần.

## 5. Ma trận cấu hình và ngân sách

| Mã | Cấu hình | Tập phát triển | Tập kiểm tra |
|---|---|---|---|
| S | `always-strong` | 1 | **2** |
| C | `always-cheap` | 1 | **2** |
| R50 | `random` 0,5 | 1 | 1 |
| R25, R75 | `random` 0,25 / 0,75 | 1 (nên có) | — |
| L | `rule` | 1 | 1 |
| FL | `first-strong`, k = 2 | 1 (nên có) | — |
| P1, P2 | Đề xuất ở 2 điểm τ | 1 mỗi điểm | P1: **2**, P2: 1 |
| E5b | Đề xuất, bỏ quán tính | 1 (nên có) | — |
| O, RouteLLM | Theo câu hỏi | Ngoại tuyến | Ngoại tuyến |

Lặp 2 lần cho S, C, P1 để đo độ dao động giữa các lần chạy; còn lại 1 lần.
MTRouter và Budget-Aware lặp 3 lần; các bài khác một lần. Hai lần là mức tối
thiểu theo ngân sách; ghi rõ trong phần giới hạn. Nhóm độ khó (E7) trên tập kiểm
tra dựa trên 2 lần chạy S và C, ít hơn 5 lần của Budget-Aware. Khoảng tin cậy lấy bằng bootstrap
trên câu hỏi (mục 6).

**Số câu phải chạy thật**, với tập phát triển khoảng 100 câu và tập kiểm tra
khoảng 155 câu:

| Phần | Bắt buộc | Thêm nếu làm "nên có" |
|---|---|---|
| Pilot | 9 × 4 = 36 | — |
| Tập phát triển | 100 × 6 = 600 | + 400 (R25, R75, FL, E5b) |
| Tập kiểm tra | 155 × 9 ≈ 1.400 | — |
| Tổng | **khoảng 2.050** | khoảng 2.450 |

Nhân với chi phí và thời gian mỗi câu đo ở pilot. Nếu vượt ngân sách, giảm theo
thứ tự: bỏ phần "nên có" → P2 trên tập kiểm tra → lấy mẫu tập kiểm tra theo mức.
Quyết định trước khi chạy và ghi lại.

## 6. Thống kê

- **Đơn vị là câu hỏi.** Lấy trung bình các lần lặp của từng câu trước, rồi mới
  tính trên cả tập.
- **Khoảng tin cậy 95% bằng bootstrap ghép cặp theo câu**, khoảng 10.000 lần lấy
  mẫu lại: lấy lại cùng một bộ câu cho cả hai cấu hình khi so chênh lệch độ chính
  xác và chênh lệch chi phí.
- **Kiểm định ghép cặp**: McNemar cho đúng/sai của hai cấu hình trên cùng câu,
  hoặc kiểm định hoán vị ghép cặp trên trung bình các lần lặp.
- **Chênh lệch chấp nhận được** cho "không kém hơn" (ví dụ 5 điểm phần trăm)
  chốt ở bước 0. Với khoảng 150 câu, khoảng tin cậy của một tỷ lệ là khoảng ±8
  điểm, của chênh lệch ghép cặp hẹp hơn. Báo cáo khoảng tin cậy, không chỉ giá
  trị p.
- **Độ dao động giữa các lần chạy**: chênh lệch độ chính xác giữa hai lần của
  S, C, P1. Chênh lệch giữa các cấu hình nhỏ hơn mức này thì không kết luận.
- Báo cáo **theo mức L1/L2/L3** là phân tích phụ, đã nêu trước.

## 7. Hình và bảng

| # | Hình | Dạng | Dữ liệu | Mức |
|---|---|---|---|---|
| **H1** | **Mặt phẳng chi phí ↔ chính xác** | x: chi phí mỗi câu, y: độ chính xác; điểm S, C, L, P1, P2; đường ngẫu nhiên nối C–R50–S; đường RouteLLM và oracle theo câu hỏi; thanh sai số 95% | Tập kiểm tra | **Bắt buộc** (hình chính của mọi bài) |
| **H2** | **Độ chính xác theo % gọi mạnh**, kiểu RouteLLM | x: % lần gọi mạnh, y: độ chính xác; đường ngang S và C; đường RouteLLM, oracle, ngẫu nhiên; điểm L và P; đánh dấu CPT(50%), CPT(80%) | Tập phát triển và kiểm tra | **Bắt buộc** |
| **B1** | **Bảng kết quả chính** | Mỗi cấu hình một hàng: độ chính xác, chi phí, % giảm chi phí, % gọi mạnh, PGR; kèm khoảng tin cậy | Tập kiểm tra | **Bắt buộc** |
| **H3** | Dòng thời gian định tuyến | Mỗi hàng một câu, mỗi ô một bước, màu = rẻ/mạnh, dấu = công cụ lỗi; 4–6 câu tiêu biểu | `session_steps` | **Bắt buộc** (giải thích được; không tốn chi phí) |
| H4 | Độ chính xác và chi phí theo mức L1/L2/L3 | Hai ô cạnh nhau, cột nhóm | Tập kiểm tra | Nên có |
| H5 | Tỷ lệ bước rẻ theo vị trí bước | x: bước 1, 2, 3…, y: tỷ lệ chọn rẻ; một đường mỗi chính sách | `route_choice` | Nên có |
| H6 | Số bước mỗi câu | Hộp theo cấu hình | telemetry | Nên có: cho thấy mô hình rẻ có làm câu dài hơn |
| H7 | Đổi mô hình và cache | x: số lần đổi mỗi câu, y: tỷ lệ token đọc từ cache | telemetry, E5b | Nên có, nếu có token cache |
| H8 | Hệ số đặc trưng của router | Cột ngang | Mô hình đã khóa | Nên có (có ngay từ hồi quy logistic) |
| **H9** | **Sau lỗi công cụ** | Hai ô: tỷ lệ đổi mô hình sau lỗi theo chính sách; tỷ lệ bước sau hồi phục theo lựa chọn sau lỗi, kèm khoảng tin cậy | R50, L, P; telemetry | **Bắt buộc** (RQ5; theo MTRouter Hình 4) |
| **B4** | **Bảng bổ trợ S/C** | 2×2 đúng/sai và số câu dễ / khó / không giải được, tổng và theo mức | Run S, C | **Bắt buộc** (E7) |
| **H10** | **Chi phí theo nhóm độ khó** | Cột chồng 100%: phần chi phí vào câu dễ / khó / không giải được; cột đầu là tỷ lệ số câu mỗi nhóm; một cột mỗi chính sách ở mức chi phí gần nhau | Run S, C (để phân nhóm) + run của từng chính sách | **Bắt buộc** (E7b; theo Budget-Aware Hình 3) |
| B2 | Ablation | E5a (AUC), E5b | Tập phát triển | Nên có |
| B3 | So sánh thiết kế với các bài liên quan | Từ [gói C](nghien-cuu-dinh-tuyen.md#3-so-sánh-với-thiết-kế-của-đồ-án); không so số vì khác benchmark | — | **Bắt buộc** (phần tổng quan) |

## 8. Công cụ còn thiếu

| # | Việc | Vì sao | Khi nào |
|---|---|---|---|
| 1 | **Xuất dữ liệu** ra CSV: một dòng mỗi câu × lần lặp (cấu hình, mức, đúng/sai, đáp án con, chi phí, token mô hình mạnh, số bước, bước rẻ, đổi mô hình, cache, độ trễ) và một dòng mỗi bước (đặc trưng, lựa chọn, mô hình, token) | Đầu vào cho mọi phân tích, E3 và gói F | **Xong** 13/11: `bench export RUN... --out DIR` |
| 2 | **Script chạy ma trận cấu hình**: đặt biến, khởi động lại gateway, chờ sẵn sàng, chạy suite, ghi mã run vào file danh sách lần chạy | Lặp lại được, tránh nhầm nhãn | Trước bước 3 |
| 3 | **Chạy tiếp run bị gián đoạn** khi hết lượt của gói Plus (gói B2) | Một run 155 câu khó chạy liền | Trước bước 3 |
| 4 | **Đánh giá ngoại tuyến theo câu hỏi** (E3): chạy router RouteLLM trên prompt, tra kết quả S/C, quét ngưỡng, oracle | RQ3; "chạy lại bài báo" | Sau bước 3 |
| 5 | Router đề xuất: huấn luyện trên CSV, chính sách có τ và quán tính | Gói F | Bước 4 |
| 5b | Chính sách `first-strong` (First-Large): mạnh k bước đầu, không nâng lên khi lỗi | Mốc E2b; khoảng 10 dòng qua `register_policy` | **Xong** 13/11 |
| 6 | **Script phân tích**: bootstrap ghép cặp, McNemar, PGR, CPT, các hình mục 7 | Gói G | Bước 6 |

## 9. Mối đe dọa đến tính hợp lệ

| Rủi ro | Cách xử lý |
|---|---|
| Đề và đáp án DABench công khai | Bỏ công cụ web; ghi rằng mô hình có thể đã thấy dữ liệu khi huấn luyện, ảnh hưởng như nhau lên mọi cấu hình |
| Chỉnh trên tập kiểm tra | Tập kiểm tra chạy một lần sau khi khóa; mọi lần chạy thử ghi lại |
| Router RouteLLM huấn luyện trên dữ liệu khác (Chatbot Arena) | Dùng nguyên trạng làm mốc "theo câu hỏi có sẵn", không chỉnh; nói rõ trong báo cáo |
| Nhà cung cấp thay đổi mô hình hoặc tải theo thời gian | Ghi tên mô hình có phiên bản, chạy xen kẽ, ghi ngày |
| Mô hình không tất định | Lặp 2 lần cho S, C, P1; tính theo câu; báo khoảng tin cậy |
| Cách chấm khác bài gốc | Đã liệt kê trong [benchmark](benchmark.md#cách-chấm-và-điểm-khác-với-bài-báo); báo cáo cả chỉ số gốc |
| Mô hình rẻ thua vì định dạng đáp án, không vì phân tích | E9 ở pilot; báo cáo riêng tỷ lệ thiếu định dạng theo cấu hình |
| Chi phí quy đổi, không phải hóa đơn thật | Ghi ngày của bảng giá; báo cáo cả token thô và token mô hình mạnh |
| Gateway khởi động lại giữa lượt làm bước sau bị coi là bước đầu | Không khởi động lại khi đang chạy run; lọc các câu bị ảnh hưởng |

## 10. Gắn vào lịch tuần

| Tuần | Bước | Kết quả |
|---|---|---|
| 1 (11–17/11) | 0, 1, 2 | Một trang RQ gửi Thầy; pilot S/C; quyết định cặp mô hình và ngân sách |
| 2 (18–24/11) | Công cụ 1–3; bắt đầu 3 | Xuất CSV, script ma trận, chạy tiếp run |
| 3 (25/11–01/12) | 3; công cụ 4 | Dữ liệu tập phát triển; E3 trên tập phát triển |
| 4 (02–08/12) | 4 | Router đề xuất, τ, E5a; bảng số liệu đầu tiên |
| 5 (09–15/12) | 4 (khóa), bắt đầu 5 | Tag `router-v1`; bắt đầu tập kiểm tra |
| 6 (16–22/12) | 5, 6 | H1, H2, B1, H3 |
| 7 (23–31/12) | 7 nếu kịp; báo cáo | Phần "nên có" còn thời gian; báo cáo cuối |

## 11. Sổ thực nghiệm

Điền khi chạy. Mỗi dòng là một run benchmark.

| Ngày | Bước | Suite (mã băm) | Cấu hình | Tham số | Nhãn | Mã run | Commit repo | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| 2026-11-12 | 1 | dabench-smoke@822a1354 | S | — | smoke-strong | 20261112-085253-a7f4f5 | 89989bf+ | Sau khi bật `execute_code`; run 085013 trước đó bị chặn `execute_code`, bỏ |
| 2026-11-12 | 1 | dabench-smoke@822a1354 | C | — | smoke-cheap | 20261112-085401-dfdfb1 | 89989bf+ | |
| 2026-11-12 | 2 | dabench-pilot | S | repeats 2 | pilot-S | 20261112-085514-2adbfb | 89989bf+ | 14/18; 77,8% theo câu; 2,8 bước/câu; $0,015/câu |
| 2026-11-12 | 2 | dabench-pilot | C | repeats 2 | pilot-C | 20261112-090000-a2dd4f | 89989bf+ | 15/18; 83,3% theo câu; 3,1 bước/câu; $0,0010/câu |
| 2026-11-12 | 2 | dabench-pilot | C' (`deepseek-flash`, provider `deepseek`, giờ cao điểm) | repeats 2, router tắt | pilot-DSF | 20261112-091702-380fb9 | 89989bf+ | 15/18; 83,3% theo câu; 4,8 bước/câu (tối đa 16); $0,0019/câu |
| 2026-11-12 | 1 | dabench-smoke@822a1354 | R50 Sol ↔ DeepSeek (`ASSISTANT_ROUTER_CHEAP_PROVIDER=deepseek`) | seed 11 | smoke-sol-ds | 20261112-093503-a81ef1 | 89989bf+ | Kiểm tra đổi nhà cung cấp theo bước: 7 bước, xen kẽ, không lỗi |
| 2026-11-12 | 2 | dacode-pilot@d590d2c45ac7 | S | sandbox `:2`, timeout 1800 s | dacode-S1 | 20261112-101706-4d15b8 | 89989bf+ | Điểm DA-Code 0,755; 5,9 bước/câu; $0,039/câu |
| 2026-11-12 | 2 | dacode-pilot@d590d2c45ac7 | S | như trên | dacode-S2 | 20261112-103717-aa9366 | 89989bf+ | Dừng sau 8 câu (runner bị dừng tay); 8 câu giống hệt S1 |
| 2026-11-12 | 2 | dacode-pilot-ml2@95fe06322716 | S | như trên | dacode-S2b | 20261112-133103-a53f89 | 89989bf+ | Bù 2 câu S2 thiếu (ml-competition-008, ml-regression-002): 0,99 / 1,0; 14 bước/câu. S2 + S2b = 0,755 |
| 2026-11-12 | 2 | dacode-pilot@d590d2c45ac7 | C (DeepSeek V4 Flash, `deepseek-flash`) | như trên; 11:36–12:30 UTC (thứ 5), giờ thấp điểm | dacode-C1 | 20261112-113633-747b7a | 89989bf+ | 0,556; 13,7 bước/câu; 2 câu ML chạm `max_turns` 25 không ghi file kết quả |
| 2026-11-12 | 2 | dacode-pilot@d590d2c45ac7 | C (DeepSeek V4 Flash, `deepseek-flash`) | như trên; 12:32–13:29 UTC (thứ 5), giờ thấp điểm | dacode-C2 | 20261112-123231-c41e00 | 89989bf+ | 0,623; 10,3 bước/câu; ml-competition-008 chạm `max_turns`, ml-regression-002 quá 1800 s |
| 2026-11-13 | 2 | dacode-pilot-ml2@95fe06322716 | R50 `random` | share 0,5, seed 21; 14:48–15:08 UTC, thấp điểm | ml2-R50-a | 20261113-144849-7c104e | b30e1b2 | 0,994 / 1,00; 9 / 13 bước; 44% / 54% bước Sol; $0,060/câu |
| 2026-11-13 | 2 | dacode-pilot-ml2@95fe06322716 | FL `first-strong` | `STRONG_FIRST_STEPS=2` | ml2-FL-a | 20261113-150858-818b87 | b30e1b2 | 0,993 / 1,00; 14 / 25 bước (reg-002 chạm `max_turns` nhưng file đã ghi); $0,023/câu |
| 2026-11-13 | 2 | dacode-pilot-ml2-long@b0a76c9211c6 | C `always-cheap` | `max_turns` 50, timeout 3600 s (wrapper nâng `MAX_TIMEOUT_S`) | ml2-C50 | 20261113-154131-4d5802 | b30e1b2 | 0,887 / 1,00; 35 / 50 bước; reg-002 3286 s; $0,026/câu |
| 2026-11-13 | 2 | dacode-pilot-ml2@95fe06322716 | R50 `random` | share 0,5, seed 22 | ml2-R50-b | 20261113-164539-ad013a | b30e1b2 | 0,986 / 1,00; 13 / 11 bước; 31% / 73% bước Sol; $0,078/câu |
| 2026-11-13 | 2 | dacode-pilot-ml2@95fe06322716 | FL `first-strong` | `STRONG_FIRST_STEPS=2` | ml2-FL-b | 20261113-165613-d32c12 | b30e1b2 | 0,995 / 0; cả hai 25 bước; reg-002 chạm `max_turns` chưa ghi file; $0,026/câu |
| 2026-11-14 | 2 | dacode-pilot@d590d2c45ac7 | S | env-v1 | env1-S1 | 20261114-035004-800088 | 37c2202 | Bỏ: dừng tay sau 1 câu để quyết định chạy song song (gateway khởi động lại) |
| 2026-11-14 | 2 | dacode-pilot@d590d2c45ac7 | S | env-v1; 03:56–04:33 UTC (thứ 7) | env1-S1b | 20261114-035622-ac3af2 | 37c2202 | 0,855; 7,0 bước/câu; $0,062/câu; 2 lần gateway GPT treo khoảng 600 s rồi thử lại (`APIConnectionError`) |
| 2026-11-14 | 2 | dacode-pilot@d590d2c45ac7 | S | env-v1; 04:33–04:53 UTC | env1-S2 | 20261114-043316-280aeb | 37c2202 | 0,856; 6,8 bước/câu; $0,088/câu |
| 2026-11-14 | 2 | dacode-pilot@d590d2c45ac7 | C | env-v1; 04:53–05:11 UTC, thứ 7 nên không có giờ cao điểm | env1-C1 | 20261114-045328-1555af | 37c2202 | 0,556; 13,5 bước/câu; $0,0064/câu |
| 2026-11-14 | 2 | dacode-pilot@d590d2c45ac7 | C | env-v1; 05:11–06:00 UTC | env1-C2 | 20261114-051122-f22b29 | 37c2202 | 0,656; 12,8 bước/câu; $0,0070/câu; 1 cell bị script cắt ở 600 s (đúng thiết kế); reg-002 không có câu trả lời cuối nhưng file đầu ra được chấm 1,0 |
| 2026-11-14 | — | dacode-conc (7 câu ngắn của pilot) | C | `--concurrency 3`, profile tách riêng `bench2` (cổng 8651, `container_cpu: 4`) | conc3-test | 20261114-041459-8de342 | 37c2202 | Gỡ lỗi, không dùng để học hay báo cáo. 3,1 phút so với 5,5–6,0 phút tuần tự (khoảng 1,8 lần); điểm 0,651; tổng độ trễ theo câu tăng khoảng 60% |

**Cổng quyết định, pilot DABench (2026-11-12): không đạt.** C ≥ S (83,3% so với
77,8%); khác biệt là lỗi so khớp của bộ chấm (0,89/0,88, NaN/nan); quỹ đạo khoảng 3
bước. DABench quá dễ và quá ngắn cho định tuyến theo bước.

**Cổng quyết định, pilot DA-Code (2026-11-12): đạt tạm thời.** Trung bình hai lần: S
0,755 / 0,755, C 0,556 / 0,623 → khoảng trống khoảng 16,5 điểm, nhưng 95% CI bootstrap
theo câu là [−8; +45] điểm (xác suất khoảng trống dưới 10 điểm khoảng 35%). Toàn bộ
khoảng trống nằm ở 2 câu ML dài (S 0,99–1,0 cả hai lần; C 0 cả hai lần, chạm `max_turns`
hoặc quá giờ); 8 câu còn lại C ngang hoặc hơn S. Chi phí S $0,041/câu, C $0,0059/câu
(khoảng 7 lần). Cả 4 run C đều chạy vào **giờ thấp điểm** của DeepSeek (cao điểm là
T2–T6 01–04 và 06–10 UTC, theo `agent/usage_pricing.py` của Hermes); bản ghi trước ghi
nhầm là giờ giá thường và tỷ lệ 3,5 lần. C tốn token gấp khoảng 4 lần S (167–186k so với
44k mỗi câu). Quỹ đạo nhiều bước (S 5,9, C 10–14 bước/câu). Giới hạn: 10 câu, khoảng
trống chỉ dựa vào 2 câu và có thể phụ thuộc ngân sách `max_turns`/timeout; C2 thiếu
token của câu quá giờ nên chi phí C2 hơi thấp. Đã nối lại câu đó qua session id suy ra (`bench export`, 13/11): C1 $0,0063, C2 $0,0068 mỗi câu, S1 $0,0387, tức khoảng 6 lần. Kiểm tra tiếp bằng thử nghiệm nhỏ trên 2
câu ML (R50, FL, C với `max_turns` 50).

**Thử nghiệm nhỏ trên 2 câu ML (2026-11-13, 5 run, đều giờ thấp điểm).** Trên 2 câu này
S được 0,99–1,0 với $0,07–0,10/câu; C với ngân sách gốc (25 bước, 1800 s) được 0 cả 4
lần vì hết ngân sách trước khi ghi file kết quả, không phải vì đáp án sai. (1) Với
`max_turns` 50 và 3600 s, C giải được cả hai (0,887 và 1,0) nhưng cần 35 và 50 bước, tới
3286 s, $0,026/câu. Vậy khoảng trống S − C của pilot là hàm của ngân sách `max_turns` /
`timeout_s`; đổi ngân sách là đổi kết luận cổng. (2) R50 cứu 4/4 lần (TB 0,995), quỹ đạo
về 9–13 bước như S, nhưng chi phí $0,06–0,08/câu, gần bằng S trên chính 2 câu này. (3) FL
(2 bước Sol đầu) cứu 3/4 với $0,023–0,026/câu, nhưng 2 bước Sol đầu chỉ là dò dữ liệu,
phần còn lại giống C và 2/3 lần thành công sát giới hạn 25 bước: FL đo chủ yếu phương sai
của C. Hệ quả: giữ ngân sách 25 bước / 1800 s là biến cố định (nó tạo ra áp lực mà router
phải giải), báo cáo kèm độ nhạy theo ngân sách; đặc trưng `step` và `short_exec_streak`
có cơ sở (C có 8–22 lệnh ngắn mỗi câu, S có 1–4). Nối dữ liệu: 18/18 kết quả nối được
sang bước bằng `bench export` (câu C2 quá giờ lấy lại qua session id suy ra). Giới hạn: 2
câu cùng loại, được chọn vì C thua ở pilot (hồi quy về trung bình), 1–2 lần mỗi cấu hình.

**Môi trường `env-v1` (chốt 2026-11-14).** Mọi run từ `env1-S1` trở đi dùng bảng giá trị cố
định bên dưới. Ba thay đổi so với trước: Sol gọi qua gateway API trả tiền thay cho ChatGPT
Plus, sandbox 12 CPU thay cho 1, và hạn tool 660 s thay cho 420 s. Đổi bất kỳ giá trị nào
thì phải chạy lại mọi cấu hình. Các run trước 14/11 (pilot DABench và DA-Code ngày 12/11,
thử nghiệm 2 câu ML ngày 13/11) **chỉ dùng để gỡ lỗi, không dùng để học hay báo cáo chung
với run env-v1**. Gateway GPT tự chèn khoảng 4,2k token hướng dẫn ẩn vào mọi lệnh gọi
(phần lớn được cache). Nội dung hướng dẫn này không biết, nên là biến gây nhiễu không kiểm
soát được và phải ghi vào phần giới hạn. Chi phí báo cáo vẫn tính theo giá API công bố
trong `pricing.yaml`, không phải giá gateway thu thật.

**Cổng quyết định, pilot DA-Code env-v1 (2026-11-14): đạt.** S được 0,855 / 0,856, C được
0,556 / 0,656. Trung bình S 0,855 và C 0,606, chênh khoảng **25 điểm**, vượt ngưỡng 10.
Chi phí S/C khoảng **11 lần** ($0,075 so với $0,0067 mỗi câu), vượt ngưỡng 3. Khoảng chênh
chỉ nằm ở 3 câu: `ml-competition-008` (S 2/2, C 0/2), `ml-regression-002` (S 2/2, C 1/2)
và `di-text-004` (S 2/2, C 0/2). 7 câu còn lại cho S và C điểm như nhau: `data-sa-031` luôn
0, `ml-cluster-002` luôn 0,56, các câu khác luôn 1. So với pilot 12/11, S tăng từ 0,755 lên
0,855, toàn bộ nhờ `di-text-004`, còn C gần như giữ nguyên. Không còn lần nào Hermes cắt
tool ở 420 s. C dùng gấp khoảng 2,2 lần token đầu vào (khoảng 184k so với 82k mỗi câu) và
khoảng 2 lần số bước (13,2 so với 6,9 bước mỗi câu). Nối dữ liệu: 40/40 kết quả có session
id trực tiếp. Giới hạn: vẫn chỉ 10 câu, khoảng chênh dựa vào 3 câu. Bước 3 cần một tập phát
triển DA-Code lớn hơn: trên máy hiện chỉ có 52 câu mẫu, bản đầy đủ có 500 câu.

**Tập phát triển và tập kiểm tra DA-Code (chốt 2026-11-14).** Dùng bản đầy đủ 500 câu (file
`source.zip` và `gold.zip` trên Google Drive của tác giả, ghi trong README của DA-Code). Bỏ
nhóm `dw` và các câu vẽ biểu đồ như trước, còn 322 câu dùng được. Chọn 255 câu, chia làm 100
câu phát triển và 155 câu kiểm tra, phân tầng theo nhóm × độ khó với seed 2026
(`python -m assistant_core.dacode split --dev 100 --test 155 --seed 2026`; danh sách câu ở
[`config/dacode/split-2026.json`](../config/dacode/split-2026.json)). 10 câu pilot bắt buộc nằm
trong tập phát triển, nên tập kiểm tra không có câu nào đã dùng để quyết định cổng. Suite
`dacode-dev` có 100 câu (sa 23, di 24, dm 22, ml 31), `dacode-test` có 155 câu (sa 33, di 38,
dm 36, ml 48). Chỉ giải nén 255 câu này vào checkout có sẵn, không ghi đè file nào; source
của các câu mẫu trùng khớp từng byte với bản đầy đủ. Thư mục làm việc khi chạy cần khoảng
2,7 GB cho `dacode-dev` và khoảng 4,1 GB cho `dacode-test`, nên phải xóa thư mục của tập
phát triển trước khi chạy tập kiểm tra.

**Hai bản gold khác nhau.** Gold của repo GitHub (bản đang dùng) và gold trên Drive (ngày
2024-10-06) khác nhau ở 6 câu: `data-sa-001`, `data-sa-031`, `di-text-004`, `dm-csv-001`,
`dm-csv-010`, `dm-csv-015`. Ví dụ, `data-sa-031` đổi dấu khoảng tin cậy; `dm-csv-015` đổi tên
cầu thủ; `di-text-004` đổi nhãn (đề bài dẫn tới `tips.md`, nhưng file này không có trong cả
hai bản). Cả 6 câu đều nằm trong tập phát triển hoặc tập kiểm tra. Quy ước: dùng gold GitHub
cho mọi câu có trong repo (bản mới hơn), gold Drive cho các câu còn lại. Chấm lại 4 run env1
bằng gold Drive (không tốn lượt gọi model): S 0,855 / 0,856 như cũ, C1 tăng từ 0,556 lên
0,656 (`data-sa-031` đạt 1), C2 giữ 0,656, nên S − C là khoảng 20 thay vì 25 điểm. Cổng vẫn
đạt. Báo cáo kết quả của 6 câu này kèm độ nhạy theo bản gold.

**Chạy song song làm hỏng thời gian train (đo 2026-11-14): giữ song song 1.** Các task chạy
song song trên cùng một profile dùng chung một container sandbox (12 CPU, 5 GB RAM). Đã đo
bằng một script giống bước train điển hình của `ml-competition-008` (760k dòng huấn luyện, [`train_contention.py`](../deploy/bench-sandbox/bench/train_contention.py); XGBoost
`hist` 300 cây; RandomForest 100 cây trên 200k dòng; mọi lõi), chạy trong container có cùng
giới hạn:

| Cách chạy | XGBoost | RandomForest | Tổng mỗi bản |
|---|---|---|---|
| 1 bản | 3,1–3,3 s | 8,2–8,3 s | 12 s |
| 2 bản cùng lúc | 104–108 s | 23–25 s | 130–132 s |
| 3 bản cùng lúc | 199–210 s | 32–40 s | 239–243 s |
| 1 bản, `OMP_NUM_THREADS=4` | 7,0 s | 8,9 s | 16 s |
| 3 bản, `OMP_NUM_THREADS=4` | 10,7–11,5 s | 41–42 s | 52–54 s |

Mỗi tiến trình thấy 16 CPU (`os.cpu_count()`) nên mở 16 luồng, trong khi quota chỉ có 12;
XGBoost bị chậm 30–60 lần vì quá nhiều luồng tranh nhau (RAM không phải vấn đề). Với song
song 2–3, một lệnh train vài giây thành vài phút, dễ chạm hạn 600 s, làm đổi kết quả của C
và đặc trưng `prev_tool_duration_log`. Giới hạn luồng chỉ giảm bớt, vẫn chậm 3–5 lần, và
đổi môi trường cho mọi task. Lần thử `--concurrency 3` trên 7 câu ngắn trước đó không thấy
vấn đề này vì các câu đó gần như không train. Kết luận: giữ song song 1 (env-v1). Đợt chạy
dài dùng `scripts/bench_queue.py`, dừng được và chạy tiếp được. Nếu cần nhanh hơn, cách
duy nhất không gây tranh chấp là mỗi cấu hình một profile, một gateway và một container
riêng chia CPU (ví dụ 3 × 4 CPU). Nhưng đó là môi trường mới (env-v2) với ít CPU hơn cho mỗi
task, nên phải chạy mọi cấu hình trong cùng điều kiện đó.

Giá trị cố định (mục 3), áp dụng từ env-v1. Giá trị trước 14/11 ghi trong ngoặc:

| Mục | Giá trị |
|---|---|
| Mô hình mạnh | `gpt-6.1-sol`, provider custom `xingchi` (gateway bên thứ ba tương thích OpenAI, key API trả tiền), chat completions, `reasoning_effort: high` gửi qua `providers.xingchi.extra_body`; telemetry ghi provider `custom` (trước: provider `openai-codex`, gói ChatGPT Plus, Responses API) |
| Mô hình rẻ | DeepSeek V4 Flash, ID API `deepseek-flash` (provider `deepseek`, đổi qua Hermes theo bước: [dinh-tuyen](dinh-tuyen.md)), `reasoning_effort: high`. Pilot DABench đầu tiên dùng `gpt-6-luna` |
| Hermes commit | `dce1e9b3` (2026-10-09); không chạy `hermes update` |
| Repo, config | `37c2202` (nhánh `exp/dacode-pilot`), plugin chép từ `src/` khi start gateway; config hash `960074a95e9ab73b` (`bench_runs.config_hash`; trước: `170133675ecc0ba0`) |
| `max_turns`, `timeout_s`, song song | 25; 900 s (DABench), 1800 s (DA-Code); 1 (`BENCH_CONCURRENCY` trong `run1.sh`, mặc định 1) |
| Hạn mỗi lệnh tool | `code_execution.timeout` 600 s (script tự dừng); `timeouts.tools.sequential_call` = `concurrent_batch` = 660 s (trước: 420 s mặc định của Hermes, `_DEFAULT_CONCURRENT_TOOL_TIMEOUT_S` trong `agent/tool_executor.py`, cắt trước 600 s) |
| Bộ công cụ `api_server` | `code_execution`, `file`, `todo` (không có web); `approvals.unattended_mode: approve` |
| Sandbox | `hermes-bench-sandbox:2` (pilot DABench dùng `:1`; `:2` thêm thư viện DA-Code) ([Dockerfile](../deploy/bench-sandbox/Dockerfile), thư viện ghim), **`container_cpu: 12`** (trước: 1), RAM 5 GB, `docker_network: false`, `/data/dabench` chỉ đọc, `/dacode` ghi được |
| Profile Hermes | `.runtime/hermes-home/profiles/bench`, gateway riêng cổng 8650 ([benchmark](benchmark.md#10-profile-thực-nghiệm)) |
| Ngày bảng giá | 2026-11-12: sol 2.00 / 10.00 / cache 0.10, luna 0.10 / 0.50 / cache 0.01, deepseek-flash 0.15 / 0.60 / cache 0.003 (giờ thấp điểm; giờ cao điểm T2–T6 01–04 và 06–10 UTC gấp đôi) USD mỗi 1M token. Chi phí DeepSeek phải tính theo giờ của từng lệnh gọi |
| Tag bộ định tuyến đã khóa, τ | |
