# Đăng ký thành viên qua số điện thoại

Thành viên nhắn riêng `/dangky SỐ_ĐIỆN_THOẠI` → bot đối chiếu Workload và lưu tài khoản Zalo. Với `REGISTRATION_AUTO_APPROVE=true`, số khớp duy nhất sẽ **được duyệt ngay**, có quyền thành viên trong chat riêng: lịch, đổi ca, nhắc việc, góp ý và trò chuyện. Không thêm từng người vào `.env`, không cần gửi CSV. Quản trị vẫn quản lý và thu hồi quyền; tự duyệt không cấp quyền quản trị.

## Thành viên làm gì?

1. Mở chat **riêng** với bot, gửi `/dangky` để xem hướng dẫn.
2. Gửi `/dangky SỐ_ĐIỆN_THOẠI`, thay phần viết hoa bằng số của bạn đã có trong Workload. Dùng số di động Việt Nam đủ 10 chữ số, bắt đầu bằng `0`, hoặc dạng `+84` tương ứng. Có thể có khoảng trắng, dấu chấm hoặc gạch ngang.
3. Khi bật tự duyệt, nhận ngay **“✅ Đăng ký thành công!”**, tên trong Workload và hướng dẫn `/menu`. Người đang chờ từ trước gửi lại `/dangky SỐ_ĐIỆN_THOẠI` để được đối chiếu, không tạo bản ghi trùng. Khi tắt tự duyệt, bot trả mã `DK-…` để quản trị duyệt.
4. Gửi `/menu` để bắt đầu; `/dangky trangthai` hoặc `/trangthaidangky` để xem trạng thái.

Số khớp Workload không xác minh người nhắn sở hữu số đó; chế độ tự duyệt không có OTP. Một người/số chỉ liên kết với một tài khoản và chat riêng; không tự ghi đè liên kết cũ. Số trùng trong Workload bị chặn. Tài khoản, chat hoặc thành viên từng bị quản trị từ chối/thu hồi vẫn cần duyệt lại thủ công. Tên trùng chưa có ánh xạ phân biệt vẫn bị chặn lịch cá nhân/đổi ca.

Không gửi số điện thoại trong nhóm. Bot lưu ID từ chính tin nhắn riêng nhận được; không suy ra chat riêng từ ID nhóm. Mỗi số trong Workload phải xác định một người; số bị trùng, mất số `0` đầu hoặc hiển thị dạng khoa học không được tự đoán sửa. Nếu Sheets làm mất số `0` đầu, quản trị đối chiếu số gốc rồi nhập lại ô theo định dạng văn bản; bot không tự sửa bảng.

Khi bật đăng ký Workload, thành viên cần đăng ký thành công trước khi gọi bot trong nhóm được cấp quyền. Chọn **@Bot** hoặc trả lời tin của bot. Người chưa đăng ký nhận hướng dẫn tại nhóm:

> Bạn cần đăng ký trước khi dùng bot.
>
> Nhắn riêng bot: /dangky SỐ_ĐIỆN_THOẠI
>
> Lưu ý: dùng số điện thoại hiện tại trên Workload; không gửi số trong nhóm.

Đăng ký xong, quay lại nhóm dùng `/menu` hoặc hỏi tự nhiên; không đăng ký lại. Quyền nhóm, quyền dữ liệu và các thao tác chỉ dùng trong chat riêng vẫn giữ nguyên. Người bị khóa không được dùng bot; người bị từ chối/thu hồi cần nhắn riêng `/dangky trangthai` để quản trị kiểm tra. Lượt chỉ hướng dẫn đăng ký không gọi AI/Sheets hoặc lưu nội dung vào lịch sử hội thoại. Khi tắt đăng ký, cơ chế allowlist cũ vẫn giữ nguyên.

Bot bỏ ký tự ẩn thường gặp khi sao chép (`U+200B`, `U+FEFF`) và chấp nhận khoảng trắng không ngắt (`U+00A0`, `U+202F`) trong cả tin đăng ký lẫn ô số điện thoại Workload. Cách này không thêm/bớt chữ số hoặc bỏ bước duyệt; số bị trùng sau chuẩn hóa vẫn bị chặn. Nếu còn báo không tìm thấy, kiểm tra cột C: số phải đủ chữ số, không kèm chữ, dấu nháy hoặc ký tự lạ khác; đặt ô ở định dạng văn bản rồi nhập lại từ nguồn đã xác minh.

Mỗi tài khoản được tra đăng ký tối đa 5 lần/giờ, toàn bot tối đa 60 lần/giờ. Giới hạn còn hiệu lực sau khi khởi động lại. Xem trạng thái không tiêu tốn lượt tra.

Bot báo riêng trường hợp không tìm thấy số trong Workload và trường hợp số xuất hiện nhiều lần, kèm hướng xử lý; không trả tên hoặc nhắc lại số điện thoại trong lỗi này.

## Quản trị chuẩn bị một lần

Nguồn danh tính là bảng đã cấu hình trong `ZALO_SHEETS_ID`, tab **`Workload - Lab coach`**:

| Trường | Vị trí | Tiêu đề phải khớp |
| --- | --- | --- |
| Số điện thoại | Cột C, dòng tiêu đề 5 | `Số điện thoại` |
| Họ tên | Cột D, dòng tiêu đề 5 | `Thành viên` |

Không dùng cột B hoặc tự chuyển sang tab tháng khác. Bot chỉ đọc hai cột cấu hình, không sửa Workload. Dữ liệu cần nằm trong giới hạn 1.000 dòng sau tiêu đề. Tiêu đề gộp dọc như C5:C6/D5:D6 được hỗ trợ nếu phần tiếp diễn trống; ghi chú gộp rộng ở cuối bảng được bỏ qua khi cả C/D trống và ô chứa ghi chú nằm ngoài hai cột. Ô dữ liệu tên/số điện thoại bị gộp, dữ liệu ẩn, công thức hoặc cấu trúc không rõ bị từ chối để tránh ghép nhầm.

Dừng bot bằng `Ctrl+C`, mở `.env` **trên máy** và bổ sung các biến sau, giữ nguyên token, quyền và cấu hình khác:

```dotenv
REGISTRATION_ENABLED=false
REGISTRATION_AUTO_APPROVE=false
REGISTRATION_ADMIN_USERS=
REGISTRATION_WORKLOAD_TAB=Workload - Lab coach
REGISTRATION_WORKLOAD_NAME_COLUMN=D
REGISTRATION_WORKLOAD_PHONE_COLUMN=C
REGISTRATION_WORKLOAD_HEADER_ROW=5
REGISTRATION_WORKLOAD_NAME_HEADER=Thành viên
REGISTRATION_WORKLOAD_PHONE_HEADER=Số điện thoại
```

Kiểm tra `ZALO_SHEETS_ID`, `ZALO_GOOGLE_CREDENTIALS` và quyền đọc bảng của service account, rồi chạy:

```powershell
.\scripts\run.ps1 RegistrationCheck
```

Lệnh này dùng được khi đăng ký còn tắt và chưa điền quản trị. Kết quả chỉ có số dòng, số điện thoại hợp lệ/thiếu/sai và số nhóm số điện thoại/tên bị trùng; không in tên, số điện thoại hay ID cá nhân. Lệnh không sửa Sheets, không tạo dữ liệu đăng ký và không tự bật tính năng. Số hợp lệ là đúng định dạng; số trùng hoặc tên trùng vẫn cần xử lý theo quy tắc đăng ký bên dưới.

Điền ID Zalo của quản trị vào `REGISTRATION_ADMIN_USERS`; nhiều ID cách nhau dấu phẩy. **Tất cả ID quản trị phải có trong `ZALO_ALLOWED_USERS`.** Dùng ID quản trị đã được cấp quyền ban đầu; nếu chưa có, làm bước `Discover` trong README. Không cần lấy ID thủ công cho các thành viên đăng ký sau đó.

Khi đã đủ cấu hình, đổi `REGISTRATION_ENABLED=true`. Đặt `REGISTRATION_AUTO_APPROVE=true` để tự duyệt, hoặc `false` để quản trị duyệt như trước. Mẫu mặc định tắt cả hai. Lưu `.env`, rồi chạy:

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 Check
.\scripts\run.ps1 Start
```

`Check` kiểm tra cấu hình bot chung. `RegistrationCheck` kiểm tra kết nối và cấu trúc Workload; bot tiếp tục đọc lại nguồn khi đăng ký và duyệt. Không đưa `.env`, file khóa, số điện thoại hoặc dữ liệu đăng ký lên Git.

**Bot trên Railway:** sửa `REGISTRATION_AUTO_APPROVE` trong Variables của service rồi triển khai lại bản code có tính năng này. Giữ nguyên các quyền, nguồn Sheets và Volume. Không bật đồng thời bot Windows. Bật tự duyệt không duyệt hàng loạt dữ liệu đang chờ; thành viên gửi lại lệnh để bot đọc mới Workload. Lịch sử quyết định ghi `auto_approved` do hệ thống, không mạo danh quản trị.

Nếu đã bật đăng ký nhưng cấu hình chưa hợp lệ, bot dừng xử lý tính năng và báo cần kiểm tra; không tự quay về đăng ký tên cũ hoặc bỏ qua bước xác minh.

## Xem và duyệt đăng ký

Quản trị nhắn **riêng** bot:

```text
/dangkychoduyet
/dangkychoduyet 2
```

Mỗi trang tối đa 5 yêu cầu, gồm tên Workload, số điện thoại đã che chỉ giữ 4 số cuối, `user_id`, `chat_id` và mã đăng ký bot tự thu nhận. Không chuyển tiếp danh sách này vào nhóm.

Phần duyệt thủ công dùng khi `REGISTRATION_AUTO_APPROVE=false` hoặc cần xử lý đăng ký từng bị từ chối/thu hồi. Quản trị xác minh qua cách liên hệ đã biết hoặc đối chiếu trực tiếp trước khi duyệt lại.

| Thao tác | Lệnh |
| --- | --- |
| Duyệt sau khi xác minh | `/duyetdangky DK-MÃ_YÊU_CẦU` |
| Từ chối | `/tuchoidangky DK-MÃ_YÊU_CẦU` |
| Xem thành viên đã duyệt | `/thanhvien` hoặc `/thanhvien 2` |
| Thu hồi liên kết | `/thuhoidangky MÃ_THÀNH_VIÊN` |

Khi tự duyệt hoặc duyệt thủ công và Tickets đã bật, bot tự tạo liên kết đổi ca nếu nguồn/ánh xạ hợp lệ; thành viên không cần gửi CSV hoặc mở Tickets trước để được người khác chọn. Nếu liên kết chưa sẵn sàng, bot báo rõ trong phản hồi và vẫn lưu quyền thành viên. Sau khi sửa nguồn, người dùng có thể mở `/doica` để thử lại. Đăng ký không tạo yêu cầu đổi ca hoặc sửa lịch.

Sao chép đúng mã từ phản hồi của bot. Trước khi duyệt, bot đọc lại Workload; dữ liệu người đăng ký đã đổi thì cần kiểm tra lại, không âm thầm ghép sang người khác. Sau duyệt, thành viên tự xem `/dangky trangthai`; quản trị nhận mã thành viên và lệnh thu hồi.

Quản trị dùng `/thanhvien` trong chat riêng để tìm thành viên đã duyệt theo nguồn Workload hiện tại; mỗi trang tối đa 5 người gồm tên, 4 số điện thoại cuối, mã thành viên và lệnh thu hồi. Dùng `/thanhvien 2` để xem tiếp, sao chép lệnh thu hồi đúng người. Dữ liệu đăng ký và quyền thành viên lưu trong SQLite trên máy chạy bot, còn sau khi khởi động lại; không ghi danh bạ vào code, `.env` hoặc Git. Chỉ quản trị đã cấu hình được xem danh sách và thay đổi trạng thái.

## Xử lý khi đăng ký hoặc liên kết bị nhầm

Giữ `REGISTRATION_AUTO_APPROVE=true`: thành viên mới có số khớp duy nhất vẫn đăng ký tự động. Không cần Admin duyệt từng người. Nếu tin “Đăng ký thành công” hiện sai tên, thành viên dừng dùng lịch cá nhân/đổi ca và báo Admin; không thử số của người khác.

**Muốn thành viên tự đăng ký lại:** Admin có quyền `members` mở **Thành viên → hồ sơ → Xóa đăng ký**, kiểm tra tên/số đã che, nhập lý do rồi tích xác nhận. Sau khi báo thành công, thành viên nhắn riêng `/dangky SỐ_ĐIỆN_THOẠI` dùng số hiện có trên Workload và kiểm tra lại họ tên. Quyền cũ bị thu hồi; liên kết chat riêng/Tickets cũ được gỡ, ngữ cảnh được làm mới và ghi nhớ cá nhân cũ ngừng dùng. Lịch, Workload, lịch sử và issue vẫn được giữ. Hồ sơ cũ có trạng thái **Đã xóa đăng ký**; xem lại bằng bộ lọc cùng tên. Thành viên bị khóa hoặc có đổi ca chưa kết thúc cần xử lý trước; thao tác này không vượt khóa hoặc ghi đè đăng ký của người khác.

**Đúng người đã đăng ký nhưng liên kết Tickets sai:** Admin có quyền `members` mở **Thành viên** → tìm người → xem chi tiết → chọn liên kết hệ thống đã đối chiếu → xem trước → xác nhận. Có thể khóa quyền trong lúc kiểm tra, rồi mở lại sau khi xử lý. Thao tác này chỉ sửa liên kết nội bộ; không đổi danh tính đăng ký hoặc ô lịch thật. Tên trùng, tài khoản đích thuộc người khác hoặc còn yêu cầu đổi ca liên quan thì hệ thống chặn; xử lý đúng nguyên nhân trước, không ghi đè.

**Đã đăng ký nhầm sang người khác:** Admin có quyền `members` có thể xử lý ngay trên trang Admin:

1. **Thành viên** → tìm và mở đúng hồ sơ → **Sửa đăng ký nhầm**.
2. Nhập **số điện thoại đúng hiện có trên Workload** và lý do sửa ngắn gọn. Không ghi số điện thoại trong ô lý do.
3. Bấm **Xem trước thay đổi**. Đối chiếu tên trước/sau, tài khoản Zalo và chat riêng; xác minh đúng người thực tế. Số điện thoại được che, chỉ hiện bốn số cuối.
4. Chọn xác nhận đã kiểm tra đúng chủ tài khoản, rồi **Xác nhận sửa đăng ký**. Thành viên gửi `/dangky trangthai` để kiểm tra tên đúng và tiếp tục dùng `/lichcuatoi` hoặc `/doica`; **không cần đăng ký lại**.

Hệ thống thu hồi đăng ký nhầm và duyệt hồ sơ đúng trên cùng tài khoản Zalo/chat riêng. Mã thành viên cũ được giữ; người đúng dùng mã cố định đã có hoặc được cấp mã mới. Lịch sử, issue và ghi nhớ cá nhân không tự chuyển giữa hai người. Ngữ cảnh cũ được làm mới, ghi nhớ theo phiên cũ ngừng dùng, nhắc việc thủ công cũ không tiếp tục gửi hoặc xuất hiện trong lịch sử nhắc của người mới. Liên kết Tickets cũ được gỡ và đối chiếu lại khi dùng bot; lịch thật không bị sửa.

Chỉ sửa đăng ký đang chờ/đã duyệt và không bị khóa. Nếu cần khóa trong lúc xác minh, xử lý xong nguyên nhân rồi mở quyền trước khi sửa. Số không tìm thấy, số/tên trùng, người đích đã đăng ký ở tài khoản khác, liên kết đích bị khóa/xung đột hoặc còn yêu cầu đổi ca liên quan đều bị chặn. Không nhập tùy ý tên hoặc Zalo ID để chuyển quyền. Nếu báo lỗi hoặc mất kết nối khi xác nhận, tải lại hồ sơ để kiểm tra kết quả trước khi thử lại.

Hồ sơ đã bị thu hồi/từ chối tiếp tục dùng quy trình đăng ký lại và duyệt thủ công: thành viên gửi `/dangky SỐ_ĐIỆN_THOẠI_ĐÚNG`; quản trị đăng ký dùng `/dangkychoduyet` rồi `/duyetdangky DK-MÃ_YÊU_CẦU` sau khi xác minh. Các lệnh này vẫn cần ID thuộc cả `REGISTRATION_ADMIN_USERS` và `ZALO_ALLOWED_USERS`; quyền web `members` không tự cấp quyền lệnh Zalo.

Các thay đổi đều có lịch sử người thực hiện, thời gian và lý do. Nếu thông tin Workload đã đổi khiến không thể mở khóa hoặc đối chiếu, nhờ người vận hành xử lý nguồn/ánh xạ trước; không tắt kiểm tra để đi tiếp.

## Duyệt xong được dùng những gì?

| Phạm vi | Điều kiện |
| --- | --- |
| Trò chuyện riêng với bot | Danh tính đã duyệt, đúng người gửi và chat riêng đã liên kết; người được cấp quyền bot trước đó phải không có đăng ký đang bị chặn |
| Tra lịch trong chat riêng | Tự có quyền đọc các tab lịch đã cấu hình tại đúng chat riêng đã liên kết |
| Lịch cá nhân | Danh tính còn khớp Workload và tên xác định duy nhất; trong nhóm cần quyền nhóm sẵn có |
| Đổi ca | Tự có quyền thành viên; Tickets đã bật, đúng nguồn lịch và tên duy nhất; vẫn cần xác nhận đổi ca và các điều kiện của chế độ đang chạy |
| Nhắc việc và thông báo | Tạo, xem, hủy nhắc của mình tại chat riêng đã liên kết; bot cần chạy để gửi đúng hạn |
| Góp ý | Gửi `/phanhoi Nội dung`; lưu riêng trên máy bot |
| Tạo issue | Nhắn riêng khi `ZALO_ISSUES_MODE=managed` đã được đối soát và bật; xem bản nháp rồi xác nhận. Issue lưu trong SQLite trên Railway, Admin xử lý trên Vercel |
| Tài liệu | Chỉ các file đã cấp quyền riêng hoặc có `approved_members: true` trong manifest; tài liệu riêng không tự được mở |
| Nhóm | Nhóm vẫn phải được cho phép; đăng ký riêng không mở quyền cho nhóm khác |

Không cần thêm thành viên đã duyệt vào `ZALO_ALLOWED_USERS` hoặc `ZALO_SHEETS_USERS` để dùng các chức năng thành viên trong chat riêng. Các ID cũ vẫn giữ nguyên; cấu hình trên Railway tiếp tục quản lý quản trị viên, nhóm, nguồn dữ liệu và các công tắc bật tính năng. Duyệt thành viên không cấp quyền quản trị hoặc mở tài liệu riêng. Chế độ Issue `managed` không cần tab Issues trên Sheets; xem [hướng dẫn Issue](ISSUES.md). `ZALO_ISSUES_ENABLED` chỉ điều khiển luồng Sheets cũ (`legacy`), không bật/tắt chế độ `managed`.

**Người đã duyệt từ bản trước không phải đăng ký hay duyệt lại.** Cập nhật và khởi động lại bot một lần để nạp cơ chế mới; các lần duyệt/thu hồi sau có hiệu lực ngay. Không cần tự chuyển dữ liệu đăng ký. Khi mở Tickets ở chế độ mới, bot tự khôi phục liên kết của các thành viên đã duyệt sau khi đối chiếu nguồn; người đổi cùng không cần mở bot trước.

**Mở đổi ca cho nhiều người:** dùng `lab_live` với giờ ca thực tế, quyền ghi và điều kiện không sửa bảng đồng thời như [hướng dẫn Tickets](tickets-chat.md#5-bật-lab_live-sau-khi-đủ-điều-kiện). Chế độ này dùng mọi thành viên đã duyệt, không giới hạn hai tên; kiểm tra trùng giờ và chỉ đổi hai ô Lab Coach đã xác nhận. `lab_live_test` vẫn giới hạn hai người để thử. Duyệt thêm người không tự đổi chế độ, bật ghi hoặc cấp vai trò quản lý.

Khi tính năng đăng ký đang bật, người đã có hồ sơ ở trạng thái **chờ duyệt, bị từ chối hoặc bị thu hồi** chỉ được dùng luồng đăng ký/xem trạng thái; các tính năng khác bị chặn dù ID vẫn nằm trong danh sách cấp quyền cũ. Lệnh quản trị đăng ký vẫn theo quyền quản trị riêng. Đăng ký lại sau khi thu hồi chưa mở quyền cho đến khi được duyệt lại.

Danh tính đã duyệt được dùng trước hồ sơ tên tự khai. `/dangky Họ tên` hoặc xóa hồ sơ cũ không đổi người đã liên kết. Nếu đăng ký nhầm người, quản trị xác minh rồi dùng **Sửa đăng ký nhầm** theo hướng dẫn trên; không mượn số của người khác.

Nếu hai người trùng toàn bộ họ tên, bot có thể lưu hai danh tính theo số điện thoại nhưng **chưa dùng tên đó để tra lịch cá nhân hoặc đổi ca**. Muốn phân biệt đúng lịch, mỗi người và từng phân công cần mã riêng trong dữ liệu; chỉ thêm hai mã vào danh bạ mà lịch vẫn ghi cùng một tên chưa đủ.

Khi `REGISTRATION_ENABLED=false`, luồng `/dangky Họ tên đầy đủ` cũ vẫn dùng để lọc lịch theo từng chat, không phải xác minh danh tính. Không hiểu đăng ký tên cũ là quyền nhắn riêng, quyền tài liệu hoặc quyền đổi ca.

## Thử trước khi mở cho nhóm

- Thành viên chưa đăng ký gọi `@Bot /menu` trong nhóm đã cấp quyền → nhận hướng dẫn đăng ký riêng bằng số hiện tại trên Workload, chưa được đọc dữ liệu nội bộ.
- Thành viên nhắn riêng `/dangky SỐ_ĐIỆN_THOẠI` của mình → khi tự duyệt bật và khớp duy nhất, bot báo thành công và đúng tên. Hồ sơ chờ/từ chối/thu hồi vẫn bị giới hạn; thử cả người đã có allowlist trước đó.
- Người không phải quản trị thử xem/duyệt → bị từ chối, không lộ danh sách.
- Sau đăng ký thành công → thành viên xem `/dangky trangthai`, thử `/menu`, “Mai mình có ca nào?” và mở `/doica` tại đúng chat riêng, không sửa `.env` cho người đó. Chỉ gửi yêu cầu đổi ca khi đã thống nhất một lần thử có thay đổi lịch thật.
- Người đã duyệt từ bản trước cũng đọc lịch được sau khi cập nhật/khởi động lại; tên trùng hoặc Workload thay đổi phải yêu cầu kiểm tra.
- Khi các chức năng tương ứng đã bật, kiểm tra nhắc gửi đúng chat và chỉ người tạo được xem/hủy. Với Issue `managed`, tạo một yêu cầu ghi rõ “Thử nghiệm” → xác nhận → Admin nhận xử lý/phản hồi → người gửi xem trạng thái; không cần sửa lịch để thử Issue.
- Quản trị xem `/thanhvien`; người thường không được xem danh sách hoặc tự cấp quyền quản trị. Tài liệu riêng và nhóm chưa cấp quyền vẫn bị chặn.
- Thu hồi liên kết → chặn tính năng và nhắc đang chờ, kể cả còn allowlist cũ; gửi đăng ký lại vẫn phải chờ duyệt. Bản nháp issue hoặc mã đổi ca cũ không được dùng để vượt quyền.

Chỉ kết luận luồng đăng ký đã chạy thật sau khi thử nhận tin, duyệt và kiểm tra quyền trên Zalo. Việc có cấu hình mẫu hoặc kiểm thử mô phỏng không thay thế bài thử này.
