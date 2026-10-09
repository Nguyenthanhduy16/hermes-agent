# Phụ lục: giao diện web thử nghiệm cục bộ

**Luồng sử dụng chính đã chuyển sang nhắn riêng với bot.** Xem [đổi ca bằng tin nhắn Zalo](tickets-chat.md) để cấu hình, cấp quyền và sử dụng `/tickets`, `/doica`. Không cần chạy web, mở URL công khai hay tạo Mini App cho luồng đó.

Tài liệu này chỉ giữ cách thử giao diện web cũ trên máy quản trị. Đây là công cụ thử nghiệm riêng, không phải bước bắt buộc của bot. Quy trình mặc định vẫn là **A tạo yêu cầu → B đồng ý → quản lý duyệt → cập nhật lịch**. B hoặc quản lý có thể từ chối; yêu cầu chưa đủ xác nhận không làm thay đổi lịch.

Hiện có hai chế độ chạy trên máy: **demo** dùng dữ liệu mẫu, và **sheets_test** dùng một bảng Google Sheets thử nghiệm riêng. Chưa triển khai công khai, chưa có Zalo Mini App, đăng nhập Zalo hoặc thông báo tự động qua Zalo.

**Không chạy web và bot cùng dữ liệu Tickets.** Dừng tiến trình đang dùng cơ sở dữ liệu trước khi thử giao diện còn lại. Tài khoản tạo bằng `add-user --chat-only` được dùng qua liên kết Zalo, không có mật khẩu được cấp cho người dùng để đăng nhập web; không tạo lại tài khoản đang dùng chỉ để xem thử giao diện.

## 1. Tài khoản và quyền

- Quản trị xác minh người dùng rồi tạo tài khoản đăng nhập riêng. Không coi tên từ `/dangky`, tên hiển thị Zalo hay một mã thành viên tự nhập là bằng chứng danh tính.
- Mỗi tài khoản gắn với một `member_id` ổn định trong lịch. Người dùng không tự nhận mã của người khác.
- Chỉ người gửi A, người nhận B và tài khoản vai trò `manager` được xem yêu cầu; nút xác nhận/phê duyệt chỉ hiện khi đúng quyền và trạng thái.
- Mặc định `TICKETS_REQUIRE_MANAGER=true`. Nếu quản trị chủ động đặt `false`, sự đồng ý của B là bước xác nhận cuối; chỉ đổi chính sách khi đã thống nhất với nhóm.

Các tài khoản có mật khẩu áp dụng cho chế độ `sheets_test`. Bản `demo` có nút chọn tài khoản mẫu, không dùng mật khẩu, chỉ chạy trên máy quản trị. Mật khẩu thật được nhập tại lời nhắc trên máy, tối thiểu 12 ký tự; không truyền trong câu lệnh, chat hoặc Git. Xem cú pháp tài khoản:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --help
```

Lệnh tạo tài khoản dùng `--mode sheets_test` và các trường `--username`, `--name`, `--member-id`, `--role member|manager`. Thành viên phải có mã được đối chiếu với bảng thử; quản lý không cần `--member-id`. Không có trang quản trị tạo tài khoản qua web.

## 2. Chạy demo trước

Tại thư mục dự án, cài môi trường nếu chưa có rồi bật trang:

```powershell
.\scripts\run.ps1 Setup
.\scripts\run.ps1 TicketsDemo
```

Mở **http://127.0.0.1:8765** trên chính máy chạy lệnh. Địa chỉ này dành cho xem thử cục bộ; không gửi cho người dùng Zalo trên máy khác. Giữ cửa sổ chạy mở và dừng bằng `Ctrl+C`. Không chạy demo và sheets_test cùng cổng cùng lúc.

Lệnh tương đương khi cần đổi cổng:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli serve --mode demo --port 8765
```

Tài khoản, yêu cầu và trạng thái demo lưu riêng tại `.runtime/tickets/demo.sqlite3`. Chế độ này không ghi lịch Google Sheets và không gọi Zalo hay model AI.

**Bài thử:** trang đã có các nút tài khoản mẫu **An (demo-a)**, **Bình (demo-b)**, **Chi (demo-c)** và **Quản lý (demo-manager)**. Mã thành viên tương ứng là `coach-a`, `coach-b`, `coach-c`; không dùng các mã mẫu này để xác minh người thật. Dùng các hồ sơ trình duyệt/cửa sổ riêng, hoặc đăng xuất trước khi đổi tài khoản:

1. Chọn An (A), chọn ca của mình và ca của Bình (B), kiểm tra thông tin rồi gửi yêu cầu.
2. Chọn Bình (B), mở yêu cầu nhận được và đồng ý. Với cấu hình mặc định, yêu cầu vẫn chờ quản lý.
3. Quản lý đăng nhập, kiểm tra hai ca rồi phê duyệt.
4. A và B mở lại yêu cầu/lịch để kiểm tra người phụ trách đã đổi đúng; xem lịch sử thao tác.
5. Tạo yêu cầu khác để thử từ chối và hủy khi còn được phép. Thử tài khoản không liên quan để kiểm tra không xem được yêu cầu của A/B.

Đây là thử dữ liệu demo; kết quả không chứng minh đã ghi Google Sheets thật.

## 3. Chuẩn bị bảng Google Sheets thử riêng

**Không dùng bảng lịch Lab đang hoạt động.** Tạo một spreadsheet riêng, có ID khác `ZALO_SHEETS_ID`, rồi chuẩn hóa các ca cần thử thành tab `TicketsTest`. Một bản sao nguyên xi có ô gộp chưa đủ điều kiện dùng; không tự chuyển cấu trúc hoặc sửa ô gộp của lịch Lab.

Hàng 1 phải có đúng tám cột A:H theo thứ tự:

| assignment_id | member_id | member_name | start | end | course | room | period |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shift-a-001 | member-a | Coach A | 2026-10-20T08:00:00+07:00 | 2026-10-20T10:00:00+07:00 | Track 1 | H201 | sáng |
| shift-b-001 | member-b | Coach B | 2026-10-21T08:00:00+07:00 | 2026-10-21T10:00:00+07:00 | Track 1 | H202 | sáng |

Đây là dữ liệu minh họa, cần chọn ngày tương lai và tên được phép dùng. Định dạng **văn bản thuần** cho tám cột trước khi dán, nhất là `start` và `end`; dùng ngày giờ ISO có múi giờ như mẫu. Không dùng công thức, ô gộp, ngày dạng số hay mã ca trùng. `assignment_id` nhận diện một ca và `member_id` nhận diện một người: giữ nguyên các mã khi sắp xếp lại dòng; cùng một người dùng một mã và tên nhất quán. Tối đa 1.000 ca, với tab tối đa 1.001 dòng kể cả tiêu đề.

Quản trị tạo tài khoản theo `member_id` trong bảng này. Dữ liệu và tài khoản của chế độ thử lưu tại `.runtime/tickets/sheets_test.sqlite3`, tách khỏi demo.

## 4. Cấu hình và bật chế độ Sheets thử

Cài hỗ trợ Google nếu chưa có:

```powershell
.\scripts\run.ps1 SetupSheets
```

Chia sẻ **bảng thử riêng** cho service account được dùng cho Tickets. Giữ file khóa trong `secrets/`; nhập các giá trị sau vào `.env` trên máy, không gửi khóa vào chat:

```dotenv
TICKETS_REQUIRE_MANAGER=true
TICKETS_TEST_SHEETS_ID=
TICKETS_TEST_SHEETS_TAB=TicketsTest
TICKETS_TEST_GOOGLE_CREDENTIALS=secrets/tickets-test-service-account.json
TICKETS_TEST_WRITE_ENABLED=false
TICKETS_TEST_EXCLUSIVE_WRITER=false
```

Điền ID bảng thử thật vào trường còn trống. Giữ `ZALO_SHEETS_ID` là ID lịch hiện tại để chương trình kiểm tra hai bảng khác nhau. Khi `TICKETS_TEST_WRITE_ENABLED=false`, chương trình không được ghi đổi ca vào bảng.

Sau khi cấu hình kết nối đọc và xác minh người nhận tài khoản, tạo tài khoản cho dữ liệu mẫu ở trên. Đổi tên/mã nếu bảng thử dùng giá trị khác:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --mode sheets_test --username coach-a --name "Coach A" --member-id member-a --role member
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --mode sheets_test --username coach-b --name "Coach B" --member-id member-b --role member
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --mode sheets_test --username manager --name "Quan ly" --role manager
```

Nhập mật khẩu riêng khi được nhắc. Thành viên đăng nhập bằng tài khoản này; không có nút chọn người như demo. Khi cần thu hồi tài khoản:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli revoke-user --mode sheets_test --username coach-a
```

**Chỉ bật ghi khi đã dành bảng riêng cho dịch vụ này:** Google Sheets không hỗ trợ thao tác có điều kiện kiểu “chỉ ghi nếu các ô vẫn bằng giá trị đã đọc” để chống một người khác sửa trực tiếp đúng lúc dịch vụ đang ghi. Vì vậy, `TICKETS_TEST_EXCLUSIVE_WRITER=true` là cam kết vận hành: chỉ một tiến trình Tickets được ghi bảng thử; không sửa tay, dùng công thức hay để công cụ/automation khác ghi đồng thời. Đây không phải khóa kỹ thuật ngăn chỉnh sửa từ giao diện Google Sheets.

Khi đã bảo đảm điều kiện đó, chủ động đặt cả hai biến sau là `true` rồi khởi động lại:

```dotenv
TICKETS_TEST_WRITE_ENABLED=true
TICKETS_TEST_EXCLUSIVE_WRITER=true
```

```powershell
.\scripts\run.ps1 TicketsTest
```

Chương trình chỉ sửa người được phân công trong hai dòng ca đã xác nhận; mã ca, giờ, khóa, phòng và buổi giữ nguyên. Nếu lịch thay đổi, ca bị trùng hoặc kết quả ghi chưa rõ, dừng xác nhận và đối soát bảng/lịch sử; không bấm lặp để cố ghi lại. Thực hiện lại bài thử A → B → quản lý và kiểm tra trên **bảng thử**, trước khi kết luận kết nối Google hoạt động.

Khi yêu cầu báo **cần đối soát**, hai ca tiếp tục được giữ cho yêu cầu đó. Quản lý dùng thao tác kiểm tra lại kết quả: thao tác này chỉ đọc bảng, không gửi lại lệnh đổi ca. Nếu cả hai phân công đã đổi đúng thì yêu cầu hoàn tất; nếu chưa khớp, cần kiểm tra bảng thử và lịch sử cùng quản trị trước khi xử lý tiếp. Bản hiện tại không có nút tự bỏ giữ ca hoặc tự ghi lại khi kết quả chưa rõ.

## 5. Luồng Zalo hiện tại

Mục **Đổi ca** trên `/menu` dùng `/doica` để chọn ca ngay trong chat riêng; `/tickets` vẫn mở menu đổi ca hoặc tiếp tục bước đang nhập, không trả link website. Danh sách chỉ có ca chưa bắt đầu và hỗ trợ `/doica trang 2` hoặc `tiếp`/`trước`. `TICKETS_PUBLIC_URL` không cần cho luồng chat. Để dùng trên Zalo, làm theo [hướng dẫn Tickets chat](tickets-chat.md), xác minh và liên kết người/chat/bot rồi chọn đúng chế độ dữ liệu. Luồng `lab_live` với cột D của lịch hiện tại không dùng giao diện web thử nghiệm trong phụ lục này.

Quyền đọc lịch trong nhóm hoặc tên `/dangky` không cấp quyền đổi ca. Khi gửi lệnh Tickets trong nhóm, bot hướng dẫn nhắn riêng. Không gửi địa chỉ localhost của giao diện này cho thành viên trên máy khác.

## 6. Kiểm tra và giới hạn

```powershell
.\scripts\run.ps1 Test
```

Kiểm thử tự động dùng dữ liệu cục bộ và Google API mô phỏng. Chỉ xác nhận kết nối ghi Google Sheets hoặc luồng trên Zalo sau khi thực hiện bài thử thật. Hiện chưa tự gửi thông báo khi B nhận yêu cầu hoặc quản lý duyệt; người dùng xem trạng thái trên trang. Bản thử trên máy chỉ lắng nghe `127.0.0.1`, chưa phải dịch vụ triển khai cho cả nhóm.
