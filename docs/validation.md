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
# Kiểm tra bổ sung: đọc lịch Lab có ô gộp — 06/10/2026

- Người dùng chọn đọc cả ba tab Lịch lab hiện có. Đã cấu hình danh sách tab rõ ràng trên máy; issue tắt, OAuth chỉ đọc, quyền nhóm Sheets vẫn trống.
- `SheetsCheck` với Google thật: thành công, 3 tab / 324 mục lịch tại thời điểm kiểm tra. Số mục có thể thay đổi khi người dùng cập nhật bảng.
- Lệnh `/lich` chạy qua `WorkService` với Google thật: các ngày 06/10, 24/09 và 10/09/2026 đều có phản hồi kèm tab, dòng và link nguồn đúng tab mong đợi. Kiểm tra này không gửi tin Zalo, không gọi AI, không ghi Google Sheets.
- Bộ cục bộ sau khi bổ sung parser: 150 bài, 144 đạt, 6 bỏ qua (5 bài Hermes chạy riêng và 1 bài symlink cần quyền hệ điều hành). Sau đó thêm bài OAuth chỉ đọc và chạy lại 4/4 bài xác thực thành công.
- 15 bài mới cho lịch Lab bao gồm ô gộp, ô trống thật, dòng Coach thiếu STT, ngày trùng ở nhiều tab, ngày thiếu dữ liệu, giới hạn phản hồi, cấu hình sai, phân quyền trước HTTP, dữ liệu nguồn thay đổi, lỗi mạng và chặn issue. Thêm 1 bài xác nhận OAuth chỉ đọc.
- `TestHermes`: 5/5 đạt với Hermes thật, Zalo/AI mô phỏng. Plugin đã được cập nhật vào runtime. `git diff --check` đạt.
- Giới hạn: chưa kiểm tra nhận/gửi lệnh lịch qua Zalo thật; không tự ánh xạ tên Coach với người dùng Zalo; người được cấp quyền Sheets xem lịch của toàn bộ các tab đã chọn. Ô nguồn thiếu tên khóa được báo thiếu, không suy diễn.
- Dữ liệu kiểm tra riêng nằm trong `data/sheets-inspection/`, không đưa vào Git; fixtures của kiểm thử chỉ chứa dữ liệu giả.
# Kiểm tra bổ sung: lệnh và hướng dẫn dễ dùng — 06/10/2026

- Menu theo tính năng và trạng thái cấu hình/quyền; mẫu câu tiếng Việt xác định trước; lệnh sai không đi vào model. Lệnh cũ tiếp tục dùng được.
- Lịch: ngày Việt Nam, ngày/tháng có hoặc không năm, tuần, khoảng tối đa 7 ngày; bộ lọc Coach/khóa/phòng/buổi; phân trang 5 mục, lệnh xem tiếp giữ nguyên ngày/bộ lọc. Tên Coach tìm theo từ và bỏ dấu, không tìm trong trạng thái.
- Nhắc: thêm `mai HH:MM`, `DD/MM/YYYY HH:MM`, danh sách đang chờ và lịch sử riêng; không tự chuyển giờ đã qua sang ngày khác. Issue vẫn cần xác nhận chính xác và vẫn tắt trong cấu hình hiện tại.
- Kiểm thử: 166 bài cục bộ, 160 đạt, 6 bỏ qua (5 Hermes chạy riêng + 1 symlink cần quyền). Trong đó 15 bài mới kiểm tra lệnh/giao diện, giới hạn phản hồi, phân quyền, sai ngày/bộ lọc, dữ liệu thiếu, không gọi model và không tạo nhắc khi thiếu thông tin. `TestHermes` 5/5 đạt.
- Google Sheets thật, chỉ đọc: mẫu câu `xem lịch 06/10/2026 | phòng=H201`, khoảng 05–11/10 trang 1 và trang 2 đều qua `WorkService` thành công. Không gửi tin Zalo, không gọi AI, không ghi bảng trong kiểm tra này.
- Plugin đã cập nhật trong Hermes. Người dùng cần khởi động lại và thử `/menu`, `/help lich`, `lịch hôm nay` trên Zalo để xác nhận bản nâng cấp mới. Mỗi trang đọc lại dữ liệu nên việc sửa bảng giữa các lần xem có thể làm đổi kết quả.
# Kiểm tra bổ sung: thành viên nhóm và câu hỏi tự nhiên — 06/10/2026

- Người dùng chọn mọi thành viên trong nhóm được cho phép được hỏi lịch/quy định, đăng ký tên để hỏi lịch cá nhân. Đã bật `ZALO_GROUP_ACCESS=members` cho nhóm hiện có trong danh sách và cấp nhóm đó quyền lịch Sheets; không mở quyền nhóm lạ, nhắn riêng hoặc tác vụ ghi cho người mới.
- Tên Coach tự khai được kiểm tra với Sheets, lưu theo user + chat + loại chat. `/dangky`, `/hoso`, `/hoso xoa`, `/lichcuatoi` hỗ trợ hồ sơ bền vững; hồ sơ chỉ là bộ lọc, không phải xác minh danh tính hay quyền tài liệu.
- Câu tự nhiên tra lịch/quy định đi qua bộ xử lý trực tiếp. Câu hỏi nhóm không rơi xuống model để bịa quy định. Cách diễn đạt mơ hồ về ngày được hướng dẫn dùng lệnh; không tự thực thi thay đổi lịch hoặc xác nhận issue.
- `Test`: 181 bài, 174 đạt, 7 bỏ qua (6 bài Hermes chạy riêng, 1 bài symlink cần quyền). 14 bài nhóm mới kiểm tra phân quyền, hồ sơ theo từng người/chat, tài liệu riêng, dữ liệu thiếu, đăng ký lỗi, câu tự nhiên, chặn ghi, định tuyến và khử trùng.
- `TestHermes`: 6/6 đạt, gồm thành viên mới qua adapter của Hermes thật; mạng Zalo/AI vẫn mô phỏng.
- Google Sheets thật: thành viên giả lập trong nhóm đã cấp quyền đăng ký tên có trong bảng, hỏi lịch cá nhân có nguồn, người khác vẫn chưa có hồ sơ. Chỉ đọc, dùng SQLite tạm, không gửi Zalo/AI và không tạo hồ sơ production.
- Kiểm tra dữ liệu hiện tại: 1 tài liệu được cấu hình, 0 tài liệu được cấp quyền nhóm. Cần bổ sung quy định thật và quyền nhóm trước khi kết luận hỏi đáp quy định có dữ liệu.
- Plugin đã cập nhật; `git diff --check` đạt. Cần người dùng khởi động lại bot và thử với các thành viên trên Zalo thật. Chưa xác nhận nhận/gửi luồng nhóm thật trong lượt này. Hướng dẫn: [dùng trong nhóm](dung-trong-nhom.md).

## Sửa nhận lệnh sau mention tên hiển thị — 06/10/2026

- Người dùng báo `@Bot LabCoach /menu` nhận câu trả lời thiếu tài liệu: đây là xác nhận bot đã nhận và trả lời trong nhóm, nhưng lệnh menu bị nhận nhầm.
- Kiểm tra `getMe` thật, chỉ đọc thông tin bot: có `display_name=Bot LabCoach`, khác `account_name` dạng handle nội bộ. Code cũ chỉ dùng handle nên không bỏ được phần gọi tên hiển thị.
- Adapter nhận cả hai tên, chỉ tách mention ở đầu tin nhóm; hỗ trợ khác biệt chữ hoa, khoảng trắng Unicode và dấu tiếng Việt. Giữ nguyên nội dung phía sau, phân quyền và hành vi tin riêng.
- Kiểm thử cục bộ: **193 bài, 186 đạt, 7 bỏ qua** (6 bài Hermes chạy riêng + 1 symlink cần quyền). Có 12 bài mới về tên hiển thị/handle, nội dung menu, câu tự nhiên, Unicode, tên khác, vị trí mention và tin riêng. Mock kết nối kiểm tra lấy đúng tên từ `getMe`.
- `TestHermes`: **6/6 đạt**, dùng Hermes thật với mạng Zalo/AI mô phỏng. Plugin đã đồng bộ vào runtime. Cần khởi động lại bot và gửi lại mention để xác nhận bản sửa trên Zalo; lần chẩn đoán này không lấy tin polling hoặc tự gửi tin.
