# Issue và đội ngũ hỗ trợ

Thành viên gửi yêu cầu qua **tin nhắn riêng Zalo**. Giảng viên và Admin xử lý trên trang Admin hiện có ở Vercel. Bot, tài khoản và dữ liệu vẫn nằm trên Volume Railway.

## 1. Ai được làm gì?

| Vai trò | Quyền |
| --- | --- |
| Thành viên đã đăng ký | Tạo, xem và bổ sung thông tin cho issue của mình. |
| Admin có quyền `issues` | Xem hàng chờ chung, nhận việc, hỗ trợ bằng ghi chú/phản hồi. Người phụ trách cập nhật trạng thái và kế hoạch. |
| Giảng viên (`faculty`) | Xem mọi issue, phân công/bàn giao; tạo, sửa, khóa/mở tài khoản Admin. |

Mỗi người có tài khoản riêng. Giảng viên **không tự có quyền đọc hội thoại, quản lý ghi nhớ hoặc quản lý thành viên**: vẫn cần cấp riêng `conversations`, `memories`, `members`. Quyền `memories` cần kèm `conversations` để sử dụng. Thành viên đăng ký Zalo không tự có tài khoản quản trị.

## 2. Thành viên gửi yêu cầu

| Việc cần làm | Nhắn riêng bot |
| --- | --- |
| Báo lỗi/hỗ trợ | `/issue tao Bot chưa hiện lịch | Mình hỏi lịch ngày mai nhưng chưa có kết quả` |
| Đề xuất tính năng | `/issue dexuat Nhắc trước ca | Mình muốn được nhắc trước ca 30 phút` |
| Xác nhận bản nháp | `/issue xacnhan MÃ_BOT_GỬI` |
| Hủy bản nháp | `/issue huy MÃ_BOT_GỬI` |
| Xem yêu cầu của mình | `/issue danhsach` hoặc `/issue danhsach 2` |
| Xem tiến độ | `/issue xem ISS-000001` |
| Bổ sung thông tin | `/issue bosung ISS-000001 | Ngày mình cần xem là 20/10` |

Đọc bản nháp rồi xác nhận trong **10 phút**. Bot chỉ tạo issue sau xác nhận, trả mã và cách theo dõi. “Mình muốn báo lỗi bot” hoặc “Mình muốn đề xuất tính năng” mở hướng dẫn phù hợp; nếu thiếu nội dung, gửi lại một câu đầy đủ theo mẫu. Không giả định bot đã nhớ câu nhập dở.

Chỉ người gửi đã liên kết đúng mã thành viên mới xem được issue của mình. Ghi chú nội bộ không xuất hiện trên Zalo. Trong nhóm, bot hướng dẫn chuyển sang tin nhắn riêng.

## 3. Admin và Giảng viên xử lý

1. Vào **Issue**, lọc loại, trạng thái, mã thành viên hoặc người phụ trách.
2. Mở yêu cầu **Mới** → **Nhận xử lý**. Hệ thống chỉ cho một người nhận thành công; người còn lại tải lại danh sách.
3. Chọn rõ **Ghi chú nội bộ** hoặc **Phản hồi cho người gửi**. Phản hồi được đưa vào hàng chờ gửi Zalo; ghi chú chỉ đội hỗ trợ xem.
4. Người phụ trách/Giảng viên cập nhật **Đang xử lý → Chờ bổ sung / Hoàn thành**. Hai trạng thái sau bắt buộc có phản hồi cho thành viên. Issue đang chờ có thể chuyển lại Đang xử lý; issue hoàn thành muốn mở lại phải ghi lý do.
5. Với đề xuất tính năng, điền mức ưu tiên, estimate và ngày dự kiến xong khi đã đánh giá; có thể để trống.

Giảng viên dùng **Phân công / bàn giao**, chọn người và nhập lý do. Mỗi issue giữ một người phụ trách chính và lịch sử thao tác. Khi người phụ trách bị khóa, Giảng viên chủ động bàn giao issue còn mở.

Thông báo gửi khi nhận việc, cần bổ sung, hoàn thành hoặc có phản hồi công khai. Mục **Thông báo Zalo** cho biết kết quả; **Thử gửi lại** chỉ xuất hiện với lỗi chắc chắn chưa gửi và dưới 3 lần thử. Trạng thái chưa rõ kết quả không tự gửi lại để tránh trùng tin. Quyền và liên kết người nhận được kiểm tra lại trước mỗi lần gửi.

Trong **Quản lý Admin**, Giảng viên tạo tài khoản, chọn quyền, khóa/mở và xem số issue đang phụ trách. Thay đổi tài khoản thu hồi phiên đăng nhập cũ. Không có nút tự nâng quyền Giảng viên; hệ thống bảo vệ Giảng viên hoạt động cuối cùng.

## 4. Tạo Giảng viên đầu tiên và khôi phục

Tài khoản được người vận hành chỉ định cho lần này: **`giangvien.maianh`**. Tạo trên đúng máy/container và cùng `ZALO_STATE_DIR` của bot; mật khẩu nhập ẩn, tối thiểu 12 ký tự. Không đặt mật khẩu trong lệnh hoặc Git.

Windows, từ thư mục repo:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m hermes_zalo.admin_cli create-account --username giangvien.maianh --display-name "Mai Anh" --role faculty --permissions issues
```

Trong container Railway, dùng lệnh tương tự với `python` thay cho `.\.venv\Scripts\python.exe`. Không dùng Python của môi trường Hermes riêng cho lệnh này. Lệnh trên chỉ cấp vai trò Giảng viên và quyền Issue; các quyền riêng khác phải được cấp rõ. Với các lệnh `python -m …` bên dưới, khi chạy trên Windows dùng `.\.venv\Scripts\python.exe -X utf8 -m …` để hiển thị đúng tiếng Việt.

Nếu chỉ định một tài khoản **đang tồn tại và hoạt động**, giữ nguyên quyền riêng:

```text
python -m hermes_zalo.admin_cli designate-faculty --username TEN_DANG_NHAP
```

Nếu bị khóa hoặc mất mật khẩu, người vận hành server khôi phục bằng `create-account ... --role faculty --permissions DANH_SACH_QUYEN --replace`. Thao tác đặt lại mật khẩu, mở tài khoản, thay quyền theo danh sách đã nhập và thu hồi phiên cũ. Đối chiếu quyền trước khi chạy; không dùng `--replace` để thử đăng nhập.

## 5. Chuyển dữ liệu Issue cũ một lần

Mặc định `ZALO_ISSUES_MODE=legacy`: giữ luồng Sheets cũ. `managed` dùng các bảng quản lý mới **trong cùng file `work-….issues.sqlite3`**, không tạo một nguồn ghi Issue thứ hai. `off` tắt thao tác Issue và gửi thông báo, giữ nguyên dữ liệu. `ZALO_ISSUES_ENABLED` chỉ điều khiển luồng Sheets cũ; chế độ mới dùng `ZALO_ISSUES_MODE` và yêu cầu đối soát thành công.

Trước khi chuyển:

1. Dừng tiến trình bot ghi dữ liệu; giữ bot Windows tắt nếu dùng Railway. Sao lưu Volume và các SQLite bằng cách nhất quán, có cả dữ liệu WAL.
2. Đóng ghi tab Issues cũ. Xuất **toàn bộ** dữ liệu vào file JSON riêng tư, bỏ hàng tiêu đề: `target` là `ID_BẢNG/TÊN_TAB`, `rows` gồm đúng 8 cột chuỗi theo thứ tự `id, created, title, body, status, user_id, chat_id, chat_type`.
3. Chỉ dùng `rows: []` sau khi đã kiểm tra nguồn cũ thực sự trống/chưa từng có Issue. Không lấy việc SQLite trống làm bằng chứng Sheets trống. Không đưa snapshot vào Git.
4. Chạy xem trước bằng công cụ dưới đây. Thay các đường dẫn mẫu bằng **đúng file của bot đang chạy** trên Volume:

```text
python -m hermes_zalo.issue_migration --database /data/DUONG_DAN/work-BOT.issues.sqlite3 --registrations /data/DUONG_DAN/work-BOT.registration.sqlite3 --snapshot /data/maintenance/issues-snapshot.json
```

Mặc định chỉ kiểm tra trên bản sao tạm. Chỉ tiếp tục khi báo `ready: true`; để áp dụng, chạy lại cùng lệnh và thêm:

```text
--apply --bot-stopped --source-complete
```

Công cụ tạo bản sao `.pre-managed-….sqlite3` trước khi ghi; nhập lặp không tạo issue trùng. Người gửi chỉ được ánh xạ bằng đăng ký đã duyệt và ID thật, không đoán theo tên/số điện thoại. Nếu có chủ sở hữu chưa xác định, bản nháp còn hiệu lực, bản đã tạo chưa nhập hoặc lần ghi cũ chưa rõ kết quả, hệ thống chặn chuyển đổi. Đối soát từng trường hợp; `--reconcile-found` chỉ dùng cho dòng đích khớp đầy đủ, `--not-created MÃ` chỉ dùng sau khi xác minh chắc chắn chưa ghi, kèm `--reason`.

Sau chuyển, SQLite là nguồn chính; tab Sheets cũ chỉ để đối chiếu. Luồng mới không ghi lịch hoặc ghi Issue trở lại Sheets. Bản code mới cũng chặn ghi legacy khi đã có dấu đối soát.

## 6. Đưa bản cập nhật lên Railway và Vercel

1. Chạy kiểm thử và thử giao diện mẫu: `.\scripts\run.ps1 AdminDemo`. Demo chỉ dùng dữ liệu giả, không gọi Zalo, Google hoặc model.
2. Đưa mã đã kiểm tra lên nhánh triển khai. Cập nhật Railway trước, giữ một tiến trình bot và Volume `/data`; thực hiện bước đối soát/tạo Giảng viên ở trên trong thời gian bảo trì.
3. Đặt `ZALO_ISSUES_MODE=managed` khi báo sẵn sàng; giữ cấu hình Admin, proxy và các khóa hiện có. Bật lại bot trên Railway.
4. Redeploy Vercel để nhận giao diện và danh sách API mới; Root Directory vẫn `deploy/vercel-admin`. Giữ ba biến kết nối phía server theo [hướng dẫn Vercel](../deploy/vercel-admin/README.md).
5. Thử tài khoản Giảng viên và Admin chỉ có `issues`; bảo đảm Admin không thấy mục Quản lý Admin/hội thoại. Thành viên tạo một issue thử qua DM → xác nhận → Admin nhận việc → phản hồi → thành viên xem kết quả. Ghi rõ đây là lần thử thật; không sửa lịch để kiểm tra Issue.

Nếu cần khôi phục phiên bản: đặt `ZALO_ISSUES_MODE=off` trên bản mới hoặc dừng tiến trình ghi, giữ nguyên Volume, snapshot cùng dữ liệu mới. **Không chuyển về ghi legacy hoặc chép đè bằng bản sao trước chuyển đổi sau khi đã có issue mới**: sẽ làm mất hoặc tách dữ liệu. Chỉ chạy phiên bản tương thích với dữ liệu hiện tại, hoặc phiên bản cũ với chức năng Issue thực sự tắt (`ZALO_ISSUES_ENABLED=false` nếu bản cũ chỉ biết cờ này) và giữ bản sao đầy đủ để phục hồi tiếp.

Kiểm thử mô phỏng và demo không chứng minh luồng Zalo/Vercel/Railway thật đã hoạt động; cần hoàn tất bước 5 sau triển khai.
