# Checklist dùng thử cho 2–3 người

Trạng thái hiện tại: **người dùng đã xác nhận** bot trả lời tin nhắn riêng và nhớ tên trong cùng cuộc trò chuyện, đã phản hồi “oki rồi” sau bài thử lịch. Đọc lịch Google Sheets thật đã được kiểm tra; chưa xác nhận chạy thật trong nhóm hoặc nhớ tên sau khi khởi động lại. Bản nâng cấp menu/bộ lọc cần thử lại trên Zalo. Xem [bảng lệnh](lenh-zalo.md), [hướng dẫn chạy](../README.md) và [ghi nhận kiểm thử](validation.md).

Chọn người A làm quản trị, người B được cấp quyền thử và tùy chọn người C chưa được cấp quyền. Dùng tài liệu, lịch và issue giả. Kiểm tra lại danh sách người/nhóm trước khi bắt đầu; tài liệu trả trong nhóm phải được phép chia sẻ cho mọi thành viên nhóm. Khi thử nhóm, trả lời một tin của bot rồi gõ lệnh. Đánh dấu từng dòng sau khi quan sát kết quả thật; ghi giờ, người thử và lỗi nếu có.

Máy chạy bot phải bật, có mạng và chỉ chạy một phiên bot. Tất cả giờ hẹn dùng **giờ Việt Nam, UTC+7**. Khi máy tắt hoặc mất mạng, bot không bảo đảm gửi đúng giờ; nhắc quá hạn hoặc kết quả gửi chưa rõ cần kiểm tra bằng `/nhac lichsu`, không mặc định đã gửi thành công.

Các tính năng công việc dùng lệnh ứng dụng rõ ràng; model trò chuyện không được tự gọi công cụ để đọc lịch, đặt nhắc hoặc tạo issue. Tra tài liệu hiện trả trích đoạn theo từ khóa, không tự suy diễn câu trả lời bằng AI.

| Đạt | Bài thử | Kết quả cần quan sát |
| --- | --- | --- |
| ☐ | **1. Tài liệu có đáp án:** `/hoi Thời gian làm việc của nhóm là gì?` | Với bộ [tài liệu mẫu](../examples/knowledge/README.md), có đoạn chứa `08:30–17:30`, tên nguồn `so-tay-mau.md` và số dòng. Có thể thử đủ [10 câu hỏi mẫu](../examples/knowledge/acceptance.json). |
| ☐ | **2. Tài liệu không có thông tin:** `/hoi Ngân sách quảng cáo năm sau là bao nhiêu?` | Báo chưa tìm thấy thông tin phù hợp; không tự đặt số tiền hoặc nguồn. |
| ☐ | **3. Quyền tài liệu:** A và B gửi `/tailieu`, hỏi một tài liệu chỉ cấp cho A; thử lại trong nhóm chưa được cấp quyền tài liệu | B không nhận nội dung/tên tài liệu riêng của A. Quyền nhắn riêng không tự cấp quyền nhóm; chỉ trả tài liệu khi đúng quyền người và nhóm. |
| ☐ | **4. Tạo, xem, nhận nhắc:** `/nhac sau 1 phut \| Kiểm tra bot`, rồi `/nhac xem` | Có mã nhắc và giờ hẹn; nhận một tin tại chính cuộc trò chuyện tạo nhắc. Ghi giờ nhận thực tế để đánh giá độ trễ. |
| ☐ | **5. Hủy nhắc:** tạo nhắc sau 2 phút, gửi `/nhac huy MA`, xem lại danh sách và đợi qua giờ hẹn | Báo đã hủy; không gửi nhắc đó. Thay `MA` bằng mã thật bot vừa trả. |
| ☐ | **6. Nhắc sau khởi động lại:** tạo nhắc sau 3 phút; tắt bot bằng `Ctrl+C`, bật lại bằng `Start` trước giờ hẹn | `/nhac xem` còn bản nhắc; đến giờ chỉ gửi một lần. Bài này kiểm tra lưu nhắc, không chứng minh model còn nhớ lịch sử chat. |
| ☐ | **7. Nhắc đúng người/nhóm:** A tạo nhắc trong nhóm thử; B xem danh sách của mình/hủy bằng mã của A; C chưa có quyền gửi lệnh | Danh sách của B không liệt kê nhắc của A; B không hủy được nhắc của A; C không được tạo nhắc. Nhắc chỉ gửi về nhóm đã tạo, mọi thành viên nhóm có thể đọc tin đó. Thử thu hồi quyền A: dừng bot, sửa quyền trong `.env`, bật lại trước giờ hẹn; bot không gửi nhắc đã mất quyền. |
| ☐ | **8. Lịch có quyền:** đặt một dòng lịch giả đúng ngày, gửi `/lich YYYY-MM-DD` | Hiện đúng giờ Việt Nam, tiêu đề, nơi họp và nguồn tab Sheets. Thay ngày mẫu bằng ngày của dòng thử; không đưa ngày mơ hồ như `06/10`. |
| ☐ | **9. Lịch không có quyền:** thêm một dòng riêng chỉ cho A; B tra cùng ngày và thử trong nhóm không có quyền dòng đó | Không lộ tiêu đề, địa điểm hoặc giờ của dòng riêng. Người/nhóm chưa có quyền Sheets không được truy vấn. |
| ☐ | **10. Bản nháp issue:** `/issue tao Lỗi thử nghiệm \| Mô tả để kiểm tra bot` | Hiện tiêu đề, mô tả, bảng/tab đích, mã và lệnh xác nhận; mở Sheets kiểm tra **chưa thêm dòng**. |
| ☐ | **11. Người khác xác nhận:** B gửi `/issue xacnhan MA` với mã bản nháp của A; A thử xác nhận mã đó trong cuộc trò chuyện khác | Cả hai đều bị từ chối, không thêm dòng. Bản nháp chỉ có hiệu lực 10 phút. |
| ☐ | **12. Chính chủ xác nhận:** A gửi `/issue xacnhan MA` tại nơi tạo bản nháp, rồi gửi lại cùng lệnh | Sheets có đúng một dòng với mã đó và nội dung đã xem. Lần xác nhận thứ hai không tạo thêm. |
| ☐ | **13. Hủy issue:** tạo bản nháp mới, gửi `/issue huy MA`, rồi thử xác nhận | Bản nháp đã hủy không được ghi vào Sheets. |
| ☐ | **14. Issue gặp timeout/chưa rõ kết quả:** nếu bot báo trạng thái này trong đợt thử | Tìm mã issue trong cột `id` trên Sheets trước khi làm tiếp; bot không tự ghi lại. Không tạo bản nháp thay thế khi chưa kiểm tra. Nếu chưa xảy ra lỗi thật, ghi “chưa thử thực tế”, không đánh dấu đạt từ kiểm thử mô phỏng. |
| ☐ | **15. Góp ý và báo cáo:** `/phanhoi Thử nghiệm: ghi rõ lỗi và giờ xảy ra`, sau đó quản trị chạy `Report` | Bot xác nhận nhận góp ý; báo cáo có số lượt/kết quả và số góp ý, không đưa nội dung góp ý vào báo cáo tổng hợp. Đối chiếu số liệu token/chi phí; phần thiếu phải là **chưa xác định**, không coi là 0 đồng. |

Các lệnh trên PowerShell, chạy từ thư mục repo:

```powershell
.\scripts\run.ps1 Start
```

Mở cửa sổ PowerShell khác khi bot đang chạy để tạo báo cáo:

```powershell
.\scripts\run.ps1 Report
```

Báo cáo lưu tại `data/reports/pilot-latest.txt`, là số liệu tích lũy, không mặc định chỉ riêng ngày thử. Phân biệt chi phí thực tế, ước tính và chưa xác định; đối chiếu trang sử dụng của nhà cung cấp AI khi cần. Không đưa tài liệu nội bộ, báo cáo riêng, khóa truy cập hoặc nội dung góp ý nhạy cảm lên Git.

Cuối buổi ghi số bài đạt/chưa đạt/chưa thử, lỗi còn mở, thời gian phản hồi và phần chi phí chưa đủ dữ liệu. Chỉ kết luận tính năng hoạt động trên Zalo/Sheets sau khi quan sát được cả tin nhắn và kết quả thực tế tương ứng.
