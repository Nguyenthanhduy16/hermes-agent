# Kế hoạch triển khai Đồ án 2: định tuyến mô hình theo từng bước

Cập nhật 11/11/2026. Hạn: cuối tháng 12/2026. Các mốc trong báo cáo tiến độ
04/11 chỉ là tạm tính; file này là kế hoạch đang dùng.

## 1. Điều kiện đã chốt

| Mục | Quyết định |
|---|---|
| Hạn | Cuối tháng 12/2026, còn khoảng 7 tuần |
| Máy chạy thực nghiệm | Máy Linux cá nhân có Docker |
| Nguồn mô hình | 3 tài khoản ChatGPT Plus, thêm dưới 50 USD API dự phòng |
| Benchmark làm trước | InfiAgent-DABench (chính), DA-Code (mẫu nhỏ). DSBench và MatPlotBench quyết định sau khi hai bộ đầu chạy được |
| Góp ý của Thầy | Chạy lại các bài báo liên quan để xem họ triển khai thế nào; làm rõ đầu ra của bộ định tuyến ở mỗi bước |

## 2. Đầu ra của bộ định tuyến (đề xuất, chờ Thầy duyệt)

- Ở mỗi bước của vòng lặp, bộ định tuyến trả một nhãn trong hai mức `rẻ` /
  `mạnh`; Hermes gọi mô hình tương ứng cho đúng bước đó.
- Bên trong là một điểm rủi ro từ 0 đến 1 ("mô hình rẻ làm hỏng bước này"),
  so với ngưỡng τ. τ là núm chỉnh giữa chi phí và chất lượng.
- Có quán tính: chỉ đổi mô hình khi lợi ích vượt chi phí mất prompt cache.
- Mỗi bước ghi lại nhãn, điểm, lý do và các đặc trưng đã dùng.

## 3. Đã xác nhận trong mã nguồn Hermes (commit ghim `93c9360a`)

- Plugin đăng ký được middleware `llm_request`: chạy trước mỗi lần gọi mô
  hình, nhận toàn bộ hội thoại, được phép đổi trường `model` của request
  (`hermes_cli/middleware.py`, `agent/turn_api_request.py`).
- Middleware bọc lời gọi (`llm_execution`) chỉ được gọi xuống một lần, nên
  không thể gọi mô hình rẻ rồi gọi lại mô hình mạnh trong cùng một bước.
- Hook `pre/post_api_request` nhận `model` là mô hình mặc định của phiên,
  không phải mô hình đã bị đổi: telemetry cần bảng riêng cho quyết định định
  tuyến.
- `terminal.docker_volumes` gắn được thư mục dữ liệu vào sandbox.

## 4. Các gói việc

| # | Gói việc | Trạng thái |
|---|---|---|
| A | Máy thực nghiệm: Hermes ghim, Docker sandbox, API server, telemetry; chọn cặp mô hình mạnh/rẻ; thử đổi mô hình giữa các bước và đo token cache | Chưa làm (chỉ làm được trên máy Linux) |
| B | Bộ đánh giá: check `answers`, bộ chuyển đổi DABench, chia tập phát triển/kiểm tra | Xong phần DABench ([hướng dẫn](benchmark.md#9-infiagent-dabench)); chưa chạy với mô hình thật |
| B2 | Chạy tiếp run bị gián đoạn (giới hạn lượt của gói Plus); giới hạn số bước mỗi tác vụ | Chưa làm |
| B3 | DA-Code: mẫu khoảng 40 tác vụ, lấy file kết quả từ sandbox, gọi `evaluate.py` gốc | Chưa làm |
| B4 | Bộ tiếng Việt khoảng 20 tác vụ cùng định dạng | Chưa làm |
| C | Ghi chú nghiên cứu: chạy lại RouteLLM, đọc mã FrugalGPT; đầu vào, đầu ra, dữ liệu huấn luyện, so với thiết kế này | Xong ghi chú và bảng so sánh ([gói C](nghien-cuu-dinh-tuyen.md)); chưa đọc toàn văn các bài 2026, chưa chạy lại mã |
| D | Khung định tuyến trong `assistant-core`: chính sách luôn mạnh, luôn rẻ, ngẫu nhiên, luật; trích đặc trưng; quyết định ghi vào telemetry và hiện trên Agent Console; lỗi thì chạy mô hình mặc định | Xong code và test giả lập ([hướng dẫn](dinh-tuyen.md)); chưa chạy trong Hermes thật |
| E | Thu dữ liệu trên tập phát triển với ba chính sách đối chứng | Chưa làm |
| F | Bộ định tuyến đề xuất: luật có ngưỡng, rồi mô hình nhỏ dễ giải thích; chỉnh trên tập phát triển rồi khóa | Chưa làm |
| G | Đánh giá trên tập kiểm tra, lặp 3 lần; script phân tích có khoảng tin cậy | Chưa làm |
| H | Báo cáo cuối và demo | Chưa làm |

Thứ tự việc, ma trận cấu hình, chỉ số, thống kê và danh sách hình: [thiết kế thực nghiệm](thiet-ke-thuc-nghiem.md).

## 5. Lịch theo tuần

| Tuần | Việc | Kết quả cần có |
|---|---|---|
| 1 (11–17/11) | A, B, bắt đầu C | Chạy được `dabench-pilot` với "luôn mạnh" và "luôn rẻ"; có chi phí thật mỗi câu |
| 2 (18–24/11) | C, B2, D | Ghi chú gửi Thầy; chạy được 4 chính sách đối chứng |
| 3 (25/11–01/12) | D, E, B3 | Dữ liệu tập phát triển; DA-Code chạy được mẫu nhỏ |
| 4 (02–08/12) | E, F, B4 | Bảng số liệu đầu tiên; bản định tuyến thử nghiệm |
| 5 (09–15/12) | F | Bộ định tuyến đã khóa |
| 6 (16–22/12) | G | Bảng và biểu đồ kết quả trên tập kiểm tra |
| 7 (23–31/12) | H | Báo cáo cuối, kịch bản demo |

**Điểm cắt.** Hết tuần 4 mà chưa đủ dữ liệu thì bộ định tuyến đề xuất dừng ở
dạng luật có ngưỡng đã chỉnh. Thứ bỏ đầu tiên là DA-Code, sau đó giảm bộ tiếng
Việt xuống mức chỉ đủ demo.

## 6. Rủi ro cần kiểm tra ngay tuần 1

- Gói Plus có cho dùng một cặp mô hình mạnh/rẻ qua Hermes không, và có báo
  token đọc từ cache không. Nếu không, chuyển sang API trả tiền với quy mô
  nhỏ hơn.
- Giới hạn lượt dùng của gói Plus: đo số câu chạy được mỗi ngày trên một tài
  khoản trước khi lên lịch chạy tập phát triển.
- Cố định bộ công cụ của nền tảng API server và tắt web khi chạy benchmark,
  vì đề và đáp án DABench công khai.

## 7. Ngoài phạm vi đến hết tháng 12

Danh tính dùng chung giữa các kênh, bộ nhớ riêng từng người, duyệt hành động,
routine, tin thoại, kỹ năng SKILL.md tái sử dụng, đối chứng với dịch vụ định
tuyến thương mại. Các chức năng Lab giữ nguyên, không phát triển thêm.
