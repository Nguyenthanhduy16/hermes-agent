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

# LabCoach Tickets — 08/10/2026

- Thêm trang web cục bộ tiếng Việt với tạo yêu cầu, chờ xác nhận, yêu cầu của tôi và lịch sử. Đã thử trọn luồng An gửi → Bình đồng ý → quản lý duyệt → đọc lại hai ca demo → hoàn tất và xem lịch sử sau tải lại trang.
- Xác thực bằng phiên phía máy chủ; quyền theo tài khoản đã được quản trị liên kết với mã thành viên. Demo có các tài khoản mẫu riêng. `/dangky` trên Zalo không cấp quyền đổi ca.
- Lưu yêu cầu, bản xem trước, lịch sử, trạng thái và giữ ca bằng SQLite. Chặn thao tác lặp, ca trùng, thay đổi nguồn/quyền/lịch; yêu cầu không rõ kết quả chỉ được đối soát bằng thao tác đọc, không tự gửi lại lệnh ghi.
- Kết nối ghi chỉ dành cho bảng Sheets thử riêng, có mã phân công ổn định và dữ liệu chuẩn hóa. Một lần cập nhật chứa cả hai phân công; vẫn phải đọc lại trước khi hoàn tất. Chưa có cơ chế khóa người sửa trực tiếp trên Sheets; điều kiện chỉ một bên ghi được nêu rõ trong cấu hình và hướng dẫn.
- Bộ tự động toàn repo: **245 bài, 238 đạt, 7 bỏ qua** (6 bài cần runtime Hermes riêng, 1 bài symlink cần quyền Windows). **52 bài Tickets mới đều đạt**, bao gồm phân quyền, trạng thái, xung đột, khởi động lại, timeout, chèn dòng, ô gộp và kết quả đọc lại sai. Sau khi thêm tình huống mã CSRF không hợp lệ, chạy lại riêng cả 7 bài HTTP đều đạt.
- `TestHermes`: **6/6 đạt** với runtime Hermes thật, Zalo/AI mô phỏng. Lần chạy đầu bị sandbox chặn truy cập Python của Hermes; chạy lại với quyền bổ sung thành công, không sửa quyền hay cài lại runtime. Tổng cộng **244 bài đạt qua hai môi trường**, còn 1 bài symlink bị bỏ qua.
- Kiểm tra trình duyệt thật ở chiều rộng 1.440 px và 390 px: luồng tạo/xác nhận/duyệt thành công, màn hình nhỏ không tràn ngang, không có lỗi JavaScript được ghi nhận. Dữ liệu minh họa và ảnh kiểm tra lưu trong `.runtime/tickets/`, không đưa vào Git.
- Đã chạy thử lệnh PowerShell `run.ps1 TicketsDemo` thành công và dừng tiến trình xem thử sau kiểm tra. Dữ liệu demo được giữ lại. Cập nhật bot đang cài bằng `run.ps1 Update`, rồi khởi động lại khi người dùng sẵn sàng; lượt này chưa ghi đè cấu hình Hermes đang dùng.
- Thêm `/tickets` và câu “mở trang đổi ca”; chỉ trả link HTTPS công khai đã cấu hình. Chưa cấu hình link thì hướng dẫn người dùng liên hệ quản trị. Không tự gửi thông báo Zalo hoặc giả định có trang đã triển khai.
- Chưa ghi Google Sheets thật, chưa thử lệnh Tickets qua Zalo thật, chưa triển khai máy chủ. Không sửa lịch Lab đang dùng hoặc bí mật người dùng. Cần tạo bảng thử riêng, liên kết tài khoản và làm bài thử ghi–đọc lại theo [hướng dẫn Tickets](tickets.md).

# Tickets qua tin nhắn riêng — 08/10/2026

- Luồng chính chuyển sang Zalo DM: chọn ca A → chọn người B và ca B → lý do → xem trước và gửi → B xác nhận → quản lý duyệt → đọc lại kết quả. Web cũ được giữ làm phụ lục; `/tickets` không yêu cầu mở web hoặc Mini App.
- Liên kết quản trị theo đúng bot, người gửi và chat riêng; không dùng tên tự khai từ `/dangky`. Thay đổi vai trò/mã thành viên, thu hồi hoặc liên kết lại làm mất hiệu lực bước nhập/xác nhận cũ. Quyền sử dụng bot trong `ZALO_ALLOWED_USERS` vẫn kiểm tra độc lập.
- Lưu bước nhập và dấu tiếp nhận tin trong SQLite. Mã chọn số không tái dùng giữa các bước; xác nhận gắn với bản xem trước hoặc phiên bản ticket và hết hạn sau 5 phút. Không tự chạy lại tin đã tiếp nhận kể cả dịch vụ bị ngắt giữa thao tác.
- Hỗ trợ tìm kiếm, phân trang, quay lại, thoát, danh sách chờ và lịch sử. Thoát bước nhập không hủy ticket. Tin Tickets trong nhóm chỉ nhận hướng dẫn nhắn riêng, không lộ nội dung riêng.
- Rà soát thêm và sửa hai lỗi ngữ cảnh: lý do “Xin nghỉ phép vì việc gia đình” không bị chuyển sang hỏi quy định; nội dung nhập sau khi hết hạn/thu hồi quyền không rơi sang model. Lệnh hỏi lịch, tài liệu và nhắc việc vẫn dùng được trong lúc nhập bản nháp.
- Chỉ bật với `TICKETS_CHAT_MODE=sheets_test`, mặc định `off`. Demo không được dùng làm danh tính trên gateway thật. CLI quản trị, web và gateway dùng chung đường dẫn dữ liệu và khóa tiến trình, tránh vừa thay quyền vừa ghi lịch. Không tự bật thông báo chủ động.
- `run.ps1 TicketsChatDemo`: đạt luồng A → B → quản lý và đọc lại đúng cả hai ca mô phỏng, không cần token, Google hoặc model. Dữ liệu demo nằm trong thư mục tạm và được dọn khi kết thúc.
- Bộ toàn repo cuối: **297 bài, 290 đạt, 7 bỏ qua** (6 bài chạy bằng runtime Hermes riêng; 1 bài symlink cần quyền Windows). `run.ps1 TestHermes`: **6/6 đạt**. Tổng cộng **296 bài đạt qua hai môi trường**, 1 bài bị bỏ qua. Có 20 bài hội thoại Tickets, 16 bài định danh/runtime/CLI, 17 bài lệnh và 6 bài kết hợp WorkService–Tickets; các bài nghiệp vụ, Sheets mô phỏng và web trước đó vẫn đạt.
- Các bài kiểm thử bao phủ quyền, xác nhận, hủy/từ chối/hết hạn, xung đột, xử lý lặp, khởi động lại, lịch thay đổi, timeout sau ghi, đối soát không gửi lại lệnh ghi và lỗi đọc lại. `git diff --check` đạt.
- Chưa nhận/gửi Tickets qua Zalo thật hoặc ghi/đọc lại Sheets thử thật trong lượt này. Không sửa `.env`, cấu hình Hermes, lịch Lab đang dùng, không khởi chạy gateway hoặc triển khai dịch vụ. Thực hiện [hướng dẫn Tickets qua tin nhắn riêng](tickets-chat.md) để cấu hình bảng thử, liên kết tài khoản, cập nhật plugin và thử thực tế.

# Tickets trên lịch Lab hiện có — 09/10/2026

- Thêm `lab_live`: dùng bảng và các tab lịch đã cấu hình, chỉ đổi đúng hai ô D (Lab Coach). Giữ chế độ bảng thử riêng. Không thêm cột hoặc mã lên Sheets; sửa cấu trúc hoặc dữ liệu ngoài D làm yêu cầu cũ mất hiệu lực.
- Luồng hiện tại gồm hai người, không yêu cầu quản lý duyệt. Kiểm tra xung đột cả Lab Coach và Key Coach; chỉ đọc lại khi kết quả ghi chưa rõ, kể cả đã qua ngày ca. Hai người liên quan được đối soát khi không có bước quản lý.
- Từ chối ô D có công thức/ô gộp/tên mơ hồ, phân công D/E không xác định được dòng và ca tương lai thiếu thông tin. Giờ Sáng/Chiều/Tối phải do quản trị cung cấp; giờ rõ trong ô Buổi được đọc trực tiếp. Không dùng giờ demo cho lịch thật.
- `TicketsLabCheck` đã gọi Google chỉ đọc và đối chiếu đúng **2/2 tên** người dùng cung cấp. `TicketsLinkUsers` đã tạo **2 liên kết cục bộ** với tài khoản Zalo đã xác minh; file danh tính, `.env` và cơ sở dữ liệu đều bị loại khỏi Git. Đã bổ sung quyền dùng bot cho người B, giữ các quyền có sẵn.
- Dữ liệu thật kiểm tra ngày 09/10 chưa có ca hiện tại/tương lai của hai người: ca gần nhất của A ngày 06/10, B ngày 05/10. Chưa được cung cấp giờ buổi thực tế; `TICKETS_CHAT_MODE=off`, hai cờ ghi lịch vẫn `false`, `TICKETS_REQUIRE_MANAGER=false`.
- Bộ toàn repo: **324 bài, 317 đạt, 7 bỏ qua**; runtime Hermes riêng **6/6 đạt**. Tổng **323 bài đạt**, còn 1 bài symlink cần quyền Windows. Các bài mới bao gồm 15 bài provider Lab, 11 bài liên kết/runtime và 1 bài luồng đủ A/B với hai tình huống ghi thành công và mất phản hồi sau ghi. Toàn bộ thao tác ghi trong kiểm thử đều dùng HTTP mô phỏng.
- Ổn định bài thử HTTP cũ trên Windows: kiểm tra từ chối kích thước bằng header, tránh việc gửi tiếp phần thân sau khi máy chủ đã đóng kết nối gây lỗi socket ngẫu nhiên. Không thay đổi máy chủ web hoặc nới điều kiện giới hạn.
- Đã cập nhật mã plugin trên máy; không thay cấu hình model và không khởi chạy gateway. **Chưa thử đổi ca bằng tin Zalo thật, chưa gửi lệnh ghi Sheets thật.** Cần bổ sung giờ buổi và ca tương lai trước khi bật theo [hướng dẫn](tickets-chat.md).

## Thử ngay trên hai tài khoản đã xác minh — 09/10/2026

- Theo lựa chọn của người dùng, thêm `lab_live_test` để chọn ca đang có, kể cả ca cũ, và ghi thật đúng hai ô Lab Coach sau xác nhận A/B. Chế độ này bỏ kiểm tra giờ/trùng lịch; thông báo rõ giới hạn tại menu, xem trước, chi tiết và kết quả. Chỉ hiển thị ngày/buổi nguồn, không đưa giờ giả ra hội thoại.
- Giới hạn đúng hai tên được cấu hình và liên kết bot/người/chat riêng đã xác minh. Cơ sở dữ liệu, đích xác nhận và cờ ghi thử tách riêng với `lab_live`; kiểm tra dữ liệu thay đổi, ghi hai ô cùng batch, đọc lại, chống trùng và không gửi ghi lại khi timeout vẫn giữ.
- Đã đối chiếu Google và nhập 2 liên kết vào dữ liệu thử cục bộ. Bản dữ liệu tải về có 8 ca của A, 14 ca của B đủ thông tin để chọn. Không cần bổ sung giờ hoặc ca mới cho riêng bài thử này.
- Đã đặt `TICKETS_CHAT_MODE=lab_live_test`, `TICKETS_REQUIRE_MANAGER=false`, bật hai cờ `TICKETS_LAB_TEST_WRITE_ENABLED`/`TICKETS_LAB_TEST_EXCLUSIVE_WRITER`; các cờ ghi lịch thông thường vẫn `false`. Đã đồng bộ mã plugin, cần khởi động lại gateway để nạp cấu hình. Người dùng được hướng dẫn không sửa lịch trực tiếp đồng thời lúc thử.
- Kiểm thử cuối: **332 bài, 325 đạt, 7 bỏ qua**; Hermes riêng **6/6 đạt**, tổng **331 bài đạt**, 1 bài symlink bị bỏ qua. Có luồng chat đầy đủ trên ca quá khứ/trùng Key Coach bằng HTTP mô phỏng: bản xem trước 5 phút, yêu cầu 48 giờ tính từ lúc thao tác; chỉ 2 ô D thay đổi sau B xác nhận, không ghi thêm khi lặp tin.
- **Chưa tạo ticket thật hoặc ghi ô Sheets trong lượt chuẩn bị.** Hai người cần tự nhắn riêng và xác nhận qua Zalo để kiểm tra nhận–gửi và ghi thực tế.

## Tin nhắn gọn và mã xác nhận ngắn — 09/10/2026

- Tin nhắn đưa lệnh cần gửi lên đầu, chia luồng thành 4 bước, ẩn mã phân công nội bộ và trình bày hai ca sau khi đổi. Tin gửi thành công chỉ rõ người còn lại dùng `/choxacnhan`; nếu có đúng một yêu cầu chờ, lệnh này mở thẳng nội dung xác nhận.
- Bước nhập, bản xem trước và mã thao tác mới có hiệu lực 15 phút; vẫn giới hạn theo giờ bắt đầu ca trong chế độ thông thường. Yêu cầu đã gửi giữ thời hạn tối đa 48 giờ. Bản nháp/mã đã tồn tại không tự gia hạn khi cập nhật.
- `/dongy` và các thao tác xác nhận dùng mã 6 chữ số, gắn đúng người, cuộc trò chuyện, yêu cầu và phiên bản dữ liệu. Mã không tái sử dụng sau khởi động lại; mã cũ dạng dài tiếp tục được kiểm tra theo cơ chế cũ. Nhắn số đơn lẻ hoặc “đồng ý” không xác nhận đổi ca.
- Kiểm thử toàn repo: **342 bài, 335 đạt, 7 bỏ qua**. Sau điều chỉnh tin gửi thành công, chạy lại **149 bài Tickets đều đạt**. `TestHermes`: **6/6 đạt** với runtime Hermes thật, các API mạng được mô phỏng; tổng **341 bài đạt qua hai môi trường**, còn 1 bài symlink Windows bị bỏ qua.
- Có 10 bài mới kiểm tra hạn 899/900 giây, sai người/chat, nhiều yêu cầu, liên kết và phiên bản thay đổi, chống xử lý lặp, khởi động lại, trùng mã và chuyển dữ liệu mã cũ. Demo hội thoại cục bộ hoàn tất, không gọi Zalo hoặc Google.
- Đã sao lưu nhất quán hai cơ sở dữ liệu Tickets bằng SQLite backup và đồng bộ mã plugin vào Hermes; giữ nguyên cấu hình và dữ liệu đang dùng. Cần khởi động lại bot để nạp bản mới. **Chưa nhận/gửi phiên bản này qua Zalo thật hoặc ghi Sheets thật trong lượt này.**

## Rút mã xác nhận còn 4 số — 09/10/2026

- Theo yêu cầu người dùng, mã xác nhận mới còn 4 chữ số; giữ hạn 15 phút và ràng buộc người/chat/yêu cầu. Mã 6 số đã cấp vẫn được kiểm tra nguyên vẹn theo thời hạn cũ, không bị cắt ngắn; mở lại yêu cầu cấp mã 4 số.
- Giữ tiền tố chế độ và sổ mã bền vững, không tái sử dụng mã. Tìm hết vùng mã còn trống trước khi báo hết mã; tối đa 1.000 mã mới mỗi chế độ/cơ sở dữ liệu. Khi hết, từ chối cấp mới, không tự đổi lịch hoặc xóa dữ liệu.
- **151 bài Tickets đều đạt**, bao gồm mã 4 số tại biên, tương thích mã 6 số, tìm mã khi gần hết/hết vùng cấp, hết hạn và chống xử lý trùng. Demo hội thoại hoàn tất và đọc lại đúng hai ca mô phỏng.
- Đồng bộ mã plugin trên máy; cần khởi động lại bot. Không đổi cấu hình, không nhận/gửi Zalo hoặc ghi Sheets thật trong lượt này.

## Thông báo riêng cho người đổi cùng — 09/10/2026

- Khi A gửi yêu cầu mới thành công, lưu thông báo cho B trong cùng giao dịch tạo yêu cầu. Đích gửi lấy từ liên kết quản trị đã xác minh; kiểm tra lại quyền hai người, bot, chat riêng, trạng thái, phiên bản, thời hạn và lịch nguồn trước khi gửi. Không tạo thông báo cho bản nháp hoặc tự gửi lại yêu cầu cũ.
- B nhận đầy đủ hai ca, lý do và tác động trước các lệnh `/dongy`/`/tuchoi` kèm mã 4 số; mã có tối đa 15 phút. Mã thông báo lưu riêng theo yêu cầu, không ghi đè bước nhập của B. Vẫn kiểm tra lại quyền và dữ liệu trước khi đổi lịch; `/choxacnhan` dùng được khi chưa nhận tin hoặc mã hết hạn.
- Bộ gửi chạy riêng, chỉ nhận đích từ hàng đợi đã xác minh, không mở quyền gửi tùy ý cho model. Gửi các đoạn tuần tự và dừng ngay khi lỗi. Trạng thái đang gửi khi bị ngắt được đánh dấu chưa rõ kết quả; không tự phát lại thông báo. Lỗi gửi không hủy yêu cầu hoặc làm mất chức năng xem thủ công. Thống kê chỉ lưu số lượng/kết quả, không đưa danh tính hay nội dung vào báo cáo.
- Đã đối chiếu [API sendMessage chính thức](https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage); bản này dùng lệnh văn bản, không giả định có nút callback. Điều kiện gửi thực tế còn phụ thuộc Zalo, quyền bot và người nhận.
- **373 bài toàn repo: 366 đạt, 7 bỏ qua**; `TestHermes` **6/6 đạt**, tổng **372 bài đạt qua hai môi trường**, còn 1 bài symlink Windows bị bỏ qua. Có **29 bài thông báo mới**: 14 bài hàng đợi/quyền/giao dịch/khởi động lại/hết hạn và 15 bài vận chuyển, gồm luồng A gửi → B nhận riêng → đồng ý trực tiếp với SQLite và API mô phỏng. `git diff --check` đạt.
- Đã sao lưu nhất quán hai cơ sở dữ liệu Tickets cục bộ, đồng bộ và đối chiếu mã plugin đã cài. Không sửa `.env` hay model; người dùng cần khởi động lại bot. **Chưa gửi thông báo Zalo thật hoặc ghi Sheets thật trong lượt này.**

## Rút gọn menu theo phạm vi người dùng chọn — 09/10/2026

- Người dùng xác nhận chỉ giữ **Tra lịch**, **Lịch của tôi**, **Đổi ca**. `/menu`, `/help` và `/trogiup` dùng menu gọn; bỏ các mục nhắc việc, tài liệu/quy định, issue, góp ý và danh sách trợ giúp dài khỏi menu chính. Hướng dẫn riêng và các lệnh hiện có vẫn dùng được khi gọi trực tiếp.
- Chỉ hiện lịch khi đã cấu hình và có quyền; lịch cá nhân dành cho lịch Lab. Đổi ca hiện khi tính năng đã bật, không lỗi khởi tạo và người dùng có quyền nhắn riêng. Thành viên chỉ có quyền đọc nhóm không được quảng bá thao tác đổi ca; menu nhóm nhắc @bot hoặc trả lời tin của bot.
- **49 bài kiểm thử liên quan đều đạt**: 14 bài lệnh, 17 bài Tickets/lệnh và 18 bài nhóm. Kiểm tra menu không gọi Google, không hiển thị mục bị ẩn kể cả issue được bật, và không liệt kê lịch/tickets chưa sẵn sàng. Không thêm bài kiểm thử mới cho thay đổi trình bày.
- Đồng bộ menu vào plugin trên máy; cần khởi động lại bot và gửi `/menu`. Không gửi Zalo, sửa Sheets hoặc thay đổi cấu hình trong lượt này. Việc giữ mục Đổi ca trong menu không xác nhận một lần đổi lịch thật đã hoàn tất.

## Lệnh đổi ca trực tiếp, ca tương lai và chuyển trang — 09/10/2026

- Menu chính dùng **Đổi ca: `/doica`** để mở ngay bước chọn ca. `/tickets` vẫn là hướng dẫn/tiếp tục bước đang nhập.
- Mọi chế độ, kể cả `lab_live_test`, chỉ cho chọn và đổi ca chưa bắt đầu. Dùng giờ rõ ràng trong ô Buổi; với chế độ thử thiếu giờ, chỉ chọn từ ngày mai theo giờ Việt Nam. Kiểm tra lại khi hiển thị, chọn, xem trước, gửi, xác nhận và ngay trước ghi; bản nháp/mã cũ không bỏ qua quy tắc mới. Chế độ thử vẫn chưa kiểm tra trùng lịch.
- Các danh sách mặc định chỉ hiện yêu cầu có cả hai ca tương lai. Yêu cầu cũ được giữ trong lịch sử và vẫn đọc bằng mã; yêu cầu đang chờ nhưng ca đã qua hết hiệu lực, còn giao dịch đang ghi/chưa rõ kết quả giữ khả năng đối soát. Thêm tương thích đọc đối soát cho giờ nội bộ của bản thử cũ; không đổi dữ liệu đã lưu và không phát sinh ghi lại Sheets.
- Mỗi trang tối đa 5 lựa chọn. `/doica trang N`, `/doica tiep`, `/doica truoc` giữ bước, ca đã chọn và bộ lọc; `tiếp`/`trước` vẫn dùng được. Hiển thị lệnh trang trước/trang sau ở cuối danh sách, kể cả danh sách yêu cầu và lịch sử. Trang sai, ngoài phạm vi hoặc hết phiên không tự chọn/gửi yêu cầu.
- **393 bài kiểm thử toàn repo: 386 đạt, 7 bỏ qua** (6 bài cần môi trường Hermes riêng, 1 bài symlink Windows). Có 20 bài mới kiểm tra ca tương lai, ranh giới giờ/ngày Việt Nam, bước đã lưu, phân trang, hết hạn và đối soát dữ liệu phiên bản cũ. Các bài ghi dùng nguồn mô phỏng, giữ kiểm tra chỉ hai ô Lab Coach thay đổi. `git diff --check` đạt.
- Sao lưu SQLite cục bộ và đồng bộ plugin; cần khởi động lại bot. **Không gửi tin Zalo hoặc sửa lịch Google Sheets thật trong lượt này.** Không tự dời ngày ca để có dữ liệu thử; nếu bảng không có ca tương lai, danh sách lựa chọn sẽ trống.

## Bổ sung nhắc lịch, thông báo và issue vào menu — 09/10/2026

- Theo yêu cầu mới, giữ Tra lịch, Lịch của tôi và Đổi ca; thêm **Nhắc lịch: `/nhac`**, **Thông báo: `/thongbao`**, **Tạo issue: `/issue`** theo quyền. Issue hiện tắt trong cấu hình máy, được ghi rõ **(chưa bật)** cho người có quyền Sheets; không tự bật ghi.
- `/thongbao` là hướng dẫn xem nhắc đang chờ, lịch sử nhắc tại chat hiện tại và yêu cầu đổi ca chờ xác nhận trong tin nhắn riêng. Không tự tạo nhắc từ Sheets, không có số tin chưa đọc. Mở hướng dẫn không khởi tạo Tickets, thay bước nhập, tạo nhắc hoặc tạo issue.
- **81 bài liên quan đều đạt**: 17 bài lệnh/menu, 15 bài nhắc việc, 14 bài issue, 18 bài nhóm, 17 bài Tickets/lệnh. Bổ sung 3 bài kiểm tra menu theo quyền, trạng thái bật/tắt và việc mở hướng dẫn không gọi Google hay tạo công việc. Các luồng ghi/nhắc trong kiểm thử dùng dữ liệu tạm và nguồn mô phỏng.
- Đồng bộ hai tệp xử lý lệnh và công việc vào plugin trên máy. Cần khởi động lại bot để thử menu. Không sửa `.env`, không gửi Zalo hoặc ghi Sheets thật trong lượt này.

## Hỏi tự nhiên và trò chuyện trong nhóm — 09/10/2026

- Thành viên trong nhóm đã cấp quyền có thể trò chuyện với Hermes khi @bot hoặc trả lời tin bot, theo chế độ `members`/`allowlist` hiện có. Giữ quyền nhắn riêng, phiên theo người và chat, công cụ rỗng và đích gửi đúng lượt nhận. Thiếu bộ xử lý công việc thì không mở đường hội thoại cho thành viên chỉ có quyền nhóm.
- Thêm cách hỏi lịch cá nhân bằng “Ngày mai mình rảnh không?”, “Ngày mai tôi trực không?”; trả lịch đã phân công, không suy ra chắc chắn rảnh. Tránh nhận nhầm câu hỏi về lớp học Python hoặc “bạn” thành lịch. Ngày mơ hồ vẫn cần nhập rõ.
- Thêm câu tự nhiên mở/tiếp tục đổi ca, xem yêu cầu chờ, xem yêu cầu của mình, thông báo, nhắc việc và menu. Lệnh nhắc có nội dung “báo lỗi” giữ nguyên nội dung; các yêu cầu tạo issue tự nhiên chỉ mở hướng dẫn. Không dùng lời đồng ý chung chung để xác nhận thay đổi.
- Câu hỏi lịch/quy định tiếp tục đi qua dữ liệu được phép. Câu hỏi nội bộ chưa nhận diện trong nhóm được giữ ở phần tra tài liệu; thiếu nguồn không tự chuyển cho model bịa dữ liệu. Trò chuyện thông thường không đọc Sheets/tài liệu, model không được cấp công cụ để thao tác.
- **413 bài toàn repo: 405 đạt, 8 bỏ qua**. Chạy riêng `TestHermes`: **7/7 đạt**, gồm trò chuyện nhóm của thành viên mới, chống xử lý trùng, tách phiên/người, chặn nhóm và tin riêng chưa được phép. Tổng **412 bài đạt**, còn 1 bài symlink Windows bị bỏ qua. Đầu vào Zalo, đầu ra model và thao tác ghi trong các bài đều mô phỏng.
- Đồng bộ mã plugin; cần khởi động lại bot và thử trên Zalo. Không đổi `.env`, không gửi tin thật hay sửa Sheets trong lượt này. Chưa xác minh trả lời trò chuyện nhóm với model/Zalo thật.
