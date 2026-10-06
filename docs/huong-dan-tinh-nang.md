# Tài liệu, nhắc việc, Google Sheets và dùng thử

Luồng nhắn riêng và nhớ tên trong cùng hội thoại đã được người dùng xác nhận. Các tính năng bên dưới có kiểm thử tự động, **chưa được xác nhận trên Zalo/Google Sheets thật**. Thử lần lượt, nhắn riêng trước rồi mới thử nhóm.

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

Nhắn riêng: người gửi phải có trong `users`. Trong nhóm: nhóm phải có trong `groups`; nếu `users` không rỗng, người gửi cũng phải có trong đó. Quyền bot trong `.env` vẫn được kiểm tra trước quyền tài liệu. **Nội dung trả trong nhóm hiển thị cho mọi thành viên nhóm**: chỉ cấp `groups` cho tài liệu được phép chia sẻ với cả nhóm.

Không dùng `*`, đường dẫn tuyệt đối, đường dẫn `..`, liên kết tới thư mục khác hoặc file bí mật. Bot chỉ đọc file có trong manifest. Quyền và nội dung tài liệu được đọc lại mỗi câu hỏi; đổi manifest không cần khởi động lại. Đổi `.env` thì phải khởi động lại.

Có thể đổi thư mục bằng `ZALO_KNOWLEDGE_DIR` trong `.env`. Các thư mục `data/` và `.runtime/` đã được loại khỏi Git. Không đặt tài liệu nội bộ vào `examples/` vì thư mục đó được đưa lên Git.

Bộ [10 câu hỏi mẫu và 3 câu không có thông tin](../examples/knowledge/acceptance.json) dùng để kiểm tra trước khi thay bằng tài liệu thật.

## 3. Nhắc việc

| Lệnh gửi trên Zalo | Ý nghĩa |
| --- | --- |
| `/nhac sau 1 phut \| Kiểm tra bot` | Nhắc sau một phút |
| `/nhac sau 2 gio \| Gửi báo cáo` | Nhắc sau hai giờ |
| `/nhac YYYY-MM-DD HH:MM \| Nội dung` | Nhắc vào ngày giờ cụ thể, thay phần ngày/giờ bằng số thật |
| `/nhac xem` | Nhắc việc của chính bạn tại cuộc trò chuyện này, kèm trạng thái |
| `/nhac huy MA` | Hủy nhắc đang chờ, dùng mã bot đã trả |

Múi giờ cố định: **giờ Việt Nam, UTC+7**. Trong nhóm, nhắc được gửi vào nhóm; nhắn riêng thì gửi vào cuộc trò chuyện riêng đó. Muốn nhắc riêng hãy tạo ở chat riêng. Không có lệnh gửi tới người/nhóm tùy ý. Trong nhóm nên **trả lời một tin của bot** rồi nhập lệnh để bot nhận được nguyên cú pháp.

- Chỉ người tạo, tại đúng cuộc trò chuyện, xem/hủy được nhắc của mình.
- Máy và bot cần chạy, có mạng, không ngủ. Bộ hẹn kiểm tra mỗi giây; thời gian nhận còn phụ thuộc mạng và Zalo, không có cam kết chính xác tuyệt đối.
- Nhắc đang chờ được giữ khi khởi động lại. Trễ không quá 24 giờ: gửi bù và ghi rõ trễ; quá 24 giờ: đánh dấu quá hạn.
- Nếu dừng máy ngay trong lúc gửi hoặc Zalo không xác nhận kết quả, nhắc được đánh dấu chưa rõ và không tự gửi lại. Hãy kiểm tra tin đã nhận trước khi tạo nhắc thay thế.
- Tối đa 30 nhắc đang chờ cho mỗi người trong mỗi cuộc trò chuyện, đặt trước tối đa 90 ngày. Thay quyền trong `.env` cần khởi động lại; nhắc của người/nhóm bị thu hồi quyền sẽ bị chặn.

## 4. Chuẩn bị Google Sheets cho lịch và issue

Chưa có đường dẫn bảng và khóa thử nghiệm, vì vậy tích hợp mặc định tắt. Bạn không cần cấu hình Sheets để thử tài liệu hoặc nhắc việc.

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

Mỗi dòng là một lịch. `users` và `groups` chứa ID ngăn cách dấu phẩy; ô trống không tự cấp quyền công khai. Nhắn riêng cần ID trong `users`; nhóm cần ID nhóm trong `groups` và kiểm tra thêm `users` nếu không rỗng. Không hỗ trợ lịch lặp; khai báo từng lần riêng. Bản thử đọc tối đa 1.000 dòng và hiển thị tối đa 20 lịch một ngày.

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

`ZALO_SHEETS_USERS` là danh sách quyền riêng cho lịch/issue. Người dùng còn phải có trong `ZALO_ALLOWED_USERS`. Với nhóm, cần cả `ZALO_ALLOWED_GROUPS` và `ZALO_SHEETS_GROUPS`.

6. Cài phần xác thực Google và kiểm tra:

```powershell
.\scripts\run.ps1 SetupSheets
.\scripts\run.ps1 SheetsCheck
```

`SetupSheets` cài thư viện vào môi trường dự án và dùng trình quản lý thư viện chính thức của Hermes để thêm bộ Google; không bật công cụ Google cho model. `SheetsCheck` chỉ đọc và kiểm tra tiêu đề hai tab, không tạo issue. Kết quả đọc thành công **chưa xác nhận quyền ghi**.

7. Dừng và bật lại bot bằng `Start` để nhận `.env` mới.

Tích hợp dùng [xác thực service account chính thức](https://developers.google.com/identity/protocols/oauth2/service-account), [Sheets values.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get) và [values.append](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append). Quyền OAuth áp dụng cấp bảng tính, không phải từng tab; adapter giới hạn thao tác trong hai tab đã cấu hình. Chỉ mẫu test trên máy được tạo tự động; chưa có thao tác nào tạo tài khoản hoặc ghi Google Sheets thật.

## 5. Thử lịch và issue

Lịch:

```text
/lich homnay
/lich 2026-10-07
```

Thay ngày bằng ngày có lịch mẫu. Bot chỉ đọc, không sửa lịch. Dòng lịch được cấp quyền nhưng sai ngày/giờ phải được sửa trên Sheets; bot không tự đoán múi giờ hoặc bỏ qua âm thầm.

Issue:

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
