# Đưa LabCoach lên Railway

Bot chạy liên tục trên Railway để nhiều thành viên dùng qua Zalo; máy Windows không cần bật. Vẫn dùng **long polling, một tiến trình bot, một Volume lưu dữ liệu**. Luồng Zalo không cần webhook. API Admin có HTTPS riêng được bảo vệ bằng khóa máy chủ; giao diện đặt trên Vercel theo [hướng dẫn Admin](admin.md).

Dockerfile của repo cài Hermes và adapter cùng nhau; giữ công cụ của model tắt như cấu hình hiện tại. Cách chạy gateway dựa trên [hướng dẫn Docker của Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/docker). Đóng gói thành công chưa chứng minh nhận–gửi Zalo thật thành công.

## Trạng thái đã xác nhận — 13/10/2026

- Đã bổ sung API Admin và lưu hội thoại, giữ nguyên Volume và quyền ghi Tickets hiện có. Kiểm tra đăng nhập API qua HTTPS đạt; bản giao diện Vercel chờ project. Lần gọi model thật mới nhất báo key OpenRouter hết hạn; chưa xác nhận nhận–gửi Zalo/đổi ca thật của luồng Admin mới. Chi tiết và bản sao trước cập nhật nằm trong [hướng dẫn Admin](admin.md).

- Service `hermes-zalo-bot` đã chạy trên Railway, một bản bot với Volume `/data`; bot Windows đã dừng.
- Đã chuyển 11 tệp dữ liệu adapter/Tickets/tài liệu. Không chuyển lịch sử hội thoại Hermes.
- Build Linux và kiểm tra nạp plugin Hermes đạt; 35 kiểm thử khởi động/chuyển dữ liệu trên container đạt.
- Kết nối Zalo đạt; đọc thật được 324 mục lịch từ 3 tab. Người dùng đã nhắn thử và xác nhận bot trả lời, nhận đúng thành viên đã đăng ký.
- Giữ cấu hình đổi ca hiện có; chưa thực hiện bài thử đổi ca ghi Sheets hoặc tải nhiều người đồng thời trên Railway trong lần bàn giao này.
- Lần này tải bản mã đã lọc từ máy qua CLI. Thay đổi Docker/Railway chưa được commit/push lên GitHub; cần đưa các file triển khai vào nhánh sử dụng trước khi dựa vào GitHub autodeploy. Không đưa `.runtime`, khóa, bản sao dữ liệu hoặc các file Excel không liên quan vào commit triển khai.

## 1. Chuẩn bị

- Project Railway hiện có; quyền tạo/cấu hình service và kết nối repo GitHub Private.
- Bot Token đang dùng, API key của model, JSON service account Google: **tự nhập trong Variables của Railway**, không gửi vào chat, không commit.
- Giữ nguyên ID bảng, tab lịch, cấu hình Workload và quản trị đang dùng. Không thêm từng thành viên đã duyệt vào biến môi trường.
- Xem [giá Railway](https://railway.com/pricing) và đặt giới hạn/cảnh báo chi phí phù hợp. Railway tính tài nguyên; model AI có hạn mức/chi phí riêng, không bảo đảm chạy miễn phí.

## 2. Tạo service ở chế độ chờ

1. Mở project Railway → **New → GitHub Repo** → chọn repo này và nhánh chứa Dockerfile.
2. Chọn service → **Variables** → nhập cấu hình theo [railway.env.example](../config/railway.env.example). Giữ `BOT_START_ENABLED=false`.
3. Tạo **Volume**, gắn vào đúng service, đặt **Mount Path = /data**. Không dùng đường dẫn Windows.
4. Trong Settings, giữ **1 replica**, tắt **Serverless/App Sleeping**; không thêm HTTP healthcheck, domain hoặc cổng công khai.
5. Deploy. Repo có `railway.toml`; giữ cách build/start mặc định của repo. Log ở chế độ chờ là bình thường, bot chưa nhận tin.

Railway đọc Dockerfile và cấu hình từ repo; thiết lập trong file có thể ưu tiên hơn Dashboard. Xem [Dockerfile](https://docs.railway.com/guides/dockerfiles) và [Config as code](https://docs.railway.com/config-as-code). Volume chỉ được gắn lúc chạy, không có trong bước build/pre-deploy; không đặt lệnh khôi phục dữ liệu ở pre-deploy. [Volume Railway](https://docs.railway.com/volumes)

### Điền những biến nào?

| Nhóm | Cách điền |
| --- | --- |
| Bot | `ZALO_BOT_TOKEN` dùng đúng token đang chạy; sao chép quyền quản trị/nhóm hiện có |
| AI | `HERMES_PROVIDER`, `HERMES_MODEL` và API key tương ứng; mẫu dùng OpenRouter, model có thể đổi/hết hạn mức |
| Google | `ZALO_GOOGLE_CREDENTIALS_SECRET_JSON`: dán toàn bộ JSON service account vào **giá trị của một biến**; chương trình tạo file tại `/data/secrets/google-service-account.json` |
| Lịch/đăng ký | Giữ `ZALO_SHEETS_ID`, các tab, cột/tiêu đề Workload, `REGISTRATION_ADMIN_USERS` và các quyền hiện có |
| Tickets | Giữ chế độ đang dùng; để các cờ ghi `false` trong lúc kiểm tra chuyển máy |
| Dữ liệu | Giữ các đường dẫn `/data/...` trong mẫu; không sao chép các đường dẫn `C:\...` hoặc `.runtime/...` của Windows |

`REGISTRATION_ENABLED` cần đặt lại `true` khi chuyển hệ thống đang dùng đăng ký Workload. Không thay tab/cột/tiêu đề hoặc token cùng lúc chuyển máy: các liên kết đã duyệt phụ thuộc cấu hình nguồn và token đang dùng.

**Đổi khóa Google:** file JSON đã tạo vẫn tồn tại trên Volume khi xóa biến `*_SECRET_JSON`. Muốn đổi khóa, thay biến bằng JSON mới rồi chạy `check` hoặc khởi động bot để cập nhật file; kiểm tra kết nối và thu hồi khóa cũ trong Google Cloud. Chỉ xóa Variables không thu hồi khóa.

Mẫu để đăng ký/ghi lịch/issue tắt nhằm chuẩn bị ban đầu; **không phải bản thay thế toàn bộ cấu hình hệ thống hiện tại**. Đối chiếu các giá trị đã chạy trước khi bật. Với Tickets `lab_live`, giờ đã xác nhận của dự án là Sáng 09:00–13:00, Chiều 14:00–18:00, Tối 17:30–20:30. Giữ điều kiện một bên duy nhất sửa lịch khi bật ghi.

## 3. Chuyển dữ liệu từ Windows

Cần chuyển danh tính đã duyệt, liên kết Tickets, nhắc việc và lịch sử để thành viên không đăng ký lại.

Lệnh export hiện chỉ lấy **ba đường dẫn mặc định trong bảng bên dưới**, không tự đọc đường dẫn tùy chỉnh từ `.env`. Nếu máy Windows đã đổi `HERMES_HOME`, `ZALO_STATE_DIR`, `TICKETS_DATA_DIR` hoặc `ZALO_KNOWLEDGE_DIR`, phải đối chiếu và điều chỉnh cách chuyển trước; không coi ZIP tạo thành công là đã lấy đúng toàn bộ dữ liệu.

1. Chọn lúc ít người dùng; **Ctrl+C dừng bot Windows** và đóng tiến trình Tickets đang dùng cùng dữ liệu. Giữ Railway ở `BOT_START_ENABLED=false`.
2. Tại thư mục repo trên Windows, tạo bản chuyển dữ liệu:

```powershell
.\.venv\Scripts\python.exe scripts\transfer_state.py export --bot-stopped --output .runtime/railway-transfer.zip
```

Cờ `--bot-stopped` là xác nhận của người vận hành; phải dừng bot thật trước. Lệnh chỉ đọc dữ liệu trên máy, không gọi Zalo/Google hoặc sửa lịch.

`railway ssh` và lệnh chuyển file cần khóa SSH đã đăng ký trên tài khoản Railway. Khóa riêng giữ trên máy; chỉ đăng ký khóa công khai `.pub`. Máy triển khai hiện tại đã có khóa riêng tên `labcoach_railway` trong `~/.ssh`; không đưa khóa vào Git hoặc gửi vào chat.

3. Dùng Railway CLI đã đăng nhập, chọn **đúng project, environment và service**:

```powershell
railway login
railway link
railway status
railway volume files upload .runtime/railway-transfer.zip /incoming/railway-transfer.zip
```

Đường dẫn upload là đường dẫn **bên trong Volume**; khi mount ở `/data`, file nằm tại `/data/incoming/railway-transfer.zip`. Nếu CLI chưa có lệnh này, cập nhật theo [Railway CLI](https://docs.railway.com/guides/cli), hoặc dùng `railway volume browse /` để tải file vào. [Lệnh quản lý file Volume](https://docs.railway.com/cli/volume)

4. Mở terminal của service bằng `railway ssh`, chạy khôi phục:

```sh
python -m hermes_zalo.state_transfer import /data/incoming/railway-transfer.zip --data-dir /data --bot-stopped
python -m hermes_zalo.container_runtime check
```

Chỉ khôi phục vào các thư mục dữ liệu đích trống; lệnh sẽ từ chối ghi đè dữ liệu đã có. Không xóa dữ liệu cũ để vượt kiểm tra. Nếu đã chạy bot mới, dừng và đối chiếu hai bản trước khi quyết định bản cần giữ.

| Nguồn trên Windows mặc định | Đích trên Volume |
| --- | --- |
| `.runtime/hermes-home/data/zalo` | `/data/hermes-home/data/zalo` |
| `.runtime/tickets` | `/data/tickets` |
| `data/knowledge` | `/data/knowledge` |

Bản chuyển chỉ mang SQLite đang dùng ở tầng đầu của hai thư mục dữ liệu adapter/Tickets và tài liệu được hỗ trợ. Các bản sao bảo trì trong thư mục con được bỏ qua và vẫn giữ nguyên trên Windows. Không mang môi trường Python Windows, Hermes binaries, file khóa tiến trình, `.env`, khóa Google hoặc hội thoại Hermes. Các bí mật cấu hình lại bằng Variables; lịch sử hội thoại model không được chuyển trong đợt này. File ZIP chứa dữ liệu nội bộ: giữ riêng, không đưa vào Git hoặc link công khai.

## 4. Bật bot và kiểm tra

1. Xác nhận bot Windows đã dừng, dữ liệu đã khôi phục và kiểm tra cấu hình không báo lỗi.
2. Đặt `BOT_START_ENABLED=true` rồi redeploy. **Chỉ một service nhận tin bằng token này**; không chạy thêm bot Windows, bản preview hoặc môi trường staging cùng token.
3. Trên Zalo, dùng một thành viên đã duyệt thử `/dangky trangthai`, `/menu`, `/lichcuatoi` và `/doica`; kiểm tra đúng danh tính và đúng lịch. Mở danh sách đổi ca không tự ghi lịch.
4. Thử hai người nhắn riêng, rồi @bot trong nhóm đã cấp quyền; kiểm tra trả lời đúng cuộc trò chuyện.
5. Đặt một nhắc việc thử, khởi động lại service và kiểm tra dữ liệu còn, nhắc được gửi đúng người. Nhắc quá hạn tối đa 24 giờ có thể được gửi bù khi bot chạy lại; quá 24 giờ sẽ hết hạn. Người đặt nhắc kiểm tra bằng `/nhac xem` trước khi tạo nhắc thay thế để tránh trùng.
6. Khi đọc dữ liệu đúng, bật lại cờ ghi Tickets theo cấu hình đã thống nhất và thử một yêu cầu với hai thành viên, hai ca tương lai được đồng ý. B xác nhận mới sửa hai ô Lab Coach thật; kiểm tra lại Sheets. Không bật issue chỉ vì đã chuyển máy.

Nếu không muốn dùng lệnh lịch cá nhân, hỏi tự nhiên **“Lịch của mình ngày mai?”**. Xem [các lệnh](lenh-zalo.md) và [đổi ca](tickets-chat.md).

Trạng thái Railway **Active** chỉ cho biết tiến trình đang chạy. Chỉ kết luận chuyển thành công sau khi có nhận–gửi thật, đúng danh tính và dữ liệu vẫn còn sau restart.

## 5. Theo dõi, cập nhật và quay lại Windows

- Xem **Logs/Metrics/Usage** của service. Không chụp/gửi màn hình Variables hoặc nội dung khóa.
- Giữ Volume khi redeploy; sao lưu trước thay đổi lớn. Railway hỗ trợ [sao lưu Volume](https://docs.railway.com/volumes/backups). Volume có thể gây khoảng dừng ngắn khi deploy, và không hỗ trợ replica; đây là cấu hình một bot dùng chung cho nhiều thành viên, chưa phải nhiều máy chạy song song. [Giới hạn Volume](https://docs.railway.com/volumes/reference)
- Tạm dừng: đặt `BOT_START_ENABLED=false` rồi redeploy, chờ bot ngừng nhận tin. Standby vẫn là service đang chạy và có thể tính tài nguyên.
- Muốn quay lại Windows: dừng bot trên Railway trước. Nếu Railway đã nhận đăng ký, nhắc việc hoặc đổi ca mới, phải sao lưu/chuyển trạng thái mới về trước; **không bật bản Windows cũ ngay**, tránh mất liên kết hoặc xử lý lại yêu cầu.
- Không chạy `Discover` khi bot Railway đang polling. Không bật webhook trong Bot Creator khi còn dùng long polling.

## Thử Docker tại máy, khi có Docker Desktop

Thử mô phỏng trước, chưa cần token/API key:

```powershell
docker build -t hermes-zalo-bot:local .
docker run --rm hermes-zalo-bot:local demo
```

Muốn chạy thử có dữ liệu, tạo file `.env.docker` riêng từ mẫu Railway, sửa giá trị và gắn Volume `/data`. Giữ `BOT_START_ENABLED=false` cho đến khi hoàn tất chuyển dữ liệu; không đưa file này vào Git. Chạy `check` chỉ kiểm tra cục bộ, không xác minh token/model/Google thật:

```powershell
docker volume create labcoach-data
docker run --rm --env-file .env.docker --mount source=labcoach-data,target=/data hermes-zalo-bot:local check
```

Nếu nhập JSON vào `.env.docker`, toàn bộ giá trị phải nằm trên **một dòng vật lý**, giữ các ký tự `\n` đã escape bên trong `private_key`. Không dán JSON nhiều dòng như trong ô giá trị Railway.

## Lỗi thường gặp

| Hiện tượng | Kiểm tra |
| --- | --- |
| Service chạy nhưng bot không trả lời | `BOT_START_ENABLED` còn false, quyền người/nhóm, bot khác đang polling, hoặc webhook đang bật |
| Mất đăng ký/nhắc sau redeploy | Volume có gắn đúng `/data`; đường dẫn state có bị ghi đè bằng đường dẫn Windows |
| Thành viên đã duyệt bị coi chưa đăng ký | Khôi phục thiếu dữ liệu, dùng khác token hoặc thay cấu hình nguồn Workload; không duyệt lại hàng loạt |
| Lỗi Google/không thấy lịch | JSON đầy đủ, Sheets đã chia sẻ service account, tab và tiêu đề đúng; không in JSON ra log |
| Có lịch nhưng model không trả lời | Kiểm tra provider/model/API key và hạn mức của nhà cung cấp |
| Lỗi khóa dữ liệu hoặc polling trùng | Dừng tiến trình Windows/staging còn chạy; không xóa file khóa để ép hai bot cùng chạy |
| Hết tài nguyên/credit | Xem Metrics/Usage, điều chỉnh gói/hạn mức theo nhu cầu thực; không có mức RAM hoặc gói miễn phí được bảo đảm |

Tài liệu đối chiếu ngày **13/10/2026**. Mỗi lần chuyển sang service/môi trường khác vẫn phải kiểm tra lại đúng project, Volume, dữ liệu và luồng Zalo thật.
