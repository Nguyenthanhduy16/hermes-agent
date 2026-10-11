# Tài liệu, nhắc việc, Google Sheets và dùng thử

Luồng nhắn riêng và nhớ tên trong cùng hội thoại đã được người dùng xác nhận. Các tính năng bên dưới có kiểm thử tự động. Tích hợp đọc lịch Lab được kiểm tra riêng với Google Sheets; luồng lịch qua Zalo, gửi nhắc và ghi issue vẫn cần thử thực tế. Thử lần lượt, nhắn riêng trước rồi mới thử nhóm.

Người dùng đã phản hồi “oki rồi” sau bài thử lịch trên Zalo. Bản nâng cấp menu/ngày/bộ lọc mới cần thử lại sau khi khởi động bot. Xem [bảng lệnh dễ dùng](lenh-zalo.md), hoặc gửi `/menu`, `/help lich`, `/help nhac` trực tiếp cho bot.

**Quản lý nhiều thành viên:** ưu tiên [đăng ký Workload trong chat riêng](dang-ky.md). Thành viên gửi `/dangky SỐ_ĐIỆN_THOẠI`; quản trị xác minh rồi duyệt từ `/dangkychoduyet`. Duyệt xong tự có quyền lịch, đổi ca, nhắc việc, góp ý và trò chuyện tại chat riêng đã liên kết, không thêm từng `user_id` vào `.env` hoặc khởi động lại cho mỗi người. Issue cần bật toàn hệ thống và có tab đích hợp lệ; tài liệu dùng chung cần `approved_members: true` trong manifest. Quản trị quản lý bằng `/thanhvien` và `/thuhoidangky MÃ_THÀNH_VIÊN`. Quyền quản trị, nhóm và tài liệu riêng giữ nguyên. Mở đổi ca nhiều người bằng `lab_live`; `lab_live_test` vẫn chỉ dành cho hai người thử.

## 1. Cập nhật và thử ngay

Dừng cửa sổ bot bằng `Ctrl+C`, rồi chạy tại repo:

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 PrepareDocs
.\scripts\run.ps1 Start
```

`Update` sao chép code mới vào Hermes riêng của dự án, giữ cấu hình model và `.env`. `PrepareDocs` chỉ tạo tài liệu minh họa nếu chưa có manifest; không ghi đè dữ liệu có sẵn. Mẫu được cấp quyền cho người có trong `.env`, **chưa cấp quyền cho nhóm**.

Gửi các tin sau trên Zalo:

```text
/trogiup
/hoi Giờ làm việc của nhóm là khi nào?
/hoi Ngân sách mua máy bay là bao nhiêu?
/nhac sau 1 phut | Kiểm tra bot đã nhắc đúng giờ
/nhac xem
```

Câu giờ làm việc phải có trích dẫn `so-tay-mau.md` và số dòng. Câu ngân sách phải báo chưa tìm thấy thông tin. Đây là **dữ liệu giả để kiểm thử**, không phải quy định thật của nhóm.

Các lệnh công việc chạy trực tiếp trong adapter, không gọi AI. Hội thoại thông thường vẫn qua Hermes và model đã chọn. Bot không suy diễn thao tác từ câu trả lời của model; hãy dùng đúng lệnh để tạo nhắc hoặc issue.

## 2. Đưa tài liệu thật vào bot

Bản này hỗ trợ **`.md` và `.txt` UTF-8**, tìm theo từ khóa tiếng Việt có hoặc không dấu, trả trích đoạn nguyên văn kèm nguồn. Chưa hỗ trợ PDF/Word/OCR, Google Drive, diễn giải bằng AI hoặc tìm kiếm ngữ nghĩa. Câu hỏi quá chung hoặc từ khóa khác tài liệu có thể không tìm thấy dù tài liệu có nội dung liên quan; thông báo là “chưa tìm thấy”, không khẳng định tài liệu hoàn toàn không có.

1. Đặt bản sao tài liệu được phép dùng vào `data/knowledge/`.
2. Sửa `data/knowledge/manifest.json`, khai báo từng file và quyền:

```json
{
  "documents": [
    {
      "id": "quy-trinh-01",
      "title": "Quy trình làm việc",
      "path": "quy-trinh.md",
      "users": ["USER_ID_CUA_BAN"],
      "groups": []
    }
  ]
}
```

3. Gửi `/tailieu` để xem tài liệu mình được phép tra, rồi `/hoi Câu hỏi cụ thể`.

Nhắn riêng: người gửi có trong `users`, hoặc đã được duyệt và tài liệu có `"approved_members": true`. Muốn chia sẻ một file cho mọi thành viên đã duyệt, đặt `"approved_members": true`, `"users": []`, `"groups": []` ở chính mục tài liệu đó; không cần khai báo từng ID. Cờ này mặc định `false`, chỉ nhận giá trị JSON `true`/`false`; tài liệu cũ và tài liệu riêng không tự được mở. Trong nhóm: nhóm phải có trong `groups`; nếu `users` không rỗng, người gửi cũng phải có trong đó. Đăng ký riêng không thay quyền tài liệu trong nhóm. Quyền dùng bot vẫn được kiểm tra trước quyền tài liệu. **Nội dung trả trong nhóm hiển thị cho mọi thành viên nhóm**: chỉ cấp `groups` cho tài liệu được phép chia sẻ với cả nhóm.

Không dùng `*`, đường dẫn tuyệt đối, đường dẫn `..`, liên kết tới thư mục khác hoặc file bí mật. Bot chỉ đọc file có trong manifest. Quyền và nội dung tài liệu được đọc lại mỗi câu hỏi; đổi manifest không cần khởi động lại. Đổi `.env` thì phải khởi động lại.

Có thể đổi thư mục bằng `ZALO_KNOWLEDGE_DIR` trong `.env`. Các thư mục `data/` và `.runtime/` đã được loại khỏi Git. Không đặt tài liệu nội bộ vào `examples/` vì thư mục đó được đưa lên Git.

Bộ [10 câu hỏi mẫu và 3 câu không có thông tin](../examples/knowledge/acceptance.json) dùng để kiểm tra trước khi thay bằng tài liệu thật.

## 3. Nhắc việc

Mọi thành viên đã duyệt được tạo, xem và hủy nhắc của mình tại đúng chat riêng đã liên kết. Không thêm ID vào `.env`. Quyền tạo nhắc trong nhóm vẫn theo cấu hình người dùng/nhóm hiện có.

| Lệnh gửi trên Zalo | Ý nghĩa |
| --- | --- |
| `/nhac sau 1 phut \| Kiểm tra bot` | Nhắc sau một phút |
| `/nhac sau 2 gio \| Gửi báo cáo` | Nhắc sau hai giờ |
| `/nhac YYYY-MM-DD HH:MM \| Nội dung` | Nhắc vào ngày giờ cụ thể, thay phần ngày/giờ bằng số thật |
| `/nhac xem` | Nhắc đang chờ/đang gửi của chính bạn tại cuộc trò chuyện này |
| `/nhac lichsu` | Nhắc gần đây, gồm đã gửi, hủy hoặc lỗi |
| `/nhac huy MA` | Hủy nhắc đang chờ, dùng mã bot đã trả |

Múi giờ cố định: **giờ Việt Nam, UTC+7**. Trong nhóm, nhắc được gửi vào nhóm; nhắn riêng thì gửi vào cuộc trò chuyện riêng đó. Muốn nhắc riêng hãy tạo ở chat riêng. Không có lệnh gửi tới người/nhóm tùy ý. Trong nhóm nên **trả lời một tin của bot** rồi nhập lệnh để bot nhận được nguyên cú pháp.

- Chỉ người tạo, tại đúng cuộc trò chuyện, xem/hủy được nhắc của mình.
- Máy và bot cần chạy, có mạng, không ngủ. Bộ hẹn kiểm tra mỗi giây; thời gian nhận còn phụ thuộc mạng và Zalo, không có cam kết chính xác tuyệt đối.
- Nhắc đang chờ được giữ khi khởi động lại. Trễ không quá 24 giờ: gửi bù và ghi rõ trễ; quá 24 giờ: đánh dấu quá hạn.
- Nếu dừng máy ngay trong lúc gửi hoặc Zalo không xác nhận kết quả, nhắc được đánh dấu chưa rõ và không tự gửi lại. Hãy kiểm tra tin đã nhận trước khi tạo nhắc thay thế.
- Tối đa 30 nhắc đang chờ cho mỗi người trong mỗi cuộc trò chuyện, đặt trước tối đa 90 ngày. Thay quyền trong `.env` cần khởi động lại; thu hồi đăng ký có hiệu lực ngay và chặn nhắc đang chờ của người đó. Nhắc của người/nhóm không còn quyền sẽ bị chặn.

## 4. Chuẩn bị Google Sheets cho lịch và issue

Tích hợp mặc định tắt khi chưa có ID bảng. Bạn không cần cấu hình Sheets để thử tài liệu hoặc nhắc việc. Chọn bảng mẫu hoặc cấu trúc Lịch lab hiện có theo hướng dẫn bên dưới.

### Tạo bảng thử nghiệm

```powershell
.\scripts\run.ps1 PrepareSheets
```

Hai file mẫu nằm trong `data/sheets-templates/`:

- `Lich.csv`: một lịch giả cho ngày kế tiếp ngày tạo mẫu, cấp quyền nhắn riêng theo danh sách người dùng hiện tại.
- `Issues.csv`: chỉ có dòng tiêu đề, chưa có issue.

Tạo một Google Sheets riêng để thử. Nhập hai CSV thành hai tab có tên chính xác **Lich** và **Issues**. Giữ nguyên tiêu đề cột. Khi nhập, tắt chuyển văn bản thành số/ngày/công thức; cột ngày giờ cần giữ dạng văn bản ISO có múi giờ, ví dụ `2026-10-07T09:00:00+07:00`.

Tab `Lich`:

```text
id,start,end,title,users,groups,location
```

Mỗi dòng là một lịch. `users` và `groups` chứa ID ngăn cách dấu phẩy; ô trống không tự cấp quyền công khai. Nhắn riêng cần ID trong `users`; nhóm cần ID nhóm trong `groups` và kiểm tra thêm `users` nếu không rỗng. **Các quyền trên từng dòng của mẫu `standard` vẫn được kiểm tra dù thành viên đã duyệt Workload**; quyền thành viên không biến dòng riêng thành dữ liệu chung. Không hỗ trợ lịch lặp; khai báo từng lần riêng. Bản thử đọc tối đa 1.000 dòng và hiển thị tối đa 20 lịch một ngày.

Tab `Issues`:

```text
id,created_at,title,description,status,user_id,chat_id,chat_type
```

### Cấp quyền cho bot bằng tài khoản dịch vụ Google

1. Trong [Google Cloud Console](https://console.cloud.google.com/), chọn hoặc tạo project riêng, bật **Google Sheets API**.
2. Vào **IAM & Admin → Service Accounts**, tạo tài khoản dịch vụ cho bot. Không cần cấp vai trò quản trị project để đọc/ghi một bảng tính được chia sẻ trực tiếp.
3. Tạo khóa JSON cho tài khoản dịch vụ, lưu **trên máy** tại `secrets/google-service-account.json`. Không gửi file khóa vào chat/Git. Nếu tổ chức không cho tạo khóa, cần quản trị viên cấp phương thức xác thực phù hợp; bản này chưa có đăng nhập Google cá nhân.
4. Lấy email `client_email` trong file khóa; chia sẻ **bảng thử nghiệm** cho email đó với quyền **Editor** để đọc lịch và thêm issue. Dùng tài khoản dịch vụ riêng, chỉ chia sẻ bảng thử nghiệm cần dùng.
5. Thêm các dòng sau vào `.env` trên máy:

```dotenv
ZALO_SHEETS_ID=ID_NAM_GIUA_D_VA_EDIT_TRONG_LINK_GOOGLE_SHEETS
ZALO_GOOGLE_CREDENTIALS=secrets/google-service-account.json
ZALO_CALENDAR_TAB=Lich
ZALO_ISSUES_TAB=Issues
ZALO_SHEETS_USERS=USER_ID_CUA_BAN
ZALO_SHEETS_GROUPS=
```

`ZALO_SHEETS_USERS` và `ZALO_ALLOWED_USERS` là cách cấp quyền cá nhân cũ, vẫn dùng cho người chưa dùng đăng ký Workload. **Người đã duyệt Workload không cần thêm vào hai danh sách để dùng các chức năng thành viên, kể cả issue khi đã bật**; quyền lấy từ dữ liệu đăng ký tại đúng chat riêng đã liên kết. Quyền trên từng dòng `standard` vẫn giữ nguyên. Với nhóm, luôn cần cấu hình cả `ZALO_ALLOWED_GROUPS` và `ZALO_SHEETS_GROUPS`; đăng ký riêng không mở thêm nhóm.

6. Cài phần xác thực Google và kiểm tra:

```powershell
.\scripts\run.ps1 SetupSheets
.\scripts\run.ps1 SheetsCheck
```

`SetupSheets` cài thư viện vào môi trường dự án và dùng trình quản lý thư viện chính thức của Hermes để thêm bộ Google; không bật công cụ Google cho model. `SheetsCheck` chỉ đọc và kiểm tra tiêu đề hai tab, không tạo issue. Kết quả đọc thành công **chưa xác nhận quyền ghi**.

7. Dừng và bật lại bot bằng `Start` để nhận `.env` mới.

Tích hợp dùng [xác thực service account chính thức](https://developers.google.com/identity/protocols/oauth2/service-account), [Sheets values.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get) và [values.append](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append). Quyền OAuth áp dụng cấp bảng tính, không phải từng tab; adapter giới hạn thao tác trong hai tab đã cấu hình. Chỉ mẫu test trên máy được tạo tự động; chưa có thao tác nào tạo tài khoản hoặc ghi Google Sheets thật.

## 5. Thử lịch và issue

### Đọc bảng Lịch lab hiện có

Chế độ `lab` đọc bảng phân công hiện có mà không sửa Google Sheets. Cấu hình trong `.env`:

```dotenv
ZALO_CALENDAR_FORMAT=lab
ZALO_CALENDAR_TABS_JSON=["Lịch lab 1009-2009","Lịch lab 2409-0210","Lịch lab 0210-1610"]
ZALO_ISSUES_ENABLED=false
```

Giữ các biến ID bảng, đường dẫn khóa và danh sách quyền đã cấu hình. Danh sách JSON là tên tab chính xác, tối đa 4 tab; tab mới phải được thêm vào danh sách trên máy rồi khởi động lại bot. `ZALO_CALENDAR_TAB` chỉ dùng cho chế độ `standard`.

Định dạng hỗ trợ: dòng ngày `Thứ 3 - 06/10/2026` hoặc `Chủ nhật - 04/10/2026`, sau đó là tiêu đề A:J `STT, Khóa, Session, Lab Coach, Key Coach, Phòng, Buổi, Sức chứa phòng, [ghi chú], Trạng thái`. Bot đọc tối đa 1.000 dòng mỗi tab. Thông tin khóa/session/phòng/buổi chỉ được dùng chung khi Google xác nhận là ô gộp; ô trống thông thường được ghi “Chưa ghi”. Dòng bổ sung không có STT chỉ được nhận khi nằm trong ô gộp Session thật.

Bot trả khóa, session, buổi, phòng, Key Coach, Lab Coach và trạng thái nguyên văn, kèm tab, dòng và link nguồn. Không suy ra giờ cụ thể từ “Sáng/Chiều/Tối”, không tự hiểu trạng thái “Rảnh” là đã xác nhận tham gia. Ngày có ở nhiều tab được hiển thị riêng theo nguồn, không tự quyết định tab nào thay thế tab nào. Lệnh `/lich` mới hiển thị tối đa 5 mục mỗi trang và có lệnh xem tiếp giữ ngày/bộ lọc. Có thể tra tuần và lọc Coach/khóa/phòng/buổi theo [bảng lệnh](lenh-zalo.md).

**Quyền đọc chế độ lab áp dụng cho toàn bộ các tab đã chọn**, vì bảng hiện tại không có ID Zalo trên từng dòng. Thành viên Workload đã duyệt được đọc lịch chung tại đúng chat riêng đã liên kết; người có quyền cá nhân theo hai danh sách cũ vẫn dùng được khi không bị chặn đăng ký. `/lichcuatoi` lọc theo danh tính đã được quản trị duyệt và được đối chiếu lại; tên trùng chưa có mã phân biệt sẽ bị chặn, không tự đoán người. Để `ZALO_SHEETS_GROUPS=` trống khi thử nhắn riêng; chỉ cấp quyền nhóm khi dữ liệu được phép hiển thị cho toàn bộ nhóm.

Với thành viên đã duyệt từ bản trước, cập nhật và khởi động lại bot một lần để nạp quyền tự động; không cần duyệt lại, thêm ID hoặc tạo CSV. Quyền đổi ca tự động chỉ áp dụng cho lịch Lab ở chế độ đã bật, vẫn giữ các bước xác nhận, phạm vi hai ô Lab Coach và danh sách hai người nếu dùng `lab_live_test`. Xem [đổi ca bằng chat](tickets-chat.md). Tên tự khai từ `/dangky Họ tên` khi Workload đang tắt không xác minh danh tính hay cấp quyền đổi ca.

Khi `ZALO_ISSUES_ENABLED=false`, bot dùng quyền OAuth chỉ đọc, `SheetsCheck` không đòi tab `Issues`, lệnh `/issue` không tạo bản nháp hay ghi bảng. Để thử issue sau này cần chuẩn bị tab mẫu riêng và chủ động bật tính năng.

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 SheetsCheck
.\scripts\run.ps1 Start
```

Dừng cửa sổ bot cũ bằng `Ctrl+C` trước khi bật lại. Sau đó nhắn riêng cho bot:

```text
/lich 2026-10-06
/lich 2026-09-24
/lich 2026-09-10
/lich homnay
```

Ngày chưa có trong bảng phải báo chưa có dữ liệu; ngày có tiêu đề nhưng không có dòng phân công phải báo không có dòng phân công. Mỗi lần tra đọc lại các tab được cấu hình; nếu một tab bị đổi tên, sai cấu trúc hoặc lỗi mạng, bot báo lỗi thay vì trả kết quả thiếu mà không thông báo.

Phần đọc dùng [spreadsheets.get với ranges và fields](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get) và [thông tin merges của Sheet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets). Nội dung bảng được xử lý trực tiếp, không đưa vào model AI. Chế độ này là tra lịch qua `/lich`, chưa phải hỏi đáp tài liệu Sheets bằng `/hoi`.

### Bảng mẫu và tạo issue

Lịch:

```text
/lich homnay
/lich 2026-10-07
```

Thay ngày bằng ngày có lịch mẫu. Bot chỉ đọc, không sửa lịch. Dòng lịch được cấp quyền nhưng sai ngày/giờ phải được sửa trên Sheets; bot không tự đoán múi giờ hoặc bỏ qua âm thầm.

Issue chỉ dùng sau khi bật `ZALO_ISSUES_ENABLED=true`, có tab `Issues` đúng tiêu đề và quyền ghi Google. **Bảng hiện tại chưa có tab này và issue đang tắt.** Thành viên đã duyệt dùng trong chat riêng mà không cần thêm ID vào `.env`.

```text
/issue tao Lỗi nút lưu | Bấm Lưu nhưng dữ liệu chưa cập nhật
```

Bot hiển thị tiêu đề, mô tả, bảng/tab đích và mã bản nháp. **Lúc này chưa ghi lên Sheets**. Nếu đúng, chính người tạo gửi trong cùng cuộc trò chuyện:

```text
/issue xacnhan MA_BAN_NHAP
```

Hoặc `/issue huy MA_BAN_NHAP`. Bản nháp hết hạn sau 10 phút. “Đồng ý” thông thường hoặc người khác xác nhận không tạo issue. Muốn sửa nội dung: hủy và tạo bản nháp mới. Xác nhận lại một mã đã xử lý không tạo thêm dòng.

Issue lưu trạng thái `open`, ID người yêu cầu và ID cuộc trò chuyện. Nội dung được ghi theo chế độ [RAW](https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption), không thực thi chuỗi người dùng nhập như công thức. Những người có quyền đọc bảng tính sẽ thấy issue trong đó.

Nếu Google bị timeout sau khi gửi, bot báo chưa rõ kết quả và không tự ghi lại. Kiểm tra cột `id` trong tab `Issues` trước khi tạo bản nháp mới. Chưa có tác vụ tự đối soát hoặc tự tạo lại issue không rõ kết quả.

## 6. Dùng thử và báo cáo

Theo [checklist 15 bài thử](pilot-checklist.md), bắt đầu với 2–3 người đã cấp quyền. Trong Zalo, ghi nhận lỗi bằng `/phanhoi Nội dung lỗi hoặc góp ý`. Góp ý được lưu riêng trên máy, không đưa vào báo cáo tổng hợp.

```powershell
.\scripts\run.ps1 FeatureDemo
.\scripts\run.ps1 Test
.\scripts\run.ps1 TestHermes
.\scripts\run.ps1 Report
```

`FeatureDemo` hoàn toàn mô phỏng, không dùng token. `Report` lưu `data/reports/pilot-latest.txt`: số lượt theo tính năng/kết quả, thời gian có đo, số góp ý, token và chi phí các phiên Zalo mà Hermes đã ghi nhận. Số liệu tích lũy, không phải báo cáo theo ngày. Không cộng chi phí thực tế và ước tính của cùng phiên; phần thiếu được ghi **chưa xác định**, không tự coi là miễn phí. Đối chiếu trang Usage của nhà cung cấp để biết hóa đơn đầy đủ, gồm các tác vụ phụ/thuê bao nếu có.

Các câu hỏi tài liệu và lệnh công việc không đi vào lịch sử model. Trích đoạn tài liệu ở bản này cũng không được gửi sang nhà cung cấp AI. Chat thông thường vẫn có thể được Hermes lưu và gửi đến nhà cung cấp đã chọn.
