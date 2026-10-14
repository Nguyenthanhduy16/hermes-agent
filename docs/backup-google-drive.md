# Sao lưu tự động vào Google Drive

Railway tạo bản sao dữ liệu đã **mã hóa**; Apps Script chạy bằng tài khoản Google của người vận hành, tải bản đó vào My Drive mỗi ngày khoảng **02:00 giờ Việt Nam**. Máy cá nhân có thể tắt. Giữ **7 bản thành công gần nhất**; các bản cũ do chính script này tạo được chuyển vào Thùng rác, không xóa vĩnh viễn và không đụng tệp khác.

Chỉ bật sau khi thư mục đích đã đặt **Quyền truy cập chung → Bị hạn chế / Restricted**. Script kiểm tra lại điều này mỗi lần chạy, không tự sửa quyền chia sẻ. Người được mời đích danh vẫn có thể truy cập; chỉ mời người vận hành cần thiết.

## Cài một lần

Dự án đang cấu hình: [LabCoach Backup trên Apps Script](https://script.google.com/home/projects/1HQftl21r_9i_3zt5jK6V8SVywl93PH69kcpfNWIF12tRxvBCXTkXQPre/edit). Người vận hành mở bằng tài khoản Google sở hữu thư mục; dùng dự án này để kiểm tra **Triggers** và **Executions**, không tạo thêm lịch trùng. Có trigger chưa đồng nghĩa bản sao lưu đã được kiểm chứng.

1. Dùng tài khoản Google sở hữu [thư mục sao lưu](https://drive.google.com/drive/folders/1441O18AnkWStFHsetN_vevUc6OLZI56Q). Xác nhận thư mục là **Restricted** và tài khoản còn dung lượng Drive.
2. Mở [Apps Script](https://script.google.com/), chọn **Dự án mới**, đặt tên `LabCoach Backup`. Chép toàn bộ [scripts/google-drive-backup.gs](../scripts/google-drive-backup.gs) vào `Code.gs`, rồi lưu. Không chia sẻ quyền chỉnh sửa dự án cho người không quản lý hệ thống.
3. Vào **Cài đặt dự án → Thuộc tính tập lệnh / Script Properties**, thêm ba dòng:

| Thuộc tính | Giá trị |
| --- | --- |
| `LABCOACH_BACKUP_URL` | `https://hermes-zalo-bot-production.up.railway.app/api/backup` |
| `LABCOACH_BACKUP_FOLDER_ID` | `1441O18AnkWStFHsetN_vevUc6OLZI56Q` |
| `LABCOACH_BACKUP_ACCESS_TOKEN` | Khóa truy cập sao lưu riêng do người vận hành cấp; nhập trực tiếp, không dán vào chat/Git |

4. Người vận hành cấu hình `LABCOACH_BACKUP_ACCESS_TOKEN` cùng giá trị bước 3, khóa mã hóa riêng và `LABCOACH_BACKUP_ENABLED=true` trên Railway; mặc định endpoint tắt. **Khóa mã hóa `LABCOACH_BACKUP_KEY` giữ ở server và nơi lưu khóa riêng; không đưa vào Apps Script hoặc thư mục sao lưu.** Khóa truy cập chỉ cho phép tải bản đã mã hóa, không dùng thay Bot Token hoặc mật khẩu Admin.
5. Chọn hàm **`setupLabCoachBackup` → Chạy / Run**, cấp quyền Drive, kết nối ngoài và tạo lịch chạy cho dự án mình vừa tạo. Hàm này kiểm tra cấu hình, thư mục rồi tạo lịch; chưa tải bản sao lưu ngay. Không cần bấm **Deploy**, không xuất bản web app.
6. Khi đã sẵn sàng sao lưu thật, chạy **`backupLabCoachDaily`** một lần. Kiểm tra trong thư mục có `LabCoach-backup-YYYY-MM-DD.lcbak` và mục **Executions / Lần thực thi** báo hoàn thành. Chạy lại cùng ngày không tạo thêm bản đã thành công.
7. Vào **Triggers / Trình kích hoạt**, kiểm tra một lịch `backupLabCoachDaily` và giữ thông báo khi lỗi. Chỉ **một người, một dự án Apps Script** cài lịch này.

**Khi chuyển sang tài khoản Google mới:** dùng dự án Apps Script thuộc tài khoản sở hữu thư mục mới. Nếu đã bật lịch ở tài khoản cũ, đăng nhập tài khoản đó và chạy `stopLabCoachBackup` trước khi bật lịch mới; giữ các bản sao cũ nếu còn cần khôi phục. Không chuyển khóa mã hóa vào Drive.

Đọc màn hình cấp quyền: dịch vụ `DriveApp` xin quyền Drive rộng; **phạm vi OAuth không chỉ giới hạn một thư mục**. Mã script kiểm tra và chỉ ghi/dọn trong thư mục đã cấu hình. Dùng đúng tài khoản sở hữu thư mục, xem lại mã trước khi cấp quyền và giữ dự án Apps Script riêng tư.

Trigger chạy dưới tài khoản người tạo; Google có thể lệch khoảng 15 phút so với 02:00. Google gửi email báo khi trigger lỗi; xem chi tiết trong **Executions**, không cần xây dịch vụ gửi mail riêng. [Tài liệu trigger](https://developers.google.com/apps-script/guides/triggers/installable), [giờ và múi giờ](https://developers.google.com/apps-script/reference/script/clock-trigger-builder).

## Dữ liệu và khóa

Bản sao lưu chứa các cơ sở dữ liệu của bot được backend cho phép: đăng ký, liên kết, nhắc việc, đổi ca, Issue, Admin và hội thoại/ngữ cảnh nếu có. Không sao chép lịch Google Sheets, `.env`, token, API key hay file khóa service account. Snapshot nhất quán trong từng cơ sở dữ liệu; không phải giao dịch nguyên khối giữa mọi cơ sở dữ liệu.

Giới hạn dữ liệu gốc hiện tại là **24 MiB**. Script chỉ nhận gói mã hóa **nhỏ hơn 45 MiB**, đúng MIME và SHA-256 do server gửi; sau khi ghi Drive sẽ tải lại để kiểm tra SHA-256 lần nữa rồi mới dọn bản cũ. Mã hóa vẫn cần khóa riêng để giải mã: **mất khóa thì không khôi phục được**. Lưu khóa trong trình quản lý mật khẩu hoặc một nơi an toàn tách biệt với Drive sao lưu; giữ khóa cũ nếu đổi khóa khi còn bản cũ cần khôi phục.

Không dùng service account đang đọc Sheets để tạo tệp trong My Drive: service account không có dung lượng lưu trữ và không thể sở hữu tệp. Apps Script tạo bản sao dưới tài khoản Google đã cấp quyền. [Giới hạn chính thức của Google Drive](https://developers.google.com/workspace/drive/api/guides/about-shareddrives).

## Khi lỗi hoặc cần tắt

| Lỗi trong Executions | Cách xử lý |
| --- | --- |
| `FOLDER_NOT_RESTRICTED` | Chuyển Quyền truy cập chung của thư mục về Restricted; kiểm tra cả quyền kế thừa. |
| `INVALID_SOURCE_URL` | Dùng đúng URL trong bảng; không thêm query, dấu `/` cuối hoặc địa chỉ chuyển hướng. |
| `INVALID_ACCESS_TOKEN`, `BACKEND_HTTP_ERROR` | Kiểm tra khóa truy cập riêng, endpoint đã bật và Railway còn chạy; không dán khóa vào log/chat. |
| `INVALID_SIZE` | Nhờ người vận hành kiểm tra dung lượng; không bỏ giới hạn bằng cách tắt kiểm tra. |
| `DOWNLOAD_HASH_MISMATCH`, `DRIVE_HASH_MISMATCH`, `DRIVE_VERIFICATION_FAILED` | Bản này chưa được xác nhận tốt. Kiểm tra mạng/tệp, giữ các bản cũ. |
| `DAILY_FILE_CONFLICT` | Có tệp cùng ngày trùng tên hoặc lần trước dang dở. Người vận hành kiểm tra tệp; script không tự ghi đè hay xóa tệp đó. |
| `BUSY` | Một lần chạy khác đang hoạt động. Đợi nó kết thúc rồi kiểm tra kết quả. |
| `OPERATION_FAILED` | Kiểm tra quyền Apps Script/Drive, dung lượng, quota và Railway; script không ghi chi tiết nhạy cảm ra lỗi. |

Lịch chỉ chạy một lần mỗi ngày. Sau khi sửa lỗi, có thể chạy lại `backupLabCoachDaily`; không tự gửi dữ liệu sang URL khác hoặc tự thử liên tục. Chạy **`stopLabCoachBackup`** để xóa lịch do tài khoản hiện tại tạo; các bản sao đang có được giữ nguyên. Thùng rác của Drive vẫn chiếm dung lượng cho đến khi được dọn theo chính sách của Google/người vận hành.

## Thử khôi phục

Tải một `.lcbak` về máy an toàn, giữ file khóa riêng ngoài Git. Dùng công cụ có sẵn để **kiểm tra** trước, rồi giải mã vào một thư mục staging trống, không đè dữ liệu bot đang chạy:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.backup verify C:\backup\LabCoach-backup-YYYY-MM-DD.lcbak --key-file C:\private\labcoach-backup.key
.\.venv\Scripts\python.exe -m hermes_zalo.backup restore C:\backup\LabCoach-backup-YYYY-MM-DD.lcbak --staging-dir C:\backup\restore-test --key-file C:\private\labcoach-backup.key
```

Thay đường dẫn bằng file thật của bạn. Khôi phục vào staging vô hiệu hóa phiên đăng nhập Admin cũ; chuyển staging vào hệ thống thật là thao tác vận hành riêng, phải dừng bot và đối chiếu dữ liệu. Không kết luận sao lưu phục hồi được chỉ vì đã thấy tệp trên Drive: cần xác nhận lệnh `verify` và lần thử khôi phục thành công.

Mã script được kiểm thử với Drive/HTTP giả. Chỉ xác nhận sao lưu Google Drive thật sau khi người vận hành cấp quyền, chạy và đối chiếu tệp thực tế; chỉ xác nhận lịch hằng ngày sau một lần trigger tự chạy thành công.
