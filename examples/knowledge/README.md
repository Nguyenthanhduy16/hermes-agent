# Bộ tài liệu thử, không phải dữ liệu của đơn vị

`so-tay-mau.md` và `acceptance.json` cung cấp 10 câu hỏi có đáp án và 3 câu hỏi không có thông tin. Các ID `u_demo`, `g_demo` là giả, không cấp quyền cho người dùng Zalo thật.

Để dùng tài liệu thật, chép tệp `.md` hoặc `.txt` UTF-8 vào kho tài liệu riêng đã được loại khỏi Git. Không chép dữ liệu nội bộ vào thư mục ví dụ này. Tạo `manifest.json` theo mẫu, thay ID bằng ID đã lấy từ Zalo và kiểm tra trước khi chạy bot.

Mỗi tài liệu phải có đủ `id`, `title`, `path`, `users`, `groups`. Có thể thêm `approved_members` với giá trị JSON `true` hoặc `false`; mặc định là `false` để giữ nguyên quyền của tài liệu cũ. Đường dẫn `path` tính từ thư mục kho, dùng `/`, không dùng đường dẫn tuyệt đối, `..`, liên kết tượng trưng hay junction. Tệp ẩn, tệp bí mật và thư mục log/runtime không được đưa vào kho. Chỉ các tệp được liệt kê trong manifest mới được đọc.

- Nhắn riêng: người hỏi nằm trong `users`, hoặc đã được quản trị duyệt đăng ký và tài liệu có `approved_members: true`. Thành viên chờ duyệt/đã thu hồi không nhận quyền từ cờ này.
- Nhắn nhóm: nhóm phải nằm trong `groups`; nếu `users` không rỗng thì người hỏi cũng phải nằm trong `users`.
- Để chia sẻ một tài liệu cho mọi thành viên đã duyệt qua tin nhắn riêng, đặt `"approved_members": true`, `"users": []`, `"groups": []`. Chỉ bật với tài liệu dùng chung, không bật cho tài liệu riêng. Không cần thêm từng ID người dùng.
- Nếu không bật `approved_members`, `users: []` chỉ cấp quyền cho các nhóm đã ghi rõ trong `groups`; không cấp quyền nhắn riêng. Không hỗ trợ `*`.
- Quyền thành viên đã duyệt hiện chỉ được dùng trong tin nhắn riêng; không tự mở tài liệu cho nhóm. Quyền `groups` và `users` trong nhóm vẫn được giữ như trên.
- Quyền sử dụng bot vẫn được kiểm tra trước quyền tài liệu. Câu trả lời trong nhóm có thể được mọi thành viên nhóm đọc: chỉ đưa vào nhóm tài liệu được phép chia sẻ với cả nhóm.

Tra cứu hiện tại so khớp từ khóa có/không dấu tiếng Việt, trả lại đoạn nguyên văn và tên tệp cùng số dòng. Đây là tìm kiếm trích đoạn, chưa phải hỏi đáp ngữ nghĩa bằng AI. Hãy viết mỗi đoạn ngắn dưới một tiêu đề rõ ràng; dòng dài hơn 1.200 ký tự không được trích để tránh cắt sai ý. Không tìm thấy từ khóa đủ liên quan thì bot báo chưa tìm thấy; điều này không chứng minh toàn bộ tài liệu không chứa thông tin dưới cách diễn đạt khác.

Giới hạn: 100 tệp trong manifest, manifest 128 KiB, mỗi tài liệu 256 KiB, tổng tài liệu được phép đọc mỗi lượt 4 MiB và 4.000 đoạn. Chưa hỗ trợ PDF, Word, ảnh hoặc liên kết web. Quyền và nội dung được đọc lại mỗi lượt, không có bộ nhớ đệm quyền cũ. Nội dung được trả trực tiếp dưới dạng trích dẫn; câu lệnh nằm trong tài liệu không được thực thi và không được gửi cho model.
