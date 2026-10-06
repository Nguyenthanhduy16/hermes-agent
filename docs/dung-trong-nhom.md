# Dùng bot trong nhóm: lệnh và câu hỏi tự nhiên

## Thành viên bắt đầu

Trong nhóm được quản trị cho phép, gõ `@`, **chọn Bot LabCoach trong danh sách**, rồi nhập `/menu`: `@Bot LabCoach /menu`. Hoặc **trả lời một tin của bot** rồi nhập `/menu`. Bot chỉ nhận những tin được Zalo chuyển tới qua mention/reply, không đọc mọi tin trong nhóm. [Hướng dẫn Zalo](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Adapter nhận tên hiển thị (`display_name`) và tên tài khoản (`account_name`) từ Zalo, chỉ bỏ phần gọi tên bot ở đầu tin nhắn. Tên tài khoản nội bộ có thể khác tên hiển thị trong nhóm. Nếu đã cập nhật code mà `/menu` vẫn trả lời về tài liệu, dừng bot bằng **Ctrl+C**, chạy `Update` rồi `Start` để nạp bản sửa. Nếu đổi tên bot trên Zalo cũng cần khởi động lại để đọc tên mới.

| Muốn làm gì | Dùng lệnh | Hỏi tự nhiên |
| --- | --- | --- |
| Lịch chung | `/lich homnay` | `Hôm nay có lịch gì?` |
| Lịch theo phòng | `/lich homnay \| phong=H201` | `Hôm nay phòng H201 có lớp nào?` |
| Đăng ký tên | `/dangky Nguyễn Văn An` | `Tôi là Nguyễn Văn An` |
| Lịch của mình | `/lichcuatoi mai` | `Ngày mai mình có lịch gì?` |
| Lịch cá nhân trong tuần | `/lichcuatoi tuan nay` | `Tuần này tôi dạy ca nào?` |
| Hỏi quy định | `/hoi Xin nghỉ phép cần báo ai?` | `Xin nghỉ phép cần báo ai?` |
| Xem tài liệu được chia sẻ | `/tailieu` | `Xem tài liệu` |
| Xem hướng dẫn nhóm | `/help nhom` | `hướng dẫn nhóm` |

Tên trên là ví dụ; thay bằng **họ tên đầy đủ đúng trong Sheets**. Bot kiểm tra tên có trong lịch trước khi lưu. Tên chưa khớp hoặc có nhiều cách viết trùng khi bỏ dấu sẽ được yêu cầu nhập lại, không tự chọn người gần giống.

Đăng ký một lần cho mỗi chat. `/hoso` xem tên đã lưu; `/dangky Họ tên mới` thay tên; `/hoso xoa` xóa tên của chính mình. Hồ sơ còn sau khi khởi động lại nhưng không dùng chung giữa các nhóm hoặc giữa nhóm và nhắn riêng.

**Đăng ký tên chỉ là chọn bộ lọc lịch, không phải xác minh danh tính.** Không dùng hồ sơ này để cấp tài liệu cá nhân, xác nhận chấm công hoặc thực hiện thay người khác. Lịch cá nhân hiện chọn các buổi có tên đó ở Lab Coach/Key Coach; phản hồi vẫn chứa phân công của buổi học. Tin trả trong nhóm hiển thị cho cả nhóm.

## Quyền của thành viên

Với `ZALO_GROUP_ACCESS=members`, thành viên không cần có tên trong danh sách user để hỏi tại **nhóm được cho phép**. Họ có thể đọc lịch/tài liệu được chia sẻ với nhóm, đăng ký/xóa tên của mình, gửi góp ý. Nhắc việc và issue vẫn cần quyền cá nhân riêng. Việc đăng ký không mở quyền nhắn riêng với bot.

Nhóm chưa được quản trị cho phép không được xử lý. Những người rời nhóm không có quyền truy cập qua nhắn riêng nhờ đăng ký trước đó. Thành viên mới chỉ đọc tài liệu có quyền nhóm phù hợp; quyền cá nhân trên từng tài liệu vẫn được giữ nguyên.

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

Nhiều ID cách nhau dấu phẩy. Không dùng `*`. `ZALO_ALLOWED_USERS` vẫn giữ các user được dùng nhắn riêng/tác vụ ghi; không thay bằng danh sách tên Coach. Với lịch Lab, quyền nhóm cho phép đọc các tab đã cấu hình. `ZALO_SHEETS_USERS` vẫn cần cho người được cấp quyền riêng; `ZALO_ISSUES_ENABLED=false` tiếp tục tắt issue.

4. Cấp tài liệu quy định cho nhóm theo phần dưới.
5. Chạy `Update`, `Check`, rồi `Start`. Mỗi lần sửa `.env` cần dừng/bật lại bot; sửa nội dung tài liệu hoặc manifest không cần bật lại.

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

`users: []` và `groups` có ID nghĩa là chia sẻ nội dung cho thành viên nhóm đó; chưa cho đọc trong chat riêng. Nếu `users` có ID, chỉ những người đó trong nhóm được hỏi tài liệu. Chỉ chia sẻ tài liệu phù hợp cho cả nhóm vì mọi người có thể nhìn thấy câu trả lời.

Bot trích đoạn từ tài liệu được cấp quyền và nêu nguồn. Chưa có tài liệu/không tìm thấy thông tin thì báo rõ. **Lịch Sheets không tự chứa quy định**, và sổ tay mẫu không thay thế quy định thật. Các câu hỏi nhóm được xử lý bằng dữ liệu được cấp quyền, không gửi sang model để tự suy diễn chính sách. Trò chuyện thông thường trong chat riêng vẫn dùng Hermes.

## Các bài thử cần đạt

- Một thành viên chưa có trong `ZALO_ALLOWED_USERS` trả lời bot `/menu` trong nhóm đã cho phép → có hướng dẫn; thử ở nhóm khác/nhắn riêng → không được mở quyền.
- Người A đăng ký tên, hỏi lịch cá nhân; người B chưa đăng ký hỏi cùng câu → bot yêu cầu B đăng ký, không dùng tên A.
- Hỏi quy định có trong file được chia sẻ → có trích đoạn và nguồn; hỏi quy định không có hoặc tài liệu riêng → không bịa và không lộ dữ liệu.
- Đổi tên/xóa hồ sơ ở nhóm A không thay đổi hồ sơ ở nhóm B.
- Thành viên chỉ có quyền đọc thử tạo nhắc/issue → bot báo cần quyền riêng.

Ngôn ngữ tự nhiên dùng các mẫu nhận diện rõ ràng, không phải mọi cách diễn đạt đều được hiểu. Khi thời gian mơ hồ (ví dụ “thứ hai”, “sắp tới”), bot hướng dẫn nhập ngày cụ thể. Lệnh `/lich` và `/hoi` luôn là cách dùng rõ ràng khi câu tự nhiên chưa được nhận đúng. Không có luồng tự thực thi xóa/sửa lịch hoặc xác nhận issue từ câu nói tự do.
