# Dùng bot trong nhóm: lệnh và câu hỏi tự nhiên

## Thành viên bắt đầu

Trong nhóm được quản trị cho phép, gõ `@`, **chọn Bot LabCoach trong danh sách**, rồi nhập `/menu`: `@Bot LabCoach /menu`. Hoặc **trả lời một tin của bot** rồi nhập `/menu`. Khi đăng ký Workload đang bật, người chưa đăng ký nhận hướng dẫn nhắn riêng `/dangky SỐ_ĐIỆN_THOẠI`; dùng số hiện tại trên Workload, không gửi số trong nhóm. Đăng ký thành công và đúng tên thì quay lại nhóm hỏi bot. Bot chỉ nhận những tin được Zalo chuyển tới qua mention/reply, không đọc mọi tin trong nhóm. [Hướng dẫn Zalo](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Bạn có thể dùng lệnh hoặc hỏi tự nhiên về **công việc Lab và tài liệu được chia sẻ**. Câu ngoài phạm vi như `Giải thích Python là gì?`, `Viết giúp mình lời chúc sinh nhật` nhận hướng dẫn `/menu`; bot không mở trò chuyện tự do. Lịch và quy định phải có nguồn được cấp quyền; đổi ca, nhắc việc và Issue vẫn theo quy trình riêng.

Adapter nhận tên hiển thị (`display_name`) và tên tài khoản (`account_name`) từ Zalo, chỉ bỏ phần gọi tên bot ở đầu tin nhắn. Nếu đổi tên bot, cần khởi động lại nơi đang vận hành để đọc tên mới. Khi dùng Railway, cập nhật/khởi động lại service trên Railway và giữ bot Windows tắt.

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
3. Khi bật tự duyệt, số khớp duy nhất Workload sẽ nhận ngay “Đăng ký thành công”. Nếu dùng chế độ duyệt thủ công hoặc từng bị từ chối/thu hồi, quản trị cần duyệt. Nhắn `/dangky trangthai` để xem kết quả.
4. Duyệt xong tự có quyền lịch, đổi ca, nhắc việc, góp ý và trò chuyện tại chat riêng đã liên kết, không thêm ID vào `.env`. Khi tên xác định duy nhất, hỏi `Mai mình có ca nào?` tại cuộc trò chuyện được cấp quyền.

Số điện thoại chỉ là thông tin đối chiếu, chưa chứng minh người gửi sở hữu số đó; tự duyệt được bật bằng `REGISTRATION_AUTO_APPROVE=true`. Đăng ký thành công mở các chức năng thành viên tại chat riêng đã liên kết. Issue `managed` cần được bật và đối soát một lần, lưu trên Railway và xử lý trên Admin; không cần tab Issues trên Sheets. Tài liệu chỉ mở khi được chia sẻ. Không tự mở nhóm mới hoặc vai trò quản trị. Trường hợp trùng toàn bộ tên, bot chặn ghép lịch cá nhân/đổi ca cho đến khi có cách phân biệt trong dữ liệu. Nếu đăng ký thành công nhưng hiện sai tên, báo Admin dùng **Sửa đăng ký nhầm**, chưa dùng lịch cá nhân/đổi ca. Xem [hướng dẫn đầy đủ](dang-ky.md).

**Nếu đăng ký Workload đang tắt:** tên trong ví dụ là họ tên đầy đủ đúng trong Sheets. `/dangky Họ tên` chỉ lưu bộ lọc tên tự khai theo từng chat; `/hoso` xem tên, `/hoso xoa` xóa hồ sơ cũ. Hồ sơ còn sau khi khởi động lại nhưng không dùng chung giữa các nhóm hoặc giữa nhóm và nhắn riêng. Tên tự khai không xác minh danh tính hoặc cấp quyền dữ liệu. Khi đã có liên kết Workload được duyệt, hồ sơ tên cũ không ghi đè liên kết đó.

Lịch cá nhân chọn các buổi của người đã xác định ở Lab Coach/Key Coach; phản hồi vẫn chứa phân công của buổi học. Tin trả trong nhóm hiển thị cho cả nhóm.

`Ngày mai mình rảnh không?` cũng tra lịch đã phân công cho tên đăng ký. Không thấy ca trong bảng không có nghĩa bạn chắc chắn rảnh hoặc không có công việc khác.

Để đổi ca, nhắn riêng `Mình muốn đổi ca` hoặc `/doica`; `Ai đang chờ mình xác nhận?` mở `/choxacnhan`, `Xem yêu cầu đổi ca của mình` mở `/yeucau`. Bot hướng dẫn chọn ca/người từ lịch thật, xem trước và xác nhận; người đổi cùng đồng ý rồi Admin duyệt mới cập nhật lịch. AI không tự chọn hoặc duyệt ca. Trong nhóm, bot hướng dẫn chuyển sang chat riêng.

Với Issue, nhắn riêng `Mình muốn báo lỗi bot` hoặc `Mình muốn đề xuất tính năng`; `/issue` luôn mở hướng dẫn. Khi AI hiểu đủ nội dung, bot có thể chuẩn bị bản nháp, nhưng vẫn cần lệnh xác nhận trước khi tạo. Thiếu thông tin thì gửi lại câu đầy đủ theo hướng dẫn. Xem [các lệnh Issue](ISSUES.md#2-thành-viên-gửi-yêu-cầu).

## Quyền của thành viên

Với `ZALO_GROUP_ACCESS=members` và đăng ký Workload đang bật, thành viên đăng ký thành công không cần thêm vào danh sách user để hỏi tại **nhóm được cho phép**. Họ đọc lịch/tài liệu được chia sẻ với nhóm khi @bot hoặc trả lời tin bot. Người chưa đăng ký nhận hướng dẫn đăng ký riêng, chưa được dùng dữ liệu nội bộ hoặc AI. Đổi ca cần danh tính/ánh xạ rõ và tính năng đã bật; đổi ca, đăng ký và Issue thực hiện qua tin nhắn riêng. Việc duyệt không mở rộng quyền ghi trong nhóm. Bộ lọc tên tự khai chỉ còn dùng khi đăng ký Workload đang tắt.

Danh tính và trạng thái xử lý tách theo **người gửi và cuộc trò chuyện**. AI phân loại chỉ nhận câu hiện tại, không nhận lịch sử/ghi nhớ, toàn bộ lịch hoặc danh bạ. Model không được mở công cụ để tự đọc dữ liệu riêng, sửa lịch, gửi tin thay bạn hoặc tạo issue. Tin trả lời trong nhóm vẫn hiện với mọi người trong nhóm.

Nhóm chưa được quản trị cho phép không được xử lý. Quyền nhóm và liên kết đăng ký riêng là hai phần độc lập; khi thành viên không còn được phép dùng bot, quản trị cần thu hồi liên kết và các quyền đã cấp riêng. Thành viên mới chỉ đọc tài liệu có quyền nhóm phù hợp; quyền cá nhân trên từng tài liệu vẫn được giữ nguyên.

Nếu cần quay lại cấp quyền từng người, đặt `ZALO_GROUP_ACCESS=allowlist` rồi khởi động lại.

## Quản trị chuẩn bị một lần cho mỗi nhóm

1. Mời bot bằng mục **Mời Bot vào nhóm** trong Bot Creator; trưởng nhóm xác nhận. Quyền nhóm còn phụ thuộc khả năng bot được Zalo cấp. [Tài liệu nhóm chính thức](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)
2. Nếu chưa có ID nhóm, người vận hành tạm đặt **`BOT_START_ENABLED=false` trong Railway Variables** và đợi deployment dừng polling. Trên Windows có đúng Bot Token, chạy `.\scripts\run.ps1 Discover`, rồi gửi `@Bot LabCoach /menu` trong nhóm. Lấy `chat_id` có `chat_type: GROUP`; Discover không trả lời và kết thúc sau khoảng 60 giây. Dừng Discover trước khi bật lại Railway. **Không chạy hai tiến trình nhận tin cùng lúc.** `/ketnoinhom` không phải lệnh thường trực của phiên bản này.
3. Trong **Railway Variables**, thêm ID nhóm vào cả hai danh sách dưới đây, giữ các ID cũ:

```dotenv
ZALO_GROUP_ACCESS=members
ZALO_ALLOWED_GROUPS=ID_NHOM_DUOC_PHEP
ZALO_SHEETS_GROUPS=ID_NHOM_DUOC_DOC_LICH
```

Nhiều ID cách nhau dấu phẩy. Không dùng `*` hoặc ID chat riêng làm ID nhóm. `ZALO_ALLOWED_USERS` giữ quản trị và quyền cá nhân cũ; không thêm từng thành viên Workload đã duyệt. Với lịch Lab, quyền nhóm cho phép đọc các tab đã cấu hình, nên chỉ cấp khi nội dung được phép hiển thị cho cả nhóm. Giữ nguyên chế độ Issue đang dùng; `ZALO_ISSUES_MODE=managed` dùng Admin, `off` tắt Issue; `ZALO_ISSUES_ENABLED` chỉ dành cho luồng Sheets cũ.

4. Giữ `REGISTRATION_ENABLED=true`, `REGISTRATION_AUTO_APPROVE=true` và nguồn Workload đã kiểm tra theo [hướng dẫn đăng ký](dang-ky.md). ID từng thành viên được bot thu tự động khi họ đăng ký riêng. Admin quản lý tại **Thành viên**, có **Sửa đăng ký nhầm** và khóa/mở quyền; không thêm từng người vào Variables.
5. Cấp tài liệu quy định cho nhóm theo phần dưới.
6. Đặt lại **`BOT_START_ENABLED=true` trên Railway**, triển khai các Variables và đợi deployment thành công; giữ bot Windows tắt. Gửi một tin mới `@Bot LabCoach /menu`, rồi `@Bot LabCoach Hôm nay có lịch gì?`. Tin đã được Discover nhận sẽ không được bot xử lý lại. Nếu chỉ chạy bot trên Windows, áp dụng các danh sách trong `.env`, dừng Discover rồi chạy `Update`, `Check`, `Start`; không đồng thời bật Railway.

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

Bot trích đoạn từ tài liệu được cấp quyền và nêu nguồn. Chưa có tài liệu/không tìm thấy thông tin thì báo rõ. **Lịch Sheets không tự chứa quy định**, và sổ tay mẫu không thay thế quy định thật. Câu hỏi quy định hoặc công việc nội bộ được nhận diện sẽ tra dữ liệu được cấp quyền. Không chuyển câu thiếu nguồn sang model trả lời chung.

## Các bài thử cần đạt

- Một thành viên chưa có trong `ZALO_ALLOWED_USERS` trả lời bot `/menu` trong nhóm đã cho phép → có hướng dẫn; nhóm khác vẫn bị chặn. Khi đã bật đăng ký Workload, chat riêng chưa duyệt chỉ được đăng ký/xem trạng thái, chưa mở trò chuyện chung.
- Người A đăng ký riêng bằng số của mình, nhận thông báo thành công đúng tên rồi thử hỏi lịch cá nhân và mở `/doica`, không thêm ID riêng vào Variables. Người B chưa đăng ký hoặc chưa duyệt không được dùng danh tính A. Tự duyệt không chứng minh quyền sở hữu số, nên cần kiểm tra tên và xử lý ngay khi có báo nhầm.
- Gửi `/dangky` trong nhóm → hướng dẫn sang chat riêng, không công khai tên đối chiếu hoặc số điện thoại.
- Hỏi quy định có trong file được chia sẻ → có trích đoạn và nguồn; hỏi quy định không có hoặc tài liệu riêng → không bịa và không lộ dữ liệu.
- Khi dùng bộ lọc tên cũ: đổi/xóa hồ sơ nhóm A không đổi nhóm B. Khi dùng danh tính đã duyệt: hồ sơ tự khai không thay được liên kết đó.
- Thành viên hỏi đổi ca hoặc Issue trong nhóm → bot hướng dẫn nhắn riêng. Khi Issue `managed` đã bật, thử yêu cầu có nội dung “Thử nghiệm” trong DM → xác nhận → Admin nhận việc/phản hồi → người gửi xem kết quả. Không cần sửa lịch thật để thử Issue.
- Thành viên trong nhóm được phép @bot hỏi kiến thức chung hoặc lời chúc → hướng dẫn phạm vi và `/menu`, không gọi model tạo câu trả lời; hỏi quy định nội bộ → tra tài liệu và nêu nguồn.
- Hạn mức AI dùng chung theo người giữa DM và nhóm; mặc định 10 lượt/người/ngày và 300 lượt/toàn bot/ngày, đặt lại lúc 00:00 giờ Việt Nam. Lệnh trực tiếp vẫn dùng khi hết hạn mức. Câu bị từ chối không được thêm vào ngữ cảnh AI; lịch sử Admin vẫn lưu theo thông báo đồng ý hiện có.

Bot xử lý **lệnh → mẫu câu đã biết → AI phân loại câu chưa hiểu khi `ZALO_NLU_ENABLED=true` → kiểm tra quyền/dữ liệu → chức năng có sẵn**. “Hôm nay”, “ngày mai”, “tuần sau” được tính theo giờ Việt Nam; “mình/tôi” dùng danh tính đăng ký, không đoán tên từ AI. Câu thiếu hoặc có nhiều yêu cầu sẽ được hỏi lại/hướng dẫn gửi đầy đủ. Khi AI lỗi, dùng `/menu`, `/lich`, `/lichcuatoi`, `/doica` hoặc `/issue`; các lệnh vẫn chạy riêng. AI không tự duyệt đăng ký, đổi lịch hoặc bỏ qua xác nhận Issue. Xem [giới hạn và cấu hình AI](ngon-ngu-tu-nhien.md).

Các bước trên là bài thử cần thực hiện tại nhóm mới; kiểm thử mô phỏng, demo Admin và build thành công chưa chứng minh nhóm thật hoặc tải nhiều người cùng lúc đã hoạt động.
