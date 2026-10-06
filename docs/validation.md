# Kiểm chứng giai đoạn 1 — 05/10/2026

## Đã hoàn thành trên máy phát triển

- Cài Hermes native Windows bằng installer chính thức, mã nguồn cố định tại `93c9360a8da592cee43e8895403e91c7a920b0ce`. Installer kết thúc thành công, lệnh kiểm tra phiên bản chạy được.
- Python của Hermes: 3.14.7. Adapter dùng HTTPX 0.28.1, tương thích với runtime Hermes đã cài.
- Cài môi trường riêng cho adapter; sao chép plugin và cấu hình vào `.runtime/hermes-home`.
- Xác nhận bản adapter đã sao chép khớp mã nguồn; cấu hình Zalo không bật công cụ, bộ nhớ dùng chung hoặc cơ chế phát lại tin của Hermes.
- Ở lần thiết lập đầu, `.env` chưa có Bot Token và kiểm tra cấu hình dừng đúng ở thông báo thiếu token. Sau khi người dùng nhập token, `ZaloCheck` đã được kiểm tra với API thật như ghi dưới đây.

## Kết quả kiểm thử

| Nhóm | Kết quả | Phạm vi |
| --- | --- | --- |
| `run.ps1 Test` | 37 đạt; 4 kiểm thử Hermes được bỏ qua ở môi trường nhỏ | API Zalo mô phỏng, phân quyền, định tuyến, chống trùng, timeout, giới hạn tin, che bí mật, khởi động/khôi phục và cấu hình |
| `run.ps1 TestHermes` | 4 đạt | Plugin loader, lớp xử lý tin và session của Hermes thật; mạng Zalo và phản hồi model vẫn được mô phỏng |
| `run.ps1 Demo` | Đạt | Một tin hợp lệ được xử lý; tin trùng và người chưa có quyền bị chặn; không gọi dịch vụ |
| Kiểm tra cú pháp Python và định dạng Git | Đạt | Các file nguồn, script và kiểm thử |

Tổng cộng **41 kiểm thử đạt qua hai môi trường**. Đây chưa phải kiểm thử Zalo hoặc AI thật.

### Sửa lỗi đường dẫn tiếng Việt trên Windows

Đã tái hiện lỗi `The system cannot find the path specified` khi wrapper `hermes.cmd` đọc đường dẫn `Đề tài` dưới bảng mã 437. Các script `TestHermes`, `Model` và `Start` hiện dùng launcher native `hermes.exe`. Sau sửa, cả 4 kiểm thử Hermes đều đạt trên Windows PowerShell 5.1 và PowerShell 7.6.5 dưới cùng bảng mã 437; không cần đổi tên thư mục hay thay đổi bảng mã terminal của người dùng.

## Kiểm tra Hermes còn cảnh báo

`hermes doctor` kết thúc với mã 1 do ba nhóm kiểm tra npm báo lỗ hổng trong dependency upstream: agent-browser, web workspace và ui-tui. Kiểm tra Python, SQLite, HTTPX, SDK và TLS hoạt động. Dự án giữ nguyên lockfile chính thức; chưa khởi chạy giao diện web, browser hoặc computer-use. Cần rà soát/nâng bản upstream và chạy lại kiểm thử trước khi triển khai thường xuyên.

Các cảnh báo mức high gồm `brace-expansion`/`js-yaml` (từ chối dịch vụ), `undici` (WebSocket và kiểm tra TLS của BalancedPool); còn cảnh báo moderate ở `@vitest/mocker`/`vitest` và `yaml`. Chi tiết phiên bản, advisory và nguồn lấy từ lần kiểm tra đã chạy được lưu cục bộ trong `.runtime/doctor-summary.txt`. Việc không bật các giao diện/công cụ trên không có nghĩa toàn bộ dependency đã được xác nhận an toàn.

## Chưa xác minh

- Nhận/gửi tin thật, hạn mức tài khoản và việc thêm bot vào nhóm thực tế. `getMe` đã xác nhận token hợp lệ và trả `can_join_groups: true`; điều này chưa phải kiểm thử hội thoại nhóm.
- Cấu hình nhà cung cấp/model và một lượt phản hồi AI thật.
- Kết quả thực tế khi mất mạng, máy ngủ hoặc bị Zalo giới hạn trong phiên sử dụng thật.
- Quyền nhóm của tài khoản: Zalo vẫn mô tả tính năng Beta/thử nghiệm nội bộ.

Bước tiếp theo là điền token trên máy, lấy ID/cấp quyền, chọn model rồi thử tin riêng theo README. Sau đó mới thử nhóm.

## Sửa kiểm tra webhook 404

Đã quan sát API thật: `getMe` thành công; `getWebhookInfo` trả HTTP 200 với `ok:false`, `error_code:404`, `description:Not Found`. Adapter cũ coi đây là lỗi chặn toàn bộ bước chuẩn bị.

Giờ lỗi 404 riêng của phương thức này được phân loại là **không xác minh được webhook**, không được biến thành kết luận “không có webhook”. `ZaloCheck` cảnh báo và cho phép thử `Discover`; gateway có thể thử polling nhưng vẫn dừng khi API polling báo lỗi cấu hình/xung đột. Webhook đã xác nhận có URL vẫn bị chặn, các lỗi xác thực/hạn mức/máy chủ không bị bỏ qua, và không có thao tác xóa webhook.

Đã chạy lại `ZaloCheck` thật thành công với cảnh báo chưa xác minh. Không lấy tin nhắn, gửi tin hoặc gọi AI trong lần chẩn đoán này. Kiểm thử bổ sung dùng dữ liệu giả bao phủ 404 đúng phương thức, lỗi API khác, trạng thái webhook đang hoạt động, xung đột polling và thông báo lỗi không lộ bí mật.
# Cập nhật tính năng công việc — 2026-10-06

- Người dùng xác nhận: bot nhắn riêng nhận–trả lời được và nhớ tên trong cùng hội thoại. Đây là xác nhận của người dùng; chưa có xác nhận thử nhóm hoặc lịch sử sau khởi động lại.
- Thêm tra tài liệu UTF-8 `.md`/`.txt` bằng trích đoạn có nguồn, ACL theo tài liệu và 10 câu hỏi mẫu + 3 câu không có thông tin. Không gọi model cho lệnh tài liệu; chưa hỗ trợ PDF/Word/tìm kiếm ngữ nghĩa.
- Thêm nhắc việc SQLite có tạo/xem/hủy, giữ đích cố định, claim ngay trước từng lần gửi, chặn quyền bị thu hồi, giữ đầy đủ Unicode và không tự gửi lại kết quả không rõ.
- Thêm Google Sheets: đọc tab lịch với ACL từng dòng; issue có bản nháp 10 phút, xác nhận đúng người/đúng chat/đúng đích, ghi RAW, không POST trước xác nhận, không tự lặp ghi khi timeout.
- Thêm góp ý riêng và báo cáo tổng hợp chỉ đọc các cột usage của phiên Zalo. Chi phí thực tế/ước tính/thiếu được tách rõ; chỉ thời gian đã đo mới tham gia trung bình.
- Cài phần Google trong môi trường dự án; Hermes dùng chính `pm.cli install --extra google` của bản đã cài. Không sửa lockfile/mã nguồn Hermes, không đăng ký dịch vụ trả phí.
- Lần chạy cuối: `unittest discover -s tests -q`: **135 bài, 129 đạt, 6 bỏ qua** (5 bài yêu cầu runtime Hermes chạy riêng, 1 bài tạo symlink thật bị Windows hạn chế quyền). Kiểm thử mô phỏng reparse/junction vẫn đạt.
- `run.ps1 TestHermes`: **5/5 đạt** với loader/base/session của Hermes thực; Zalo, model và lần gửi nhắc trong bài thử đều mô phỏng.
- `FeatureDemo`: câu hỏi có nguồn, không tìm thấy, tạo/xem/hủy nhắc chạy ngoại tuyến. `SetupSheets`, `PrepareDocs`, `PrepareSheets`, `Update`, `Check` và `Report` đã chạy trên máy.
- Báo cáo ban đầu đọc tổng usage, không đọc nội dung hội thoại. Chỉ là số Hermes ghi nhận, không thay thế hóa đơn nhà cung cấp.
- Đã sao chép plugin mới vào runtime. Bot đang chạy cần dừng/bật lại để nạp code mới. Không tự gửi tin Zalo, không tạo nhắc thật, không đọc/ghi Google Sheets thật trong lượt phát triển này.
- Còn cần người dùng: tài liệu thật, bảng Sheets thử nghiệm và khóa service account lưu trên máy; chạy [checklist thử thật](pilot-checklist.md). Không xem kết quả mô phỏng là tính năng đã chạy thành công ngoài thực tế.

---
