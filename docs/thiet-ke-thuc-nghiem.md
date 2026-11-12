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
| RQ5 (nếu kịp) | Bộ định tuyến đã khóa có giữ được trên bộ khác không? | DA-Code mẫu nhỏ, bộ tiếng Việt |

Viết RQ, chỉ số chính và mức chênh chấp nhận được (mục 6) thành một trang, gửi
Thầy duyệt **trước** khi chạy tập kiểm tra.

## 2. Chọn từ các bài liên quan

Chọn theo hai tiêu chí: **bài nào cũng có** (hội đồng sẽ hỏi nếu thiếu), và
**chi phí** (số lượt gọi mô hình). Ba mức: **bắt buộc**, **nên có** nếu còn
thời gian và quota, **bỏ** kèm lý do để ghi vào phần giới hạn.

### Thực nghiệm

| # | Thực nghiệm | Theo bài | Chi phí | Mức |
|---|---|---|---|---|
| E1 | Mô hình đơn: luôn mạnh (S), luôn rẻ (C) trên tập phát triển và kiểm tra | Mọi bài | Chạy thật | **Bắt buộc** |
| E2 | Router ngẫu nhiên theo bước (R) và router luật (L) | RouteLLM, MTRouter (router luật của MTRouter gần như trùng `rule`) | Chạy thật | **Bắt buộc** |
| E3 | **Theo câu hỏi, ngoại tuyến:** oracle và router của RouteLLM (`bert` hoặc `mf`) chấm prompt DABench, tra đúng/sai từ lần chạy S và C | RouteLLM (đánh giá ngoại tuyến), RouterBench | **Gần như 0**: không gọi mô hình agent | **Bắt buộc**; đây cũng là phần "chạy lại bài báo" Thầy yêu cầu |
| E4 | Router đề xuất: chọn τ trên tập phát triển, 2 điểm τ trên tập kiểm tra | TRIM-Thr (ngưỡng), MTRouter (học từ log) | Chạy thật | **Bắt buộc** |
| E5a | Ablation **dự đoán**: bỏ từng nhóm đặc trưng, đo AUC của mô hình rủi ro bằng kiểm chứng chéo trên log tập phát triển | EvoRoute (bỏ từng thành phần) | 0 | Nên có |
| E5b | Ablation **chạy thật**: bỏ quán tính, một lần trên tập phát triển | EvoRoute | 1 run | Nên có, chỉ khi nhà cung cấp báo token cache |
| E6 | Chuyển giao sang DA-Code mẫu nhỏ và bộ tiếng Việt | MTRouter (ngoài phân phối), TRIM (chuyển bộ) | Chạy thật | Nên có; bỏ đầu tiên nếu trễ |
| — | RouteLLM / FrugalGPT áp theo từng bước | AgentRouter | Cần router đọc nội dung trong vòng lặp, hoặc cascade 2 lần gọi một bước | **Bỏ**: Hermes không cho cascade; RQ3 đã có E3 |
| — | Router-R1, tái hiện đầy đủ EvoRoute | — | Huấn luyện RL / nhiều mô hình | **Bỏ**: ngoài phạm vi và ngân sách |
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
| CPT(50%), CPT(80%) | RouteLLM | **Bắt buộc** (nội suy trên đường) |
| Hai chỉ số con của DABench (PSAQ, UASQ) và số câu đã chấm | DABench | Bắt buộc, có sẵn trong `bench show` |
| APGR | RouteLLM | Nên có: chính xác cho E3; thô cho router theo bước vì ít điểm |
| Token mô hình mạnh sinh ra | TRIM | Nên có; hữu ích khi dùng gói Plus không có hóa đơn |
| Số lần đổi mô hình mỗi câu | MTRouter | Nên có, có sẵn |
| Số bước mỗi câu, độ trễ trung vị, tỷ lệ token đọc từ cache | EvoRoute (độ trễ) | Nên có, có sẵn trong telemetry |
| AIQ | RouterBench | Bỏ: trùng ý với APGR |
| Độ chính xác gán mức từng bước | AgentRouter | Bỏ: không có nhãn mức cho từng bước |

## 3. Biến cố định

Ghi các giá trị dưới đây vào bảng mục 11 trước lần chạy đầu tiên. Giữ nguyên cho
mọi cấu hình; đổi bất kỳ giá trị nào thì phải chạy lại mọi cấu hình.

| Nhóm | Cố định |
|---|---|
| Agent | Commit Hermes (`93c9360a`), `agent.max_turns`, compression, memory tắt |
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

**Cổng quyết định** (kiểm tra lại trên toàn tập phát triển ở bước 3):

- Mô hình mạnh hơn mô hình rẻ **dưới khoảng 10 điểm phần trăm** → định tuyến
  gần như không có gì để lấy lại. Đổi sang cặp mô hình chênh nhau rõ hơn.
- Mô hình rẻ không rẻ hơn ít nhất khoảng 3 lần mỗi câu → tiết kiệm không đáng
  kể. Đổi cặp.
- Ngân sách ước tính cho mục 5 vượt khả năng → lấy mẫu tập kiểm tra theo mức
  (`--per-level`), **trước** khi chạy.

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
và đặc trưng tự chọn:

1. **Nhãn**: đúng/sai của cả câu, gắn cho mọi bước của câu đó.
2. **Mô hình**: với mỗi lựa chọn (rẻ, mạnh), một hồi quy logistic trên đặc
   trưng của bước, ước lượng `P(câu đúng | đặc trưng, lựa chọn)`. Đây là bản
   tương tự router Ridge theo từng mô hình của MTRouter. Điểm rủi ro = hiệu hai
   xác suất; chọn mạnh khi điểm rủi ro > τ.
3. **Kiểm chứng chéo theo file CSV** bên trong tập phát triển để chọn đặc
   trưng (đồng thời là E5a).
4. **Chọn τ**: tính ngoại tuyến tỷ lệ bước mạnh theo τ trên log, chọn 2 giá trị
   cho 2 tỷ lệ mục tiêu (ví dụ 30% và 50%). Chạy thật 2 điểm này trên tập phát
   triển, mỗi điểm một lần.
5. **Khóa**: commit, đặt tag (ví dụ `router-v1`), ghi τ vào bảng mục 11. Sau
   thời điểm này không sửa bộ định tuyến.

Dữ liệu nhỏ (khoảng 100 câu × 1–3 run ngẫu nhiên), các bước trong cùng câu
tương quan. Hồi quy không hơn luật có ngưỡng thì dùng luật có ngưỡng, đúng điểm
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
| P1, P2 | Đề xuất ở 2 điểm τ | 1 mỗi điểm | P1: **2**, P2: 1 |
| E5b | Đề xuất, bỏ quán tính | 1 (nên có) | — |
| O, RouteLLM | Theo câu hỏi | Ngoại tuyến | Ngoại tuyến |

Lặp 2 lần cho S, C, P1 để đo độ dao động giữa các lần chạy; còn lại 1 lần. Phần
lớn bài liên quan chỉ báo cáo một lần chạy. Khoảng tin cậy lấy bằng bootstrap
trên câu hỏi (mục 6).

**Số câu phải chạy thật**, với tập phát triển khoảng 100 câu và tập kiểm tra
khoảng 155 câu:

| Phần | Bắt buộc | Thêm nếu làm "nên có" |
|---|---|---|
| Pilot | 9 × 4 = 36 | — |
| Tập phát triển | 100 × 6 = 600 | + 300 (R25, R75, E5b) |
| Tập kiểm tra | 155 × 9 ≈ 1.400 | — |
| Tổng | **khoảng 2.050** | khoảng 2.350 |

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
| B2 | Ablation | E5a (AUC), E5b | Tập phát triển | Nên có |
| B3 | So sánh thiết kế với các bài liên quan | Từ [gói C](nghien-cuu-dinh-tuyen.md#3-so-sánh-với-thiết-kế-của-đồ-án); không so số vì khác benchmark | — | **Bắt buộc** (phần tổng quan) |

## 8. Công cụ còn thiếu

| # | Việc | Vì sao | Khi nào |
|---|---|---|---|
| 1 | **Xuất dữ liệu** ra CSV: một dòng mỗi câu × lần lặp (cấu hình, mức, đúng/sai, đáp án con, chi phí, token mô hình mạnh, số bước, bước rẻ, đổi mô hình, cache, độ trễ) và một dòng mỗi bước (đặc trưng, lựa chọn, mô hình, token) | Đầu vào cho mọi phân tích, E3 và gói F | Trước bước 3 |
| 2 | **Script chạy ma trận cấu hình**: đặt biến, khởi động lại gateway, chờ sẵn sàng, chạy suite, ghi mã run vào file danh sách lần chạy | Lặp lại được, tránh nhầm nhãn | Trước bước 3 |
| 3 | **Chạy tiếp run bị gián đoạn** khi hết lượt của gói Plus (gói B2) | Một run 155 câu khó chạy liền | Trước bước 3 |
| 4 | **Đánh giá ngoại tuyến theo câu hỏi** (E3): chạy router RouteLLM trên prompt, tra kết quả S/C, quét ngưỡng, oracle | RQ3; "chạy lại bài báo" | Sau bước 3 |
| 5 | Router đề xuất: huấn luyện trên CSV, chính sách có τ và quán tính | Gói F | Bước 4 |
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
| | | | | | | | | |

Giá trị cố định (mục 3):

| Mục | Giá trị |
|---|---|
| Mô hình mạnh | |
| Mô hình rẻ | |
| Hermes commit | `93c9360a` |
| `max_turns`, `timeout_s`, song song | |
| Bộ công cụ `api_server` | |
| Ngày bảng giá | |
| Tag bộ định tuyến đã khóa, τ | |
