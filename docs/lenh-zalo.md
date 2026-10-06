# Các lệnh dùng trong Zalo

Gửi `/menu` để xem hướng dẫn ngay trong bot. Có thể sao chép các ví dụ bên dưới rồi thay nội dung. Trong nhóm, trả lời một tin của bot rồi nhập lệnh. Thời gian luôn theo **giờ Việt Nam**.

**Dùng trong nhóm:** xem [hướng dẫn thành viên và quản trị](dung-trong-nhom.md). Đăng ký tên một lần bằng `/dangky Họ tên đầy đủ`, sau đó hỏi `Ngày mai mình có lịch gì?` hoặc `/lichcuatoi mai`. Có thể hỏi trực tiếp `Xin nghỉ phép cần báo ai?` để tra tài liệu đã chia sẻ.

## Bắt đầu

| Bạn muốn | Nhắn cho bot |
| --- | --- |
| Xem menu | `/menu` hoặc `menu` |
| Hướng dẫn tra lịch | `/help lich` |
| Hướng dẫn nhắc việc | `/help nhac` |
| Hướng dẫn tài liệu | `/help tailieu` |
| Xem cách gửi góp ý | `/help phanhoi` |

`/trogiup` và `/help` vẫn dùng được. Bot báo rõ tính năng chưa kết nối, chưa bật hoặc chưa có quyền. Lệnh gõ sai như `/lichh` được hướng dẫn sửa.

## Tra lịch

| Bạn muốn | Ví dụ |
| --- | --- |
| Lịch hôm nay | `/lich homnay` hoặc `lịch hôm nay` |
| Lịch ngày mai | `/lich mai` hoặc `xem lịch ngày mai` |
| Lịch ngày cụ thể | `/lich 06/10/2026` hoặc `/lich 2026-10-06` |
| Viết ngày ngắn | `/lich 06/10` — hiểu là 6 tháng 10 **năm hiện tại** |
| Cả tuần hiện tại | `/lich tuan nay` — thứ Hai đến Chủ nhật |
| Tuần kế tiếp | `/lich tuan sau` |
| Khoảng ngày, tối đa 7 ngày | `/lich 2026-10-05..2026-10-11` |

Các mục tuần, bộ lọc và chia trang áp dụng cho bảng **Lịch lab**. Bảng mẫu `standard` vẫn tra theo từng ngày.

Thêm bộ lọc sau dấu `|`:

```text
/lich homnay | coach=Nguyễn Văn An
/lich homnay | khoa=Track 1
/lich homnay | phong=H201
/lich homnay | buoi=sáng
/lich tuan nay | khoa=Track 1 | buoi=chiều
```

Tên người ở ví dụ là tên minh họa; thay bằng tên trong bảng. `coach` tìm cả **Lab Coach và Key Coach**, không phân biệt hoa/thường hay dấu tiếng Việt. Có thể nhập một phần tên theo từ, ví dụ `Văn An`; nếu nhiều người trùng tên, nhập tên đầy đủ. Bot dùng `/dangky` để lưu tên bạn tự khai tại từng chat; `/hoso` xem tên, `/hoso xoa` xóa tên. Bộ lọc và việc đăng ký không cấp thêm quyền đọc dữ liệu.

Bot trả tối đa **5 mục mỗi trang**, có số trang/tổng số mục và link tới dòng gốc. Nếu còn kết quả, bot gửi sẵn một lệnh dưới chữ **Xem tiếp**; sao chép nguyên lệnh đó để giữ ngày và bộ lọc. Ví dụ:

```text
/lich 2026-10-06 | khoa=Track 1 | trang=2
```

Mỗi lần tra đọc lại bảng: nếu có người sửa lịch giữa hai lần xem trang, kết quả có thể thay đổi. Ngày thiếu dữ liệu được báo rõ; bot không coi đó là chắc chắn rảnh. Ô trống được báo “Chưa ghi”, lịch cùng ngày ở nhiều tab được giữ riêng theo nguồn. Trạng thái như “Rảnh” giữ nguyên theo bảng, không tự hiểu là đã xác nhận tham gia.

## Nhắc việc

```text
/nhac sau 10 phut | Gửi báo cáo
/nhac mai 09:00 | Họp nhóm
/nhac 07/10/2026 09:00 | Kiểm tra lịch
```

Thay ngày cụ thể bằng ngày tương lai. Cũng có thể nhắn **`nhắc mình sau 10 phút: Gửi báo cáo`**. Bot gửi lại nội dung, ngày giờ, nơi nhận và mã nhắc để bạn kiểm tra. Thời gian đã qua sẽ báo lỗi, không tự chuyển sang ngày khác.

- `/nhac xem`: các nhắc **đang chờ/đang gửi**, của chính bạn trong chat hiện tại.
- `/nhac lichsu`: các nhắc gần đây, gồm đã gửi, hủy hoặc lỗi; hiển thị tối đa 30 mục.
- `/nhac huy MÃ_NHẮC`: thay `MÃ_NHẮC` bằng mã nhận từ bot. Chỉ hủy được nhắc chưa bắt đầu gửi.

Dấu `|` ngăn thời gian với nội dung. Nhắc việc phải có đủ giờ cụ thể; ngày dạng ngắn `07/10` chỉ dùng cho tra lịch, nhắc việc cần cả năm. Máy chạy bot cần bật và có mạng; nhắc được gửi vào đúng chat đã tạo.

## Tài liệu, góp ý và issue

```text
/tailieu
/hoi Giờ làm việc của nhóm là khi nào?
/phanhoi Lịch ngày 06/10 hiển thị thiếu phòng
```

`hỏi tài liệu Giờ làm việc của nhóm là khi nào?` cũng dùng được. Tài liệu hiện là các file `.md`/`.txt` đã cấp quyền; lịch Google Sheets dùng `/lich`. `/gopy` là tên khác của `/phanhoi`.

**Issue đang tắt trên bảng lịch hiện tại.** Khi người quản lý bật tính năng và cấp quyền, `/help issue` sẽ hướng dẫn tạo bản nháp → kiểm tra → `/issue xacnhan MÃ_BẢN_NHÁP`. “Đồng ý” không tự tạo issue.

Các lệnh và mẫu câu được liệt kê ở đây được xử lý trực tiếp. Những câu trò chuyện khác vẫn qua Hermes; không phải mọi câu tiếng Việt đều là lệnh thực hiện công việc.
