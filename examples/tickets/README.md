# Bảng riêng để thử đổi ca

Đây là dữ liệu giả. Không phải lịch lab thật và không được dùng để sửa bảng lịch hiện tại.

## Tạo bảng thử

1. Tạo **một Google Spreadsheet mới**, đặt tên `LabCoach Tickets — TEST`. ID phải khác `ZALO_SHEETS_ID` đang dùng cho bot. Không chỉ tạo tab mới trong bảng lịch thật.
2. Đặt tên tab `TicketsTest`. Giữ tối đa **1.001 hàng** (gồm dòng tiêu đề); xóa các hàng thừa nếu cần. Mỗi phân công có một dòng, tối đa 1.000 ca.
3. Định dạng cột A:H là **Văn bản thuần túy / Plain text**. Nhập dữ liệu từ `assignments.csv`; tắt chuyển văn bản thành số/ngày/công thức khi nhập CSV. Không dùng ô gộp hoặc công thức trong A:H.
4. Đổi ngày mẫu năm 2035 thành các ngày tương lai phù hợp để thử. Dùng định dạng đầy đủ như `2026-10-20T08:00:00+07:00`; không chỉ nhập `Sáng` để thay thế thời gian.
5. Chia sẻ riêng bảng thử cho service account có quyền chỉnh sửa. Người thử khác dùng quyền xem. Nếu cấu hình bảo vệ tab, chỉ cấp quyền sửa cho service account; chủ sở hữu vẫn có thể sửa nên cần thỏa thuận không chỉnh tay khi dịch vụ hoạt động.
6. Cấu hình các biến thử nghiệm bên dưới trên máy; không gửi file khóa vào chat hoặc Git. Khởi động lại sau khi đổi cấu hình.

```dotenv
# ID lịch đang được bot đọc; chỉ dùng để chặn ghi nhầm vào cùng bảng.
ZALO_SHEETS_ID=<ID_BANG_LICH_HIEN_TAI>
# Bảng mới dành riêng cho thử nghiệm, không dùng chung ID ở trên.
TICKETS_TEST_SHEETS_ID=<ID_BANG_THU_MOI>
TICKETS_TEST_SHEETS_TAB=TicketsTest
TICKETS_TEST_GOOGLE_CREDENTIALS=secrets/tickets-test-service-account.json
# Giữ false cho đến khi đã kiểm tra bảng mới, phân quyền và chế độ ghi độc quyền.
TICKETS_TEST_WRITE_ENABLED=false
TICKETS_TEST_EXCLUSIVE_WRITER=false
```

Chỉ đổi cả hai cờ cuối thành `true` khi đã chủ động cho phép ghi bảng thử, bảo đảm chỉ một tiến trình Tickets hoạt động và không có Apps Script, dịch vụ khác hoặc người sửa tay cùng lúc. Cờ `EXCLUSIVE_WRITER` là **xác nhận vận hành**, không phải công cụ tự khóa Google Sheets.

## Ý nghĩa các cột

| Cột | Ý nghĩa |
|---|---|
| `assignment_id` | Mã duy nhất, cố định của phân công; không tái sử dụng sau khi xóa, không đổi khi chèn hoặc sắp xếp dòng. |
| `member_id` | Mã thành viên đã được quản trị viên xác minh trong Tickets; không mặc định trùng ID Zalo. |
| `member_name` | Tên hiển thị; một `member_id` chỉ có một tên nhất quán trong bảng. |
| `start`, `end` | Ngày và giờ bắt đầu/kết thúc dạng ISO có múi giờ, `end` sau `start`. |
| `course` | Tên lớp/hoạt động. |
| `room` | Phòng cụ thể; dùng `Không áp dụng` nếu ca không cần phòng. |
| `period` | Tên buổi hiển thị, ví dụ Sáng hoặc Chiều. |

Các trường đều bắt buộc. Với một ca có nhiều người phụ trách, tạo nhiều phân công có mã khác nhau nhưng cùng lớp/phòng/thời gian; chỉ hai phân công được hai bên chọn mới được đổi. Không chuyển tự động ô gộp lịch lab thật sang mẫu này và không đoán người từ tên trùng.

## Ghi và đối soát

- Trước khi ghi, dịch vụ đọc lại bảng, tìm **mã phân công** ở vị trí hiện tại và đối chiếu đủ tám trường với nội dung đã được xác nhận. Chèn/sắp xếp dòng trước bước đọc lại không làm lệch đối tượng. Ca thay đổi hoặc bị xóa thì dừng.
- Một `spreadsheets.batchUpdate` chứa hai `updateCells`, chỉ sửa `member_id` và `member_name` (B:C) của hai dòng. Mã, lớp, phòng và thời gian giữ nguyên.
- Theo [tài liệu chính thức Google](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate), các cập nhật trong một batch được áp dụng cùng nhau; điều này **không khóa người cộng tác** và không phải so sánh-ghi có điều kiện. Vì vậy bản thử yêu cầu chỉ một bên ghi. Không sửa/chèn/xóa dòng khi dịch vụ đang xử lý.
- Timeout, lỗi máy chủ hoặc phản hồi ghi không rõ kết quả: không gửi ghi lại. Ticket phải được đối soát bằng cách đọc nguồn trước khi cho phép thao tác tiếp. Chỉ hoàn tất khi đọc lại đúng cả hai phân công; đọc không khớp cần người quản lý kiểm tra, không tự ghi đè để “sửa”.
- Provider không gọi API Google thật trong kiểm thử tự động. Kiểm thử dùng phản hồi mô phỏng; chỉ kết luận kết nối/ghi thật sau khi kiểm tra bảng thử thực tế.

Đặc tả nguồn đọc: [spreadsheets.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get). Thử trên Sheets vẫn cần cấu hình đăng nhập và liên kết thành viên thật của Tickets; tài khoản demo chỉ dành dữ liệu giả cục bộ.
