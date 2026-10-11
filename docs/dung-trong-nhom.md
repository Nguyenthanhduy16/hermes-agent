# Dùng bot trong nhóm: lệnh và câu hỏi tự nhiên

## Thành viên bắt đầu

Trong nhóm được quản trị cho phép, gõ `@`, **chọn Bot LabCoach trong danh sách**, rồi nhập `/menu`: `@Bot LabCoach /menu`. Hoặc **trả lời một tin của bot** rồi nhập `/menu`. Bot chỉ nhận những tin được Zalo chuyển tới qua mention/reply, không đọc mọi tin trong nhóm. [Hướng dẫn Zalo](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Bạn có thể dùng lệnh, hỏi tự nhiên hoặc trò chuyện bình thường: `Giải thích Python là gì?`, `Viết giúp mình lời chúc sinh nhật`. Phần trò chuyện do Hermes trả lời. Lịch và quy định vẫn được tra từ nguồn đã cấp quyền; các bước đổi ca, nhắc việc và issue vẫn theo quy trình riêng.

Adapter nhận tên hiển thị (`display_name`) và tên tài khoản (`account_name`) từ Zalo, chỉ bỏ phần gọi tên bot ở đầu tin nhắn. Tên tài khoản nội bộ có thể khác tên hiển thị trong nhóm. Nếu đã cập nhật code mà `/menu` vẫn trả lời về tài liệu, dừng bot bằng **Ctrl+C**, chạy `Update` rồi `Start` để nạp bản sửa. Nếu đổi tên bot trên Zalo cũng cần khởi động lại để đọc tên mới.

| Muốn làm gì | Dùng lệnh | Hỏi tự nhiên |
| --- | --- | --- |
| Lịch chung | `/lich homnay` | `Hôm nay có lịch gì?` |
| Lịch theo phòng | `/lich homnay \| phong=H201` | `Hôm nay phòng H201 có lớp nào?` |
| Hướng dẫn đăng ký Workload, khi đã bật | `/dangky` — bot hướng dẫn nhắn riêng | Nhắn riêng `/dangky` để bắt đầu |
| Bộ lọc tên cũ, khi đăng ký Workload đang tắt | `/dangky Nguyễn Văn An` | `Tôi là Nguyễn Văn An` |
| Lịch của mình | `/lichcuatoi mai` | `Ngày mai mình có lịch gì?` |
| Ca của mình ngày mai | `/lichcuatoi mai` | `Mai mình có ca nào?`, `Ngày mai tôi trực không?` |
| Lịch cá nhân trong tuần | `/lichcuatoi tuan nay` | `Tuần này tôi dạy ca nào?` |
| Hỏi quy định | `/hoi Xin nghỉ phép cần báo ai?` | `Xin nghỉ phép cần báo ai?` |
| Xem tài liệu được chia sẻ | `/tailieu` | `Xem tài liệu` |
| Xem hướng dẫn nhóm | `/help nhom` | `hướng dẫn nhóm` |
| Xem menu bot | `/menu` | `Bot làm được gì?` |
| Hướng dẫn thông báo | `/thongbao` | `Xem thông báo` |

## Đăng ký thành viên bằng Workload

Nếu quản trị đã bật đăng ký bằng số điện thoại:

1. Mở hồ sơ bot → **Nhắn tin**, gửi `/dangky SỐ_ĐIỆN_THOẠI` đúng trong Workload. **Không gửi số điện thoại trong nhóm.**
2. Bot tự ghi nhận ID người gửi và chat riêng; bạn không cần tìm hoặc gửi ID cho quản trị.
3. Chờ quản trị xác minh và duyệt, rồi nhắn `/dangky trangthai` để xem kết quả. Bot chưa tự gửi thông báo duyệt.
4. Duyệt xong tự có quyền lịch, đổi ca, nhắc việc, góp ý và trò chuyện tại chat riêng đã liên kết, không thêm ID vào `.env`. Khi tên xác định duy nhất, hỏi `Mai mình có ca nào?` tại cuộc trò chuyện được cấp quyền.

Số điện thoại chỉ là thông tin đối chiếu, chưa chứng minh danh tính. Trong lúc chờ duyệt, bot không trả tên đầy đủ của người trong Workload. Đăng ký đã duyệt mở các chức năng thành viên tại chat riêng đã liên kết; issue cần bật toàn hệ thống và có tab đích hợp lệ, tài liệu chỉ mở khi được chia sẻ. Không tự mở nhóm mới hoặc vai trò quản trị. Trường hợp trùng toàn bộ tên, bot chặn ghép lịch cá nhân/đổi ca cho đến khi có cách phân biệt trong dữ liệu. Xem [hướng dẫn đầy đủ](dang-ky.md).

**Nếu đăng ký Workload đang tắt:** tên trong ví dụ là họ tên đầy đủ đúng trong Sheets. `/dangky Họ tên` chỉ lưu bộ lọc tên tự khai theo từng chat; `/hoso` xem tên, `/hoso xoa` xóa hồ sơ cũ. Hồ sơ còn sau khi khởi động lại nhưng không dùng chung giữa các nhóm hoặc giữa nhóm và nhắn riêng. Tên tự khai không xác minh danh tính hoặc cấp quyền dữ liệu. Khi đã có liên kết Workload được duyệt, hồ sơ tên cũ không ghi đè liên kết đó.

Lịch cá nhân chọn các buổi của người đã xác định ở Lab Coach/Key Coach; phản hồi vẫn chứa phân công của buổi học. Tin trả trong nhóm hiển thị cho cả nhóm.

`Ngày mai mình rảnh không?` cũng tra lịch đã phân công cho tên đăng ký. Không thấy ca trong bảng không có nghĩa bạn chắc chắn rảnh hoặc không có công việc khác.

Để đổi ca, nhắn riêng `Mình muốn đổi ca` để mở hoặc tiếp tục `/tickets`; `Ai đang chờ mình xác nhận?` mở `/choxacnhan`, `Xem yêu cầu đổi ca của mình` mở `/yeucau`. Trong nhóm, bot hướng dẫn chuyển sang chat riêng. `Nhắc lịch giúp mình` và yêu cầu tạo issue tự nhiên chỉ mở hướng dẫn; tạo nhắc có thời gian rõ ràng vẫn dùng mẫu `nhắc mình sau 10 phút: Gửi báo cáo`.

## Quyền của thành viên

Với `ZALO_GROUP_ACCESS=members`, thành viên không cần có tên trong danh sách user để hỏi tại **nhóm được cho phép**. Họ có thể đọc lịch/tài liệu được chia sẻ với nhóm, gửi góp ý và trò chuyện với Hermes khi @bot hoặc trả lời tin bot. Bộ lọc tên tự khai còn dùng khi đăng ký Workload đang tắt. Nhắc việc và issue vẫn cần quyền cá nhân riêng; đổi ca cần liên kết đã duyệt, tên phân biệt rõ và chế độ đã bật. Khi bật đăng ký Workload, người chưa có quyền chỉ được vào luồng đăng ký trong chat riêng; duyệt xong mở trò chuyện, lịch, đổi ca, nhắc việc, góp ý và issue khi đã bật tại đúng chat đó. Việc duyệt không mở rộng quyền ghi trong nhóm.

Phiên Hermes tách theo **người gửi và cuộc trò chuyện**, không dùng chung lịch sử giữa hai thành viên hoặc giữa các nhóm. Model không được mở công cụ để tự đọc dữ liệu riêng, sửa lịch, gửi tin thay bạn hoặc tạo issue. Tin trả lời trong nhóm vẫn hiện với mọi người trong nhóm.

Nhóm chưa được quản trị cho phép không được xử lý. Quyền nhóm và liên kết đăng ký riêng là hai phần độc lập; khi thành viên không còn được phép dùng bot, quản trị cần thu hồi liên kết và các quyền đã cấp riêng. Thành viên mới chỉ đọc tài liệu có quyền nhóm phù hợp; quyền cá nhân trên từng tài liệu vẫn được giữ nguyên.

Nếu cần quay lại cấp quyền từng người, đặt `ZALO_GROUP_ACCESS=allowlist` rồi khởi động lại.

## Quản trị chuẩn bị một lần cho mỗi nhóm

1. Mời bot bằng mục **Mời Bot vào nhóm** trong Bot Creator; trưởng nhóm xác nhận. Quyền nhóm còn phụ thuộc khả năng bot được Zalo cấp. [Tài liệu nhóm chính thức](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)
2. Nếu là nhóm mới, dừng bot và chạy `Discover`, sau đó một người gọi `@bot` trong nhóm để lấy `chat_id` (GROUP). `Discover` chỉ lấy ID, không gửi trả lời. Không chạy đồng thời với `Start`.
3. Trong `.env`, thêm ID nhóm vào các danh sách thích hợp, giữ lại các ID cũ:

```dotenv
ZALO_GROUP_ACCESS=members
ZALO_ALLOWED_GROUPS=ID_NHOM_DUOC_PHEP
ZALO_SHEETS_GROUPS=ID_NHOM_DUOC_DOC_LICH
```

Nhiều ID cách nhau dấu phẩy. Không dùng `*`. `ZALO_ALLOWED_USERS` giữ quản trị và quyền cá nhân cũ; không cần thêm thành viên Workload đã duyệt để dùng các chức năng trong chat riêng. Với lịch Lab, quyền nhóm cho phép đọc các tab đã cấu hình. `ZALO_SHEETS_USERS` vẫn dùng cho quyền cá nhân cũ và quyền thao tác trong nhóm; `ZALO_ISSUES_ENABLED=false` tiếp tục tắt issue.

4. Nếu muốn thành viên tự đăng ký bằng số điện thoại, làm theo [cấu hình Workload và quản trị duyệt](dang-ky.md). Chỉ cần chuẩn bị ID quản trị; ID từng thành viên được bot thu tự động khi họ đăng ký riêng. Duyệt tự cấp các quyền thành viên trong chat riêng theo tính năng đã bật, lưu trong dữ liệu đăng ký. Quản lý bằng `/thanhvien` và `/thuhoidangky MÃ_THÀNH_VIÊN` trong chat riêng; không sửa `.env` hoặc khởi động lại cho từng người.
5. Cấp tài liệu quy định cho nhóm theo phần dưới.
6. Chạy `Update`, `Check`, rồi `Start`. Mỗi lần sửa `.env` cần dừng/bật lại bot; sửa nội dung tài liệu hoặc manifest không cần bật lại.

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 Check
.\scripts\run.ps1 Start
```

## Thêm quy định thật

Đặt file `.md` hoặc `.txt` UTF-8 trong `data/knowledge/`, ví dụ `quy-dinh-lab.md`. Trong `data/knowledge/manifest.json`, **thêm** một mục vào danh sách `documents`, giữ các mục cũ:

```json
{
  "id": "quy-dinh-lab",
  "title": "Quy định Lab Coach",
  "path": "quy-dinh-lab.md",
  "users": [],
  "groups": ["ID_NHOM_DUOC_DOC_QUY_DINH"]
}
```

`users: []` và `groups` có ID nghĩa là chia sẻ nội dung cho thành viên nhóm đó; chưa cho đọc trong chat riêng. Nếu tài liệu này cũng dành cho mọi thành viên đã duyệt qua chat riêng, thêm `"approved_members": true` vào đúng mục tài liệu. Cờ này mặc định `false`; không tự mở tài liệu riêng hoặc thay quyền nhóm. Nếu `users` có ID, chỉ những người đó trong nhóm được hỏi tài liệu. Chỉ chia sẻ tài liệu phù hợp cho cả nhóm vì mọi người có thể nhìn thấy câu trả lời.

Bot trích đoạn từ tài liệu được cấp quyền và nêu nguồn. Chưa có tài liệu/không tìm thấy thông tin thì báo rõ. **Lịch Sheets không tự chứa quy định**, và sổ tay mẫu không thay thế quy định thật. Câu hỏi quy định hoặc công việc nội bộ được nhận diện sẽ tra dữ liệu được cấp quyền; câu chưa khớp lệnh nhưng vẫn có từ ngữ công việc nội bộ tiếp tục tra tài liệu. Không để model tự suy diễn chính sách. Hermes xử lý trò chuyện thông thường trong cả nhóm được phép và chat riêng đã cấp quyền.

## Các bài thử cần đạt

- Một thành viên chưa có trong `ZALO_ALLOWED_USERS` trả lời bot `/menu` trong nhóm đã cho phép → có hướng dẫn; nhóm khác vẫn bị chặn. Khi đã bật đăng ký Workload, chat riêng chưa duyệt chỉ được đăng ký/xem trạng thái, chưa mở trò chuyện chung.
- Người A đăng ký riêng, quản trị xác minh và duyệt rồi thử hỏi lịch cá nhân/đổi ca trong phạm vi đã bật, không thêm ID riêng vào `.env`. Người B chưa đăng ký hoặc chưa duyệt không được dùng danh tính A.
- Gửi `/dangky` trong nhóm → hướng dẫn sang chat riêng, không công khai tên đối chiếu hoặc số điện thoại.
- Hỏi quy định có trong file được chia sẻ → có trích đoạn và nguồn; hỏi quy định không có hoặc tài liệu riêng → không bịa và không lộ dữ liệu.
- Khi dùng bộ lọc tên cũ: đổi/xóa hồ sơ nhóm A không đổi nhóm B. Khi dùng danh tính đã duyệt: hồ sơ tự khai không thay được liên kết đó.
- Thành viên chỉ có quyền nhóm thử tạo nhắc/issue trong nhóm → bot vẫn yêu cầu quyền cá nhân đã cấu hình. Thành viên đã duyệt có thể tạo nhắc trong chat riêng; issue còn cần tính năng được bật và tab đích hợp lệ.
- Thành viên trong nhóm được phép @bot hỏi kiến thức chung hoặc lời chúc → Hermes trả lời; hỏi quy định nội bộ → vẫn tra tài liệu và nêu nguồn.
- Hai thành viên trò chuyện riêng với bot trong cùng nhóm → không dùng lẫn lịch sử phiên; lời yêu cầu sửa lịch không được model tự thực hiện.

Ngôn ngữ tự nhiên dùng các mẫu nhận diện rõ ràng, không phải mọi cách diễn đạt đều được hiểu. Khi thời gian mơ hồ (ví dụ “thứ hai”, “sắp tới”), bot hướng dẫn nhập ngày cụ thể. Lệnh `/lich` và `/hoi` luôn là cách dùng rõ ràng khi câu tự nhiên chưa được nhận đúng. Không có luồng tự thực thi xóa/sửa lịch hoặc xác nhận issue từ câu nói tự do.
