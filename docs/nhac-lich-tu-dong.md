# Nhắc lịch học tự động

Thành viên chỉ cần đăng ký thành công bằng tin nhắn riêng `/dangky SỐ_ĐIỆN_THOẠI`, dùng số hiện tại trong Workload. Khi tính năng đã bật, bot tự nhắn vào chat riêng đã liên kết:

| Lúc gửi — giờ Việt Nam | Nội dung |
| --- | --- |
| 20:00 tối hôm trước | Một bản tổng hợp các ca ngày mai để chuẩn bị |
| 30 phút trước từng ca | Ca sắp bắt đầu, để chuẩn bị check-in |

Một người có một ca sẽ nhận hai lần nhắc. Nếu có nhiều ca trong ngày: một tin tổng hợp và một tin trước **mỗi ca**. Không có lịch thì không gửi. Đây là tin nhắc, **chưa có chức năng ghi nhận điểm danh**.

Bot đọc cột **Lab Coach** trong các tab lịch đã cấp quyền, dùng giờ buổi đã cấu hình. Chỉ thành viên được duyệt, liên kết đúng chat riêng và xác định duy nhất trên lịch mới nhận tin. Không dùng ID nhóm làm nơi nhận; người trùng tên chưa có ánh xạ phân biệt sẽ không được đoán ghép. Người chưa đăng ký hoặc đã bị khóa/thu hồi quyền không nhận lịch nội bộ.

## Người vận hành bật như thế nào?

Giữ cấu hình lịch và đăng ký hiện tại; kiểm tra đúng nguồn, ánh xạ người và giờ ca trước khi bật. Trong Railway Variables hoặc `.env` trên máy chạy bot:

```dotenv
ZALO_AUTO_REMINDERS_ENABLED=true
ZALO_AUTO_REMINDERS_EVENING_TIME=20:00
ZALO_AUTO_REMINDERS_LEAD_MINUTES=30
ZALO_AUTO_REMINDERS_POLL_SECONDS=60
```

Giờ ca lấy từ `TICKETS_LAB_PERIODS_JSON` đang dùng: Sáng 09:00–13:00, Chiều 14:00–18:00, Tối 17:30–20:30. Không tự đoán giờ khi lịch/cấu hình thiếu hoặc không hợp lệ. Tính năng chỉ đọc lịch, không cần bật quyền ghi Sheets để gửi nhắc. Mẫu cấu hình mặc định tắt để tránh tự gửi khi mới cài.

Sau khi cập nhật, triển khai/khởi động lại **một** tiến trình bot; giữ bản Windows tắt nếu Railway đang chạy. Mở `/thongbao` để xem chính sách giờ nhắc đã bật. Để ngừng gửi mới, đặt `ZALO_AUTO_REMINDERS_ENABLED=false` rồi khởi động lại; giữ nguyên dữ liệu trạng thái trên Volume.

## Lịch thay đổi và gián đoạn

- Bot đọc lại lịch trước khi gửi; các thành viên trong cùng đợt dùng chung bản đọc không quá 5 giây. Khi Admin cập nhật đổi ca, bot tạm ngừng nhắc và bỏ bản đọc cũ. Tin gần giờ học gửi theo người phụ trách đã cập nhật trên Sheets. Thay đổi thủ công trực tiếp trên Sheets chỉ được thấy ở lần đọc tiếp theo.
- Bản tổng hợp tối đã gửi không thể thu hồi. Nếu lịch đổi sau đó, dùng `/lichcuatoi mai` để kiểm tra; phiên bản này chưa gửi riêng thông báo cho mọi thay đổi lịch.
- Trạng thái gửi được lưu bền vững trên Volume để không gửi lại cùng lượt sau khởi động lại. Không xóa dữ liệu này khi triển khai.
- Nếu bot tạm mất kết nối: bản tối chỉ được gửi bù trước 23:00 cùng tối; tin gần ca chỉ được gửi trước giờ bắt đầu. Quá khoảng này thì bỏ qua, không dồn nhắc cũ.
- Lỗi đọc lịch trước khi bắt đầu gửi được thử ở lượt kiểm tra sau; lỗi Zalo 429 được chờ và thử lại có giới hạn. Lượt gửi đã ghi nhận thất bại, bị chặn hoặc chưa rõ kết quả không tự phát lại. Vì vậy không bảo đảm tin nào cũng đến khi Zalo/mạng gặp sự cố.
- Nhịp kiểm tra mặc định 60 giây; giờ nhận thực tế có thể chậm thêm do đọc Sheets hoặc Zalo.

## Menu và kiểm thử

Menu chính không còn mục **Nhắc lịch: `/nhac`**. `/thongbao` giới thiệu nhắc lịch tự động khi được bật. Các lệnh `/nhac`, `/nhac xem`, `/nhac lichsu`, `/nhac huy MÃ` vẫn quản lý các nhắc **tự đặt** hiện có; không xóa hay chuyển chúng thành nhắc tự động.

Kiểm thử mô phỏng dùng lịch, danh tính và bộ gửi giả; không sửa Sheets, gọi model hoặc gửi Zalo thật. Để xác nhận vận hành, chọn một thành viên đã đăng ký có ca tương lai thật, kiểm tra quyền và giờ, rồi đối chiếu hai tin nhắc thực nhận. Chỉ kết luận gửi thật hoạt động sau lần đối chiếu này.
