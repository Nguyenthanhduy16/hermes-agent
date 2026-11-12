# Thiết kế thực nghiệm: định tuyến mô hình theo từng bước

Cập nhật 12/11/2026. Tài liệu này chốt **làm gì, theo thứ tự nào, chạy cấu hình
nào, đo gì, vẽ hình gì** cho Đồ án 2. Công cụ chạy đã có: [benchmark](benchmark.md)
và [khung định tuyến](dinh-tuyen.md). Bối cảnh các bài liên quan: [gói C](nghien-cuu-dinh-tuyen.md).
Lịch tuần vẫn theo [kế hoạch đồ án](ke-hoach-do-an.md).

Nguyên tắc chung theo quy trình chuẩn:

1. **Chốt câu hỏi, chỉ số và cách phân tích trước khi chạy** tập kiểm tra.
2. **Tập phát triển dùng để xây và chỉnh; tập kiểm tra chỉ chạy một lần**, sau
   khi đã khóa bộ định tuyến.
3. **Mọi cấu hình chỉ khác nhau ở đúng một biến**: chính sách định tuyến (và τ).
4. **Mỗi con số đi kèm khoảng tin cậy** và số câu đã chấm.

## 1. Câu hỏi nghiên cứu

| # | Câu hỏi | So sánh |
|---|---|---|
| RQ1 | Định tuyến theo bước giảm được bao nhiêu chi phí mà không làm giảm rõ độ chính xác so với luôn dùng mô hình mạnh? | Đề xuất ↔ luôn mạnh, luôn rẻ |
| RQ2 | Ở **cùng mức chi phí**, bộ định tuyến đề xuất có tốt hơn các cách đơn giản không? | Đề xuất ↔ ngẫu nhiên, luật, theo câu hỏi |
| RQ3 | Định tuyến **theo bước** có lợi hơn định tuyến **theo câu hỏi** không? | Đề xuất ↔ theo câu hỏi, oracle theo câu hỏi |
| RQ4 | Đặc trưng nào và quán tính (chống mất cache) đóng góp bao nhiêu? | Ablation trên tập phát triển |
| RQ5 (nếu kịp) | Bộ định tuyến đã khóa có giữ được trên bộ khác không? | DA-Code mẫu nhỏ, bộ tiếng Việt |

Viết RQ, chỉ số chính và mức chênh chấp nhận được (mục 5) thành một trang, gửi
Thầy duyệt **trước** khi chạy tập kiểm tra.

## 2. Biến cố định

Ghi các giá trị dưới đây vào bảng mục 9 trước lần chạy đầu tiên. Giữ nguyên cho
mọi cấu hình; đổi bất kỳ giá trị nào thì phải chạy lại mọi cấu hình.

| Nhóm | Cố định |
|---|---|
| Agent | Commit Hermes (`93c9360a`), `agent.max_turns`, compression, memory tắt |
| Mô hình | Tên chính xác (có phiên bản) của mô hình mạnh và rẻ; mô hình mặc định của Hermes = mô hình mạnh; temperature, reasoning effort của từng mức |
| Công cụ | `platform_toolsets.api_server` cố định, **không có web**; Docker sandbox; thư mục dữ liệu chỉ đọc |
| Benchmark | Mã băm suite (in trong `bench list`), `timeout_s`, số request song song (ví dụ 2) |
| Mã | Commit của repo này khi chạy; bộ định tuyến đề xuất có tag git riêng khi khóa |
| Giá | `pricing.yaml` theo giá API công bố tại một ngày cố định, kể cả `cache_read` |

Dùng gói ChatGPT Plus thì không có hóa đơn theo token. Chi phí báo cáo là **chi
phí quy đổi theo giá API** trong `pricing.yaml`; ghi rõ điều này trong báo cáo.

## 3. Thứ tự việc từ đầu

### Bước 0. Chốt câu hỏi và kế hoạch phân tích

Mục 1, 5, 6 của tài liệu này; Thầy duyệt. Kết quả: một trang.

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
  cấp không báo, bỏ RQ4 phần cache, ghi vào giới hạn.
- [ ] Sandbox đọc được `/data/dabench/*.csv`; agent không có công cụ web.
- [ ] `bench show` có số bước, chi phí, `cheap_step_share` cho từng câu.

### Bước 2. Pilot và cổng quyết định

Chạy `dabench-pilot` (9 câu, 3 mỗi mức) với `always-strong` và `always-cheap`,
mỗi cấu hình 2 lần. Đo:

| Đo | Dùng để |
|---|---|
| Độ chính xác mỗi mô hình | **Khoảng trống**: mô hình mạnh hơn rẻ bao nhiêu |
| Chi phí, số bước, thời gian mỗi câu | Ước tính ngân sách cho mục 4 |
| Số câu chạy được mỗi ngày trên một tài khoản Plus | Lên lịch chạy |
| Tỷ lệ lỗi, quá thời gian | Chỉnh `timeout_s`, `max_turns` |

**Cổng quyết định** (kiểm tra lại trên toàn tập phát triển ở bước 3):

- Mô hình mạnh hơn mô hình rẻ **dưới khoảng 10 điểm phần trăm** → định tuyến
  gần như không có gì để lấy lại. Đổi sang cặp mô hình chênh nhau rõ hơn.
- Mô hình rẻ không rẻ hơn ít nhất khoảng 3 lần mỗi câu → tiết kiệm không đáng
  kể. Đổi cặp.
- Ngân sách ước tính cho mục 4 vượt khả năng → giảm số lần lặp hoặc lấy mẫu tập
  kiểm tra theo mức (`--per-level`), **trước** khi chạy.

### Bước 3. Thu dữ liệu trên tập phát triển (gói E)

Chạy `dabench-dev` với các chính sách sau:

| Cấu hình | Số lần | Vai trò |
|---|---|---|
| `always-strong` | 2–3 | Trần chất lượng; một nửa của oracle |
| `always-cheap` | 2–3 | Sàn chi phí; nửa còn lại của oracle |
| `random`, `STRONG_SHARE` = 0,25 / 0,5 / 0,75 | 1 mỗi mức | Đường cơ sở; **dữ liệu để học** |
| `rule` | 1 | Mốc luật đơn giản |

Vì sao chính sách `random` quan trọng: lựa chọn ở mỗi bước **không phụ thuộc
trạng thái**, nên so kết quả các bước rẻ và mạnh ở cùng trạng thái cho ước
lượng không thiên lệch về tác động của việc chọn mô hình rẻ. Đây là dữ liệu
huấn luyện kiểu MTRouter.

Tính ngay, không tốn lượt gọi:

- **Oracle theo câu hỏi**: mỗi câu dùng mô hình rẻ nếu mô hình rẻ làm đúng, còn
  lại dùng mô hình mạnh. Đây là cận trên của mọi bộ định tuyến theo câu hỏi.
- Phân bố số bước mỗi câu của từng mô hình. Mô hình rẻ hay đi vòng nhiều bước
  hơn và có thể ăn mất phần tiết kiệm.

### Bước 4. Xây và khóa bộ định tuyến đề xuất (gói F)

Chỉ dùng dữ liệu tập phát triển.

1. **Nhãn.** Không có nhãn đúng/sai cho từng bước, chỉ có kết quả cả câu. Từ
   các run `random`, học `P(câu đúng | đặc trưng bước, chọn rẻ)` và
   `P(câu đúng | đặc trưng bước, chọn mạnh)`. Điểm rủi ro = hiệu hai xác suất.
2. **Mô hình.** Theo thứ tự: luật có ngưỡng trên 1–2 đặc trưng → hồi quy
   logistic vài đặc trưng → cây quyết định nông. Dừng ở mức đơn giản nhất mà
   không kém rõ mức phức tạp hơn.
3. **Kiểm chứng chéo theo file CSV** bên trong tập phát triển, giống cách chia
   dev/test, để chọn đặc trưng và tránh khớp quá mức.
4. **Quét τ** để có đường chi phí ↔ chính xác trên tập phát triển. Chọn trước
   2–3 điểm làm việc, ví dụ "rẻ nhất mà vẫn trong 3 điểm của luôn mạnh" và
   "PGR 80%".
5. **Ablation trên tập phát triển**: bỏ từng nhóm đặc trưng; bỏ quán tính.
6. **Khóa**: commit, đặt tag (ví dụ `router-v1`), ghi τ đã chọn vào bảng mục 9.
   Sau thời điểm này không sửa bộ định tuyến.

Dữ liệu tập phát triển nhỏ: khoảng 100 câu × 3 run ngẫu nhiên × vài bước mỗi
câu, và các bước trong cùng một câu tương quan với nhau. Thiếu tín hiệu thì
dừng ở luật có ngưỡng, đúng điểm cắt trong kế hoạch.

### Bước 5. Đánh giá trên tập kiểm tra (gói G)

Chạy `dabench-test` với ma trận mục 4, **xen kẽ các cấu hình theo thời gian**
(ví dụ mỗi buổi chạy một lần lặp của mọi cấu hình) để thay đổi phía nhà cung
cấp theo ngày không dồn vào một cấu hình. Ghi ngày giờ từng run.

### Bước 6. Phân tích, hình và bảng

Mục 5, 6, 7.

### Bước 7. Chuyển giao (nếu kịp)

Bộ định tuyến đã khóa, không chỉnh lại, chạy trên mẫu DA-Code và bộ tiếng Việt
với `always-strong`, `always-cheap` và đề xuất, mỗi cấu hình một lần.

## 4. Ma trận cấu hình trên tập kiểm tra

| Mã | Cấu hình | Lần lặp | Bắt buộc |
|---|---|---|---|
| S | `always-strong` | 3 | Có |
| C | `always-cheap` | 3 | Có |
| R25, R50, R75 | `random` ở 0,25 / 0,5 / 0,75 | 1 mỗi mức | R50 bắt buộc, còn lại nếu đủ ngân sách |
| L | `rule` | 3 | Có |
| Q | Theo câu hỏi: quyết định ở bước 1, giữ cả lượt (chính sách mới, cần viết) | 3 | Có, cho RQ3 |
| P1, P2 | Đề xuất ở 2 điểm τ đã khóa | 3 | Có |
| O | Oracle theo câu hỏi | Tính từ S và C | Không tốn lượt |

Mỗi cấu hình là một lần khởi động gateway với `ASSISTANT_ROUTER_POLICY`, tham số
và `ASSISTANT_RUN_LABEL` riêng, rồi một lệnh `bench run --repeats N`. Dùng
`always-strong` thay vì `off` để mốc cũng có ghi quyết định.

**Ngân sách.** Số request = số câu × số lần lặp × số cấu hình. Ví dụ tập kiểm
tra khoảng 150 câu, 6 cấu hình bắt buộc × 3 lần + R50 × 1 → khoảng 2.850 câu.
Nhân với chi phí và thời gian mỗi câu đo ở pilot. Mỗi run tối đa 500 request,
nên 150 câu × 3 lần vừa trong một run. Không đủ thì giảm theo thứ tự: bỏ
R25/R75 → còn 2 lần lặp → lấy mẫu tập kiểm tra theo mức. Quyết định trước khi
chạy và ghi lại.

## 5. Chỉ số và thống kê

### Chỉ số

| Loại | Chỉ số | Nguồn |
|---|---|---|
| **Chính** | Độ chính xác theo câu, **lỗi và quá thời gian tính là sai** (`Pass rate`) | `bench show` |
| **Chính** | Chi phí quy đổi mỗi câu (USD) | telemetry + `pricing.yaml` |
| Phụ | Ba chỉ số gốc của DABench (bỏ câu không có câu trả lời) kèm số câu đã chấm | `bench show` |
| Phụ | Tỷ lệ bước rẻ, số lần đổi mô hình mỗi câu, số bước mỗi câu | `bench compare` |
| Phụ | Tỷ lệ token đọc từ cache, độ trễ trung vị và p95, tỷ lệ lỗi công cụ | telemetry |
| Dẫn xuất | **PGR** = (acc − acc_rẻ) / (acc_mạnh − acc_rẻ) | Tính |
| Dẫn xuất | **Tiết kiệm** = 1 − chi phí / chi phí_mạnh | Tính |
| Dẫn xuất | **CPT(80%)**: tỷ lệ bước mạnh tối thiểu để đạt PGR 80% | Tính từ đường quét τ |

Lấy độ chính xác có lỗi tính là sai làm chỉ số chính: bộ định tuyến làm agent
chạy quá thời gian thì phải bị phạt.

### Thống kê

- **Đơn vị là câu hỏi.** Lấy trung bình các lần lặp của từng câu trước, rồi mới
  tính trên cả tập.
- **Khoảng tin cậy 95% bằng bootstrap ghép cặp theo câu**, khoảng 10.000 lần lấy
  mẫu lại: lấy lại cùng một bộ câu cho cả hai cấu hình khi so chênh lệch độ chính
  xác và chênh lệch chi phí.
- **Kiểm định ghép cặp**: McNemar cho đúng/sai của hai cấu hình trên cùng câu
  (một lần lặp), hoặc kiểm định hoán vị ghép cặp trên trung bình các lần lặp.
- **Chênh lệch chấp nhận được** cho "không kém hơn" (ví dụ 5 điểm phần trăm) chốt
  ở bước 0. Với khoảng 150 câu, khoảng tin cậy của một tỷ lệ là khoảng ±8 điểm,
  của chênh lệch ghép cặp hẹp hơn. Chênh lệch nhỏ hơn vài điểm thường không kết
  luận được; báo cáo khoảng tin cậy, không chỉ giá trị p.
- Báo cáo **theo mức L1/L2/L3** là phân tích phụ, đã nêu trước; không chọn mức
  sau khi thấy kết quả.

## 6. Hình

Mỗi hình ghi rõ dữ liệu cần có. Hình chính là H1.

| # | Hình | Trục / dạng | Dữ liệu | Trả lời |
|---|---|---|---|---|
| **H1** | **Mặt phẳng chi phí ↔ chính xác** | x: chi phí mỗi câu, y: độ chính xác; điểm S, C, L, Q, P1, P2, O; nối R25–R50–R75 thành đường ngẫu nhiên; thanh sai số 95% hai chiều | Tập kiểm tra | RQ1, RQ2 |
| H2 | Đường quét τ trên tập phát triển | x: tỷ lệ bước mạnh, y: PGR; đường đề xuất, đường chéo ngẫu nhiên, điểm luật; đánh dấu τ đã khóa | Tập phát triển | Cách chọn τ; CPT |
| H3 | Độ chính xác và chi phí theo mức | Hai ô cạnh nhau: cột nhóm theo L1/L2/L3 cho từng cấu hình | Tập kiểm tra | Định tuyến có lợi ở loại câu nào |
| H4 | Tỷ lệ bước rẻ theo vị trí bước | x: bước 1, 2, 3…, y: tỷ lệ chọn rẻ; một đường mỗi chính sách | `route_choice` theo bước | Bộ định tuyến dùng mô hình mạnh ở đâu |
| H5 | Dòng thời gian định tuyến | Mỗi hàng một câu, mỗi ô một bước, màu = rẻ/mạnh, dấu = công cụ lỗi; vài câu tiêu biểu | `session_steps` | Giải thích được, ví dụ minh họa |
| H6 | Phân bố số bước mỗi câu | Hộp hoặc violin theo cấu hình | telemetry | Mô hình rẻ có làm câu dài hơn không |
| H7 | Cache khi đổi mô hình | x: số lần đổi mỗi câu, y: tỷ lệ token đọc từ cache; có và không có quán tính | telemetry, ablation | RQ4 phần cache |
| H8 | Trọng số hoặc độ quan trọng đặc trưng | Cột ngang | Mô hình đã khóa | RQ4, tính giải thích |

Bảng:

| # | Bảng |
|---|---|
| B1 | Kết quả chính trên tập kiểm tra: mỗi cấu hình một hàng; độ chính xác, chi phí, tiết kiệm, PGR, bước rẻ, đổi mô hình, số bước, độ trễ; kèm khoảng tin cậy |
| B2 | Ablation trên tập phát triển |
| B3 | Chi phí tách theo token vào, ra, đọc cache và theo mô hình |
| B4 | So với các bài liên quan về thiết kế (từ [gói C](nghien-cuu-dinh-tuyen.md#3-so-sánh-với-thiết-kế-của-đồ-án)), không so số vì khác benchmark |

## 7. Công cụ còn thiếu

| # | Việc | Vì sao | Gói |
|---|---|---|---|
| 1 | Chính sách **theo câu hỏi** (Q): quyết định ở bước 1, giữ cả lượt | Mốc cho RQ3 | D |
| 2 | **Xuất dữ liệu** ra CSV: một dòng mỗi câu × lần lặp (cấu hình, mức, đúng/sai, đáp án con, chi phí, số bước, bước rẻ, đổi mô hình, cache, độ trễ) và một dòng mỗi bước (đặc trưng, lựa chọn, mô hình, token) | Đầu vào cho phân tích và vẽ hình; hiện chỉ có `bench show/compare` dạng bảng | G |
| 3 | **Script chạy ma trận cấu hình**: đặt biến, khởi động lại gateway, chờ sẵn sàng, chạy suite, ghi mã run vào file danh sách lần chạy | Lặp lại được, tránh nhầm nhãn | E |
| 4 | **Chạy tiếp run bị gián đoạn** khi hết lượt của gói Plus | Một run 450 câu khó chạy liền | B2 |
| 5 | **Script phân tích**: bootstrap ghép cặp, McNemar, PGR, oracle, các hình mục 6 | Gói G | G |
| 6 | Chính sách đề xuất có tham số τ và quán tính | Gói F | F |

Nên làm 2 và 3 ngay sau pilot, trước khi chạy tập phát triển. Thiếu chúng thì
phải đối chiếu mã run và nhãn bằng tay.

## 8. Mối đe dọa đến tính hợp lệ

| Rủi ro | Cách xử lý |
|---|---|
| Đề và đáp án DABench công khai | Bỏ công cụ web; ghi rằng mô hình có thể đã thấy dữ liệu khi huấn luyện, ảnh hưởng như nhau lên mọi cấu hình |
| Chỉnh trên tập kiểm tra | Tập kiểm tra chạy một lần sau khi khóa; mọi lần chạy thử ghi lại |
| Nhà cung cấp thay đổi mô hình hoặc tải theo thời gian | Ghi tên mô hình có phiên bản, chạy xen kẽ, ghi ngày |
| Mô hình không tất định | 3 lần lặp, tính theo câu, báo khoảng tin cậy |
| Cách chấm khác bài gốc | Đã liệt kê trong [benchmark](benchmark.md#cách-chấm-và-điểm-khác-với-bài-báo); báo cáo cả chỉ số gốc |
| Chi phí quy đổi, không phải hóa đơn thật | Ghi ngày của bảng giá; báo cáo cả token thô |
| Gateway khởi động lại giữa lượt làm bước sau bị coi là bước đầu | Không khởi động lại khi đang chạy run; lọc các câu bị ảnh hưởng |

## 9. Sổ thực nghiệm

Điền khi chạy. Mỗi dòng là một run benchmark.

| Ngày | Gói | Suite (mã băm) | Cấu hình | Tham số | Nhãn | Mã run | Commit repo | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

Giá trị cố định (mục 2):

| Mục | Giá trị |
|---|---|
| Mô hình mạnh | |
| Mô hình rẻ | |
| Hermes commit | `93c9360a` |
| `max_turns`, `timeout_s`, song song | |
| Bộ công cụ `api_server` | |
| Ngày bảng giá | |
| Tag bộ định tuyến đã khóa, τ | |
