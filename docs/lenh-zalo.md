# Các lệnh dùng trong Zalo

Gửi `/menu` để xem **Tra lịch**, **Lịch của tôi**, **Đổi ca**, **Nhắc lịch**, **Thông báo** và **Tạo issue** theo cấu hình và quyền. Mục **Đổi ca** dùng `/doica` để vào chọn ca ngay. Thành viên đã duyệt dùng nhắc lịch trong chat riêng; issue cần bật toàn hệ thống và có tab đích hợp lệ, hiện **(chưa bật)** nếu đang tắt. Trong nhóm, @bot hoặc trả lời một tin của bot rồi nhập lệnh. Thời gian luôn theo **giờ Việt Nam**.

Tài liệu/quy định và góp ý vẫn ẩn khỏi menu chính; các lệnh chi tiết bên dưới vẫn được giữ. Hiển thị một mục hướng dẫn không tự cấp thêm quyền, tạo nhắc việc hoặc bật ghi issue.

**Dùng trong nhóm:** xem [hướng dẫn thành viên và quản trị](dung-trong-nhom.md). Nếu đã bật đăng ký bằng Workload, nhắn **riêng** bot `/dangky SỐ_ĐIỆN_THOẠI` và chờ quản trị duyệt; không gửi số vào nhóm. Sau khi được duyệt, tự có quyền lịch, đổi ca, nhắc việc, góp ý và trò chuyện trong chat riêng, không thêm ID vào `.env`; hỏi `Ngày mai mình có lịch gì?` hoặc `/lichcuatoi mai`. Nếu tính năng đăng ký đang tắt, vẫn dùng bộ lọc tên cũ `/dangky Họ tên đầy đủ`. Có thể hỏi trực tiếp `Xin nghỉ phép cần báo ai?` để tra tài liệu đã chia sẻ.

## Hỏi tự nhiên và trò chuyện

Trong nhóm được cho phép, **@bot hoặc trả lời tin bot** rồi hỏi. Với chế độ `members`, thành viên cũng có thể trò chuyện thông thường, như `Giải thích Python là gì?` hoặc `Viết giúp mình lời chúc sinh nhật`. Hermes trả lời phần trò chuyện; lịch, quy định và thao tác công việc vẫn đi qua dữ liệu và quyền đã cấu hình. Phiên tách theo người và chat; model không được tự chạy công cụ hoặc thay dữ liệu.

| Câu bạn có thể nhắn | Bot xử lý |
| --- | --- |
| `Mai mình có ca nào?`, `Ngày mai tôi trực không?` | Tra lịch của tên đã đăng ký |
| `Ngày mai mình rảnh không?` | Trả lịch đã phân công; không khẳng định bạn rảnh ngoài dữ liệu Sheets |
| `Mình muốn đổi ca` | Mở menu hoặc tiếp tục bước đổi ca: `/tickets` |
| `Ai đang chờ mình xác nhận?` | `/choxacnhan`, dùng trong chat riêng |
| `Xem yêu cầu đổi ca của mình` | `/yeucau`, dùng trong chat riêng |
| `Xem thông báo` | `/thongbao` |
| `Bot làm được gì?` | `/menu` |
| `Nhắc lịch giúp mình` | Hướng dẫn `/nhac`, chưa tạo nhắc |
| `Xem các nhắc lịch của tôi` | `/nhac xem`, trong chat hiện tại |

Yêu cầu tạo issue bằng lời tự nhiên chỉ mở hướng dẫn; vẫn cần gửi nội dung và xác nhận bản nháp. Trong nhóm, câu hỏi có từ ngữ công việc nội bộ chưa nhận được lệnh phù hợp sẽ tra tài liệu được cấp quyền, không để model tự suy diễn quy định. Các mẫu nhận diện có giới hạn; dùng lệnh rõ ràng khi bot chưa hiểu đúng.

## Bắt đầu

| Bạn muốn | Nhắn cho bot |
| --- | --- |
| Xem menu | `/menu` hoặc `menu` |
| Tra lịch hôm nay | `/lich homnay` |
| Lịch của tôi | `/lichcuatoi homnay` |
| Đăng ký thành viên khi đã bật Workload | `/dangky SỐ_ĐIỆN_THOẠI` — chỉ nhắn riêng |
| Xem trạng thái đăng ký | `/dangky trangthai` hoặc `/trangthaidangky` — chỉ nhắn riêng |
| Bộ lọc tên cũ, khi đăng ký Workload đang tắt | `/dangky Họ tên đầy đủ` |
| Hướng dẫn tra lịch | `/help lich` |
| Menu đổi ca trong tin nhắn riêng | `/tickets` |
| Bắt đầu yêu cầu đổi ca | `/doica` |
| Hướng dẫn đổi ca | `/help tickets` |
| Thông báo: lịch tự động khi đã bật, nhắc tự đặt và yêu cầu đổi ca | `/thongbao` |
| Tạo issue: hướng dẫn lập bản nháp | `/issue` |

`/trogiup` và `/help` mở cùng menu gọn. Hướng dẫn riêng theo tính năng vẫn báo rõ phần chưa kết nối, chưa bật hoặc chưa có quyền. Lệnh gõ sai như `/lichh` được hướng dẫn trở về menu.

## Đăng ký bằng số điện thoại

Mở chat riêng với bot, gửi `/dangky` để xem hướng dẫn, rồi `/dangky SỐ_ĐIỆN_THOẠI` với số của bạn trong Workload. Bot tự thu ID người gửi và chat riêng, trả mã yêu cầu `DK-…`; chưa cấp danh tính chỉ vì số có trong bảng. Quản trị phải xác minh người đăng ký rồi duyệt. Khi đang chờ, bot không trả tên người khớp số hoặc số điện thoại đầy đủ.

Gửi `/dangky trangthai` hoặc `/trangthaidangky` để xem kết quả; chưa có thông báo duyệt tự động. Duyệt xong tự mở lịch, đổi ca, nhắc việc, góp ý và trò chuyện tại đúng chat riêng đã liên kết, không thêm ID vào `.env`. Issue cần bật toàn hệ thống và chuẩn bị tab đích; tài liệu cần quyền riêng hoặc `approved_members: true`. Quyền quản trị và quyền nhóm không thay đổi. Tên trùng chưa được phân biệt trong lịch sẽ không được dùng để tra lịch cá nhân/đổi ca. Danh tính đã duyệt được ưu tiên hơn hồ sơ tên tự khai cũ.

Quản trị dùng các lệnh dưới đây **trong chat riêng**:

| Việc cần làm | Lệnh |
| --- | --- |
| Xem đăng ký chờ, 5 mục/trang | `/dangkychoduyet` hoặc `/dangkychoduyet 2` |
| Duyệt sau khi xác minh người gửi | `/duyetdangky DK-MÃ_YÊU_CẦU` |
| Từ chối yêu cầu | `/tuchoidangky DK-MÃ_YÊU_CẦU` |
| Xem thành viên đã duyệt | `/thanhvien` hoặc `/thanhvien 2` |
| Thu hồi liên kết đã duyệt | `/thuhoidangky MÃ_THÀNH_VIÊN` |

Dùng nguyên mã bot cung cấp. Danh sách quản trị có số đã che, chỉ giữ 4 số cuối, và ID người/chat bot đã thu nhận; không cần hỏi thành viên gửi ID. Xem [hướng dẫn cấu hình, phân quyền và thử](dang-ky.md).

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

Tên người ở ví dụ là tên minh họa; thay bằng tên trong bảng. `coach` tìm cả **Lab Coach và Key Coach**, không phân biệt hoa/thường hay dấu tiếng Việt. Có thể nhập một phần tên theo từ, ví dụ `Văn An`; tên đầy đủ giúp phân biệt khi chỉ trùng một phần tên. Nếu trùng toàn bộ tên, cần mã người riêng trong phân công để xác định đúng người.

Khi đăng ký Workload đang tắt, `/dangky Họ tên đầy đủ` lưu tên tự khai tại từng chat; `/hoso` xem tên, `/hoso xoa` xóa hồ sơ cũ. Khi danh tính Workload đã được duyệt, hồ sơ tên cũ không ghi đè liên kết đó. Bộ lọc tên cũ không cấp quyền dữ liệu; đăng ký Workload đã duyệt tự cấp các quyền thành viên trong chat riêng theo phạm vi đã bật.

Bot trả tối đa **5 mục mỗi trang**: buổi, khóa/session, phòng, Key Coach và danh sách tên Lab Coach. Tin lịch không kèm trạng thái từng người, dòng nguồn hoặc link Sheets. Nếu còn kết quả, bot hiện số trang và gửi sẵn một lệnh dưới chữ **Xem tiếp**; sao chép nguyên lệnh đó để giữ ngày và bộ lọc. Ví dụ:

```text
/lich 2026-10-06 | khoa=Track 1 | trang=2
```

Mỗi lần tra đọc lại bảng: nếu có người sửa lịch giữa hai lần xem trang, kết quả có thể thay đổi. Ngày thiếu dữ liệu được báo rõ; bot không coi đó là chắc chắn rảnh. Ô trống được báo “Chưa ghi”; lịch cùng ngày ở nhiều tab vẫn được liệt kê riêng. Trạng thái gốc trong Sheets được giữ nguyên.

## Đổi ca trong tin nhắn riêng

Mở chat riêng với bot, dùng **Đổi ca** trong `/menu` hoặc gửi `/doica` để bắt đầu; `/tickets` vẫn dùng để xem menu hoặc tiếp tục bước đang nhập. Bot hướng dẫn chọn ca của bạn → người đổi cùng → ca của người đó → lý do → xem trước và xác nhận. Chỉ ca **chưa bắt đầu** được chọn. Khi bot đưa danh sách hoặc lệnh xác nhận, trả lời theo đúng hướng dẫn ở bước hiện tại; không tự sửa mã trong lệnh.

| Bạn muốn | Lệnh |
| --- | --- |
| Xem menu đổi ca | `/tickets` |
| Tạo yêu cầu theo từng bước | `/doica` |
| Chuyển trang trong danh sách đang chọn | `/doica trang 2` — dùng số trang bot đưa ra; hoặc `tiếp`/`trước` |
| Xem danh sách yêu cầu của mình | `/yeucau` |
| Xem chi tiết một yêu cầu được phép | `/yeucau MÃ_YÊU_CẦU` |
| Xem các yêu cầu đang chờ mình đồng ý | `/choxacnhan` |
| Xem lịch sử đổi ca | `/lichsudoica` |
| Xem toàn bộ thao tác của một yêu cầu | `/lichsudoica MÃ_YÊU_CẦU trang 1` |
| Quản lý xem yêu cầu chờ duyệt, nếu bật chế độ này | `/choduyet` |
| Đồng ý, từ chối hoặc hủy | `/dongy`, `/tuchoi`, `/huydoica` — dùng lệnh đầy đủ bot gửi |
| Quản lý duyệt hoặc kiểm tra kết quả chưa rõ | `/duyet`, `/doisoat` — dùng lệnh đầy đủ bot gửi |

Luồng `lab_live` cho **mọi thành viên đã duyệt và liên kết đúng lịch**, với `TICKETS_REQUIRE_MANAGER=false`: **A tạo → bot báo riêng cho B → B đồng ý → đổi hai ô Lab Coach ở cột D**, không có bước quản lý duyệt. B đọc hai ca và gửi `/dongy MÃ_4_SỐ` hoặc `/tuchoi MÃ_4_SỐ` đúng như bot cung cấp. Mọi chế độ đều chỉ cho đổi ca chưa bắt đầu. Trong `lab_live_test`, ca hôm nay cần giờ rõ ràng trong ô Buổi; thiếu giờ thì chỉ chọn từ ngày mai trở đi. Chế độ này bỏ kiểm tra trùng ca nhưng **vẫn ghi lịch thật**. Không sửa bảng đồng thời. “Đồng ý” bất kỳ không thay thế xác nhận cho một yêu cầu cụ thể. Các lệnh `/choduyet`, `/duyet` chỉ cần nếu quản trị bật chế độ có quản lý ở đợt dùng khác.

Khi tạo: nhập số chọn trong **danh sách mới nhất**, không dùng lại số của bước trước. Dùng lệnh trang trước/trang tiếp bot hiển thị, như `/doica trang 2`, hoặc nhắn `tiếp`/`trước`; chuyển trang giữ bước đang chọn. Nhắn `tìm tên hoặc từ khóa` để lọc, `quay lại` hoặc `thoát` khi cần. Đọc bản xem trước rồi sao chép `/doica gui MÃ_4_SỐ`. `/doica` không kèm trang bắt đầu lại bản nháp, không hủy yêu cầu đã gửi.

Các lệnh đồng ý, từ chối, hủy, duyệt và đối soát dùng mã bốn chữ số, ví dụ `/dongy 1234`; sao chép **đúng mã bot gửi**, không dùng mã ví dụ. Mỗi bước nhập, bản xem trước và mã xác nhận có tối đa **15 phút**; mã hết hạn, sai người/chat hoặc đã dùng không đổi lịch. Yêu cầu đã gửi chờ tối đa **48 giờ** và hết hạn sớm hơn khi ca bắt đầu; ca thiếu giờ trong chế độ thử chỉ xử lý trước ngày diễn ra. Danh sách dài dùng `/yeucau trang 2` hoặc lệnh xem tiếp của bot; các danh sách chờ và lịch sử cũng hỗ trợ `trang 2`.

Chi tiết yêu cầu hiển thị gọn hai ca nhận sau khi đổi và lệnh thao tác. Lịch sử nằm riêng ở `/lichsudoica MÃ_YÊU_CẦU`, mỗi trang tối đa 5 thao tác, mới nhất trước; sao chép lệnh trang tiếp bot đưa ra khi cần.

Chỉ A, B và quản lý được xem yêu cầu theo quyền đã cấp. Quản trị phải xác minh và liên kết đúng **người gửi + cuộc chat riêng + bot**. Đăng ký Workload đã duyệt tự có quyền thành viên dùng Tickets, không cần thêm ID vào các danh sách quyền bot/Sheets. Vẫn phải đúng nguồn, tên duy nhất và đủ điều kiện chế độ đang chạy; `lab_live` không giới hạn hai người, còn `lab_live_test` giữ đúng hai tên cấu hình. Tên tự khai trong luồng cũ không cấp quyền đổi ca. Trong nhóm, bot chỉ hướng dẫn chuyển sang nhắn riêng.

Không cần mở web hoặc Mini App. B cần đã nhắn riêng và được liên kết đúng với bot để nhận thông báo yêu cầu mới. Không thấy tin hoặc mã hết hạn: dùng `/choxacnhan`. Các yêu cầu cũ không được tự gửi lại thông báo; gửi tin chưa rõ kết quả cũng không tự phát lại. Bản này dùng lệnh văn bản, chưa có nút bấm đồng ý/từ chối. Chế độ `lab_live` chỉ sửa D của hai ca đã xác nhận, không sửa Key Coach; `sheets_test` vẫn dùng bảng thử riêng. Nếu ghi chưa rõ kết quả ở luồng hai người, A hoặc B dùng `/doisoat` theo lệnh bot gửi để đọc lại, không tự ghi lại. Xem [hướng dẫn thành viên và quản trị](tickets-chat.md).

## Nhắc lịch tự động và nhắc tự đặt

Mục `/nhac` đã ẩn khỏi menu chính. Khi quản trị bật nhắc lịch tự động, thành viên đã đăng ký được nhắn riêng **20:00 tối hôm trước** và **30 phút trước mỗi ca** theo lịch Lab Coach. Thành viên không cần tạo từng nhắc. Tin gần giờ học chỉ nhắc chuẩn bị check-in, chưa ghi nhận điểm danh. Xem [chi tiết và cấu hình](nhac-lich-tu-dong.md).

Lệnh `/nhac` và các nhắc tự đặt cũ vẫn hoạt động theo quyền hiện có. Thành viên đã duyệt được tạo, xem và hủy nhắc của mình tại chat riêng đã liên kết. `/nhac`, hoặc `Nhắc lịch giúp mình`, mở hướng dẫn; `Xem các nhắc lịch của tôi` mở danh sách **nhắc tự đặt** đang chờ trong chat hiện tại. Danh sách này không bao gồm thông báo lịch tự động.

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

## Thông báo

`/thongbao` mở hướng dẫn tra các mục bạn được phép dùng:

- Khi đã bật: giờ tổng hợp lịch ngày mai và thời điểm nhắc trước ca qua tin nhắn riêng.
- `/nhac xem`: nhắc đang chờ của chính bạn tại chat hiện tại.
- `/nhac lichsu`: lịch sử nhắc của chính bạn tại chat hiện tại.
- `/choxacnhan`: yêu cầu đổi ca đang chờ bạn, dùng trong **chat riêng** với bot. Nếu đang ở nhóm, chuyển sang nhắn riêng.

Đây là trang hướng dẫn bằng tin nhắn, không có số thông báo chưa đọc hay thao tác đánh dấu đã đọc. Mở `/thongbao` không tạo nhắc mới hoặc tự gửi thông báo cho người khác.

## Tạo issue

**Issue đang tắt và bảng hiện tại chưa có tab `Issues`.** Thành viên đã duyệt nhắn riêng hoặc người có quyền Sheets sẽ thấy **Tạo issue: `/issue` (chưa bật)**; mở mục này để xem trạng thái. Khi quản trị bật tính năng, gửi:

```text
/issue tao Lỗi nút lưu | Bấm Lưu nhưng dữ liệu chưa cập nhật
```

Bot trả bản nháp ghi rõ nội dung và nơi nhận. Chính người tạo kiểm tra rồi gửi `/issue xacnhan MÃ_BẢN_NHÁP` tại cùng chat trong **10 phút**, hoặc `/issue huy MÃ_BẢN_NHÁP`. Dùng đúng mã bot cấp; câu “đồng ý” không tạo issue. Bản nháp chưa ghi vào Sheets.

**Quản trị bật trên bảng hiện tại:**

1. Chuẩn bị tab `Issues` tách khỏi các tab lịch. Dòng 1 có đúng tám cột A–H theo thứ tự: `id`, `created_at`, `title`, `description`, `status`, `user_id`, `chat_id`, `chat_type`. Xem [mẫu Sheets và quyền Google](huong-dan-tinh-nang.md).
2. Bảo đảm tài khoản dịch vụ có quyền **Editor**. Thành viên đã duyệt được tạo issue trong chat riêng đã liên kết, không thêm ID vào `.env`. Người dùng cách cấp quyền cũ cần cả `ZALO_ALLOWED_USERS` và `ZALO_SHEETS_USERS`; trong nhóm vẫn giữ nguyên quyền người dùng/nhóm hiện có.
3. Trong `.env`, đặt `ZALO_ISSUES_TAB=Issues` và `ZALO_ISSUES_ENABLED=true`, rồi dừng/bật lại bot. Việc bổ sung menu không tự thay các giá trị này.

## Tài liệu và góp ý

```text
/tailieu
/hoi Giờ làm việc của nhóm là khi nào?
/phanhoi Lịch ngày 06/10 hiển thị thiếu phòng
```

`hỏi tài liệu Giờ làm việc của nhóm là khi nào?` cũng dùng được. Tài liệu là file `.md`/`.txt` đã cấp quyền: quản trị đặt `approved_members: true` trong manifest cho tài liệu dùng chung để mọi thành viên đã duyệt tra qua chat riêng. Mặc định `false`, không tự mở file riêng hoặc quyền nhóm. Lịch Google Sheets dùng `/lich`. Thành viên đã duyệt gửi góp ý bằng `/phanhoi`; `/gopy` là tên khác của lệnh này.

Các lệnh và mẫu câu được liệt kê ở đây được xử lý trực tiếp. Trò chuyện thông thường qua Hermes trong chat riêng đã cấp quyền hoặc nhóm được phép khi @bot/trả lời tin bot. Không phải mọi câu tiếng Việt đều được nhận thành lệnh; không dùng lời tự do để thay bước xác nhận đổi ca hoặc issue.
