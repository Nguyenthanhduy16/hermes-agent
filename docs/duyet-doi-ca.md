# Admin duyệt đổi ca

**Luồng sử dụng:** A gửi yêu cầu trong Zalo → B đồng ý → Admin duyệt → bot cập nhật Sheets và đọc lại để kiểm tra. B đồng ý chưa làm thay đổi lịch.

## Thành viên

1. Nhắn riêng bot `/doica`, chọn hai ca và gửi yêu cầu như trước.
2. B nhận thông báo hoặc gửi `/choxacnhan`, rồi dùng `/dongy MÃ_4_SỐ` hay `/tuchoi MÃ_4_SỐ` bot cung cấp.
3. Sau khi B đồng ý, yêu cầu chuyển sang **Chờ Admin duyệt**. Dùng `/yeucau` để xem tiến độ; chỉ coi lịch đã đổi khi trạng thái là **Đã đổi ca**.

Yêu cầu vẫn hết hạn theo thời hạn hiện có, không muộn hơn lúc ca bắt đầu. Hiện chưa có thông báo tự động riêng cho A/B sau quyết định của Admin; thành viên xem kết quả bằng `/yeucau`.

### Tên khác chữ hoa/thường trên lịch

Trong chế độ `lab_live` có đăng ký qua Workload, bot đối chiếu tên không phân biệt chữ hoa/thường với **một dòng duy nhất trong Workload**. Ví dụ `Trần Văn Tài`, `TRẦN VĂN TÀI` và `trần văn tài` trên lịch cùng trỏ về tên chuẩn trong Workload. Thành viên đã liên kết đúng không cần đăng ký lại; mã và liên kết đang lưu được giữ nguyên. Kiểm tra trùng giờ áp dụng cùng cách đối chiếu cho cả Lab Coach và Key Coach.

Bot chỉ bỏ qua khác biệt hoa/thường, không tự bỏ dấu, sửa tên sai hoặc ghép gần đúng. Hai dòng Workload trùng tên, kể cả khác hoa/thường hoặc một dòng thiếu số điện thoại, cần quản trị làm rõ; bot không tự chọn. Tên chưa có trong Workload không được tự cấp quyền. Giữ cách viết tên chuẩn trong Workload ổn định; nếu đổi tên chuẩn, quản trị cần kiểm tra lại đăng ký và liên kết hiện có.

Không cần biến môi trường mới: dùng `REGISTRATION_WORKLOAD_*` đang cấu hình. Workload được đọc lại khi kiểm tra nguồn và trước khi ghi; lỗi đọc hoặc cấu trúc không hợp lệ sẽ chặn đổi ca. Chế độ thử hai người `lab_live_test` và cài đặt không bật đăng ký giữ cách đối chiếu chính xác cũ.

Sau khi cập nhật, gửi `thoát` rồi `/doica` để lấy danh sách mới. Bản xem trước/yêu cầu đang chờ theo cấu hình nguồn cũ cần tạo lại nếu bot báo lịch hoặc nguồn đã thay đổi. Khi đổi thành công, hai ô Lab Coach được ghi theo tên chuẩn Workload; không tự sửa các ô khác.

## Admin

1. Đăng nhập [trang Admin](https://hermes-zalo-bot-vercel-admin.vercel.app/), mở **Duyệt đổi ca**.
2. Danh sách mặc định chỉ hiện yêu cầu chờ duyệt. Có tìm kiếm, lọc trạng thái và chuyển trang; mỗi trang tối đa 20 yêu cầu.
3. Mở yêu cầu để xem hai người, hai ca, lý do và lịch sử xử lý.
4. Chọn **Duyệt và cập nhật lịch**, đọc xác nhận rồi đồng ý; hoặc **Từ chối yêu cầu** và nhập lý do.

Bot kiểm tra lại quyền, liên kết thành viên, ca chưa bắt đầu, lịch mới nhất và trùng giờ trước khi ghi. Nếu dữ liệu thay đổi, tải lại và kiểm tra thông báo; không bấm duyệt liên tục. Hai Admin cùng duyệt chỉ có một thao tác hợp lệ theo phiên bản yêu cầu.

Nếu kết quả ghi chưa xác định, dùng **Đối soát**: thao tác này chỉ đọc lịch để xác định kết quả, không ghi lại. Không sửa trạng thái bằng tay để thử lại một lần ghi chưa rõ kết quả.

## Cấp quyền và bật tính năng

- Trên server bot: giữ chế độ `TICKETS_CHAT_MODE=lab_live`, đặt `TICKETS_REQUIRE_MANAGER=true` rồi triển khai/khởi động lại. Các cờ cho phép ghi lịch và cấu hình giờ ca giữ nguyên theo môi trường đã xác minh.
- Giảng viên mở **Quản lý Admin**, cấp riêng quyền **Duyệt đổi ca** (`tickets`) cho người phụ trách. Vai trò Giảng viên hoặc Admin không tự có quyền này; quyền hội thoại/ghi nhớ không thay đổi.
- Người vận hành có thể cấp quyền bằng công cụ server, không cần đổi mật khẩu:

  ```sh
  python -m hermes_zalo.admin_cli grant-permissions --username TEN_DANG_NHAP --permissions tickets
  ```

  Chạy trong môi trường bot dùng đúng cấu hình và Volume hiện tại. Lệnh giữ quyền cũ, thêm quyền được chỉ định, ghi lịch sử và thu hồi phiên cũ; người được cấp cần đăng nhập lại.

Tài khoản Admin web và tài khoản Zalo là hai hệ danh tính riêng. Chưa có liên kết xác minh giữa chúng, nên không suy ra một Admin là A hoặc B bằng tên hiển thị.

## Cập nhật hệ thống đang chạy

Sao lưu SQLite Tickets và Admin trên Volume trước khi cập nhật. Khi bật duyệt bắt buộc, yêu cầu cũ còn chờ nhưng được tạo theo luồng không cần Admin sẽ bị đánh dấu cần tạo lại và giữ lịch sử; bản xem trước cũ cũng hết hiệu lực. Yêu cầu đã hoàn thành và yêu cầu cần đối soát vẫn giữ nguyên. Không bật lại luồng cũ chỉ để xử lý yêu cầu đã hết hiệu lực.

`lab_live_test` là chế độ thử hai người cũ, yêu cầu `TICKETS_REQUIRE_MANAGER=false`; không dùng chế độ này cho luồng Admin duyệt. Muốn thử toàn bộ luồng an toàn, chạy `./scripts/run.ps1 AdminDemo`: có sẵn một yêu cầu chờ Admin và một yêu cầu chờ B, chỉ dùng dữ liệu giả, không kết nối Sheets/Zalo/model.

Kiểm thử tự động dùng nguồn mô phỏng. Sau khi triển khai, cần một yêu cầu thật do người dùng chủ động gửi, B đồng ý và Admin duyệt để xác nhận toàn bộ luồng Zalo–Admin–Sheets.
