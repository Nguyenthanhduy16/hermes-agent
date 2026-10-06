# hermes-zalo-bot

Bot hỗ trợ công việc trên **Zalo Bot Platform chính thức**, dùng Hermes Agent để xử lý hội thoại: **tin Zalo → Hermes → trả lời đúng cuộc trò chuyện**.

Người dùng đã xác nhận nhắn riêng nhận được câu trả lời và bot nhớ tên trong cùng hội thoại. Đã bổ sung lệnh tra tài liệu có nguồn, nhắc việc, đọc lịch và tạo issue qua Google Sheets có xác nhận, cùng báo cáo dùng thử. **Các tính năng mới đang được kiểm chứng bằng mô phỏng; chưa thử Google Sheets và nhắc việc thật.** Chưa triển khai máy chủ.

**Bot đã chạy được?** Xem [hướng dẫn tính năng mới theo từng bước](docs/huong-dan-tinh-nang.md). Bản tài liệu hiện tìm trích đoạn `.md`/`.txt` theo từ khóa, chưa có PDF/Word hoặc hỏi đáp ngữ nghĩa.

## 1. Chuẩn bị

- Windows và PowerShell; mở terminal tại thư mục repo này.
- Mạng để tải Hermes và thư viện. Khi chạy bot, máy phải bật, có mạng và không ngủ.
- Bot Token từ Zalo Bot Creator; tài khoản/model AI do bạn chọn.
- Đặt repo GitHub ở chế độ **Private**. Không nhập token/API key vào chat hoặc commit chúng.

**Tạo bot:** trong ứng dụng Zalo, tìm OA **Zalo Bot Manager** → **Tạo bot** → nhập tên bắt đầu bằng `Bot` → hoàn tất. Zalo gửi token qua tin nhắn cho tài khoản của bạn. [Hướng dẫn chính thức](https://docs.zaloplatforms.com/docs/BOT/create_bot)

## 2. Cài đặt và thử cục bộ

Chạy lần lượt:

```powershell
.\scripts\install-hermes.ps1
.\scripts\run.ps1 Setup
.\scripts\run.ps1 Demo
.\scripts\run.ps1 Test
.\scripts\run.ps1 TestHermes
```

- `install-hermes.ps1`: cài Hermes bằng trình cài Windows native chính thức, cố định phiên bản mã nguồn tại commit `93c9360a8da592cee43e8895403e91c7a920b0ce`; bỏ bước tương tác và công cụ browser/computer-use tùy chọn.
- `Setup`: tạo môi trường Python riêng, cài adapter, đặt plugin và cấu hình vào thư mục Hermes dành riêng cho dự án.
- `Demo`: mô phỏng một tin được phép, một tin trùng và một người chưa được cấp quyền; không gọi Zalo hoặc AI.
- `Test`: chạy kiểm thử tự động bằng dữ liệu giả và API mô phỏng.
- `TestHermes`: nạp plugin bằng Hermes đã cài và kiểm tra luồng xử lý của Hermes thật; đầu vào Zalo và câu trả lời model vẫn được mô phỏng, không dùng token/API key thật.

Hermes hỗ trợ cài native trên Windows và cho phép tách nơi lưu mã nguồn với dữ liệu người dùng. Dự án dùng `.runtime/hermes-agent/` và `.runtime/hermes-home/`. [Tài liệu cài Hermes](https://hermes-agent.nousresearch.com/docs/getting-started/installation)

Nếu PowerShell chặn chạy script, mở một cửa sổ PowerShell với quyền thực thi chỉ áp dụng cho cửa sổ đó:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass
```

Trong cửa sổ mới, chuyển về thư mục repo và chạy lại lệnh. Không cần đổi chính sách toàn máy.

## 3. Nhập token và lấy ID để cấp quyền

Tạo `.env` từ mẫu nếu chưa có:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
notepad .env
```

Nhập **trên máy** token thật vào `ZALO_BOT_TOKEN`, lưu file. Chưa cần điền danh sách ID ở bước này.

```dotenv
ZALO_BOT_TOKEN=TOKEN_BAN_TU_NHAP_TREN_MAY
ZALO_ALLOWED_USERS=
ZALO_ALLOWED_GROUPS=
```

Kiểm tra token và trạng thái webhook:

```powershell
.\scripts\run.ps1 ZaloCheck
```

Lệnh này chỉ đọc thông tin bot/webhook, không gửi tin và không gọi AI. Nếu bot có webhook, polling sẽ không hoạt động; cần gỡ cấu hình webhook cũ trước. Adapter không tự xóa webhook. [Quy tắc polling](https://docs.zaloplatforms.com/docs/BOT/apis/getUpdates)

Nếu token hợp lệ nhưng riêng `getWebhookInfo` trả `404`, script báo **chưa xác minh được trạng thái webhook** và cho phép thử `Discover`. Mã này không chứng minh bot đang có hay không có webhook. Nếu Zalo từ chối polling, chương trình dừng và báo bước/mã lỗi; không tự thay đổi cấu hình webhook.

Tiếp theo lấy ID:

```powershell
.\scripts\run.ps1 Discover
```

Trong khoảng 60 giây chờ, mở Zalo và gửi một tin riêng cho bot. Terminal hiển thị `user_id`, `chat_id`, `chat_type`; không in nội dung tin và không trả lời.

**Chỉ chạy một tiến trình nhận tin mỗi lần.** Dừng `Start` trước khi chạy `Discover`. `Discover` lấy các event khỏi luồng polling, vì vậy hãy gửi tin mới sau khi bật lại bot.

Sửa `.env`: sao chép `user_id` vào `ZALO_ALLOWED_USERS`. Nhiều ID ngăn cách bằng dấu phẩy, không dùng `*`.

```dotenv
ZALO_ALLOWED_USERS=id_nguoi_1,id_nguoi_2
ZALO_ALLOWED_GROUPS=
```

Để trống `ZALO_ALLOWED_GROUPS` sẽ tắt xử lý tin nhóm. Chạy kiểm tra cấu hình:

```powershell
.\scripts\run.ps1 Check
```

`Check` chỉ kiểm tra cục bộ định dạng token và danh sách quyền; không xác thực token với Zalo.

## 4. Chọn model và bật bot

```powershell
.\scripts\run.ps1 Model
```

Trình cấu hình `hermes model` mở trong môi trường riêng của dự án. Chọn nhà cung cấp/model bạn muốn và tự nhập API key hoặc đăng nhập theo hướng dẫn. Cấu hình truy cập AI được lưu trong `.runtime/hermes-home/`, không đưa vào Git. Việc gọi model khi chat có thể tính phí theo tài khoản của bạn; dự án không đăng ký dịch vụ hoặc mua gói tự động.

Sau đó:

```powershell
.\scripts\run.ps1 Start
```

Mở Zalo, dùng người đã cấp quyền gửi một tin mới cho bot, ví dụ: “Hãy chào mình bằng tiếng Việt”. Bot phải trả lời trong chính cuộc trò chuyện đó.

**Dừng:** bấm `Ctrl+C` trong cửa sổ đang chạy. **Bật lại:** chạy `Start`. Sau khi sửa `.env`, dừng rồi bật lại.

Biến môi trường đã đặt trong cửa sổ PowerShell được ưu tiên hơn `.env`. Nếu trước đó bạn đã đặt `ZALO_*` bằng tay, xóa các giá trị của cửa sổ hiện tại trước khi chạy lại để dùng `.env` (lệnh không xóa file hay token trên Zalo):

```powershell
Remove-Item Env:ZALO_BOT_TOKEN, Env:ZALO_ALLOWED_USERS, Env:ZALO_ALLOWED_GROUPS -ErrorAction SilentlyContinue
```

Hermes chỉ xử lý hội thoại; công cụ thao tác máy, duyệt web và các lệnh điều khiển Hermes qua Zalo bị vô hiệu hóa. Lệnh công việc do adapter xử lý trực tiếp. Phiên được tách theo loại chat, ID cuộc trò chuyện và ID người gửi.

## Các tính năng công việc

| Tính năng | Lệnh trên Zalo | Chuẩn bị |
| --- | --- | --- |
| Tài liệu | `/tailieu`, `/hoi Câu hỏi` | `PrepareDocs` tạo mẫu; manifest cấp quyền từng file |
| Nhắc việc | `/nhac sau 1 phut \| Nội dung`, `/nhac xem`, `/nhac huy MA` | Bot/máy phải chạy; giữ trạng thái khi bật lại |
| Lịch | `/lich homnay`, `/lich YYYY-MM-DD` | Google Sheets, tab `Lich`, quyền đã cấp |
| Issue | `/issue tao Tiêu đề \| Mô tả`, `/issue xacnhan MA`, `/issue huy MA` | Google Sheets, tab `Issues`; bản nháp hết hạn 10 phút |
| Dùng thử | `/phanhoi Nội dung`, `/trogiup` | Chạy `Report` trên máy để tổng hợp |

Hướng dẫn cài, mẫu Sheets, quyền và giới hạn: [docs/huong-dan-tinh-nang.md](docs/huong-dan-tinh-nang.md). Bài thử cho nhóm nhỏ: [docs/pilot-checklist.md](docs/pilot-checklist.md).

## 5. Thử trong nhóm Zalo

**Quyền nhóm còn cần xác minh trên bot thật.** Trang giới thiệu công bố Basic có 3 nhóm Beta, nhưng hướng dẫn nhóm vẫn ghi tính năng đang thử nghiệm nội bộ. Kiểm tra kết quả `can_join_groups` từ `ZaloCheck` và giao diện Bot Creator; không xem đây là quyền đã có sẵn cho mọi tài khoản. [Gói Basic](https://bot.zapps.me/), [hướng dẫn nhóm](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Khi tài khoản có tính năng:

1. Mở **Zalo Bot Creator**, chọn bot.
2. Tại **Mời Bot vào nhóm**, chia sẻ link vào nhóm thử.
3. **Trưởng nhóm** mở link và xác nhận thêm bot.
4. Chạy `Discover`, rồi **@mention bot** hoặc trả lời một tin bot đã gửi.
5. Lấy `chat_id` có `chat_type: GROUP` điền vào `ZALO_ALLOWED_GROUPS`; thêm `user_id` của từng người thử vào `ZALO_ALLOWED_USERS`.
6. Chạy `Check`, sau đó `Start`; gửi một câu hỏi mới bằng @mention.

Bot nhận tin nhóm khi được nhắc tên hoặc có người trả lời tin của bot; không nhận toàn bộ hội thoại nhóm. ID dùng gửi về nhóm là `chat.id`. [Quy tắc tương tác nhóm](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Nhóm cần được cấp quyền **và** người gửi cũng phải được cấp quyền. Dù ngữ cảnh model được tách theo người, **câu trả lời gửi trong nhóm vẫn hiển thị cho các thành viên của nhóm**.

## 6. Bài thử trước khi cho nhóm dùng

| Bài thử | Kết quả cần thấy |
| --- | --- |
| Người được cấp quyền nhắn riêng | Bot trả lời đúng cuộc trò chuyện |
| Người chưa được cấp quyền nhắn | Bot không gọi Hermes và không trả lời |
| Hai người hỏi riêng nội dung khác nhau | Không lẫn ngữ cảnh hội thoại |
| Nhóm và người đều được cấp quyền @bot | Bot trả lời đúng nhóm |
| Nhóm chưa được cấp quyền @bot | Không xử lý câu hỏi |
| Người chưa được cấp quyền @bot trong nhóm đã cấp quyền | Không xử lý câu hỏi |
| Câu trả lời dài | Chia thành nhiều tin phù hợp giới hạn Zalo |
| Mất mạng rồi có lại | Tiến trình nhận tin có cơ chế thử lại; kiểm tra bằng tin mới |

Chỉ ghi nhận luồng thật thành công sau khi đã gửi và nhận tin trên Zalo với model thật. Có thể thử chat riêng trước nếu quyền nhóm chưa được mở.

## 7. Giới hạn và cách xử lý lỗi

| Hiện tượng | Cách xử lý |
| --- | --- |
| Chưa có Hermes/runtime | Chạy `install-hermes.ps1`, sau đó `Setup`; xem thông báo cài đặt nếu tải thư viện thất bại |
| `The system cannot find the path specified` khi chạy Hermes | Dùng bản script hiện tại: `TestHermes`, `Model` và `Start` gọi `hermes.exe` để hỗ trợ đường dẫn có dấu trên Windows; không cần đổi tên thư mục hoặc bảng mã terminal |
| `Check` báo thiếu ID | Chạy `Discover`, điền `ZALO_ALLOWED_USERS`, lưu rồi kiểm tra lại |
| Token bị từ chối | Kiểm tra token trong `.env`; nếu đã reset token trên Zalo thì nhập token mới |
| Đang có webhook | Gỡ webhook cũ qua phương thức chính thức `deleteWebhook`, rồi chạy lại `ZaloCheck` |
| Token hợp lệ nhưng `getWebhookInfo` trả `404` | Đây là trạng thái webhook chưa xác minh; có thể tiếp tục thử `Discover`. Không cần đổi token chỉ vì lỗi này |
| `Discover` không có tin | Kiểm tra bot được nhắn có đúng không, dừng tiến trình polling khác; với nhóm phải @bot/quote tin bot |
| Nhận tin nhưng không trả lời | Kiểm tra cả quyền người/nhóm, cấu hình `Model`, mạng và hạn mức tài khoản |
| Vượt hạn mức (`429`) | Kiểm tra hạn mức Zalo; adapter chờ và thử lại có giới hạn |
| Model chưa cấu hình | Chạy `Model` và hoàn thành cấu hình nhà cung cấp |
| Không thêm được bot vào nhóm | Kiểm tra quyền nhóm và tính năng Beta trên tài khoản; tiếp tục thử nhắn riêng |
| Gửi bị timeout hoặc lỗi máy chủ | Kết quả gửi có thể chưa rõ; adapter không tự gửi lại. Kiểm tra Zalo trước, rồi gửi yêu cầu mới nếu cần |

Polling phục vụ chạy thử, **không có cam kết nhận đủ tin khi máy tắt, ngủ hoặc mất mạng**. Zalo khuyên dùng webhook khi triển khai thường xuyên. [Tài liệu getUpdates](https://docs.zaloplatforms.com/docs/BOT/apis/getUpdates)

Adapter ghi dấu nhận tin vào cơ sở dữ liệu trước khi gọi Hermes để ngăn xử lý lặp. Nếu tiến trình chết giữa chừng, một tin đã ghi dấu nhưng chưa hoàn thành có thể không được xử lý lại; người dùng cần gửi tin mới. Không có bảo đảm “đúng một lần” từ đầu đến cuối. Tin gửi có kết quả không rõ cũng không tự động phát lại, vì Zalo chưa công bố khóa chống gửi trùng.

`.env`, `.runtime/`, `.venv/`, `data/`, `logs/` và các file cơ sở dữ liệu đã được loại khỏi Git. Hermes có thể lưu lịch sử hội thoại trong thư mục dữ liệu cục bộ; không chia sẻ hoặc commit các thư mục này. Log adapter không ghi nội dung hội thoại, token hay URL chứa token. Chỉ cung cấp cho bot dữ liệu được phép gửi tới nhà cung cấp AI đã chọn.

## Lệnh tra nhanh

| Lệnh | Mục đích | Gọi dịch vụ bên ngoài? |
| --- | --- | --- |
| `install-hermes.ps1` | Tải và cài Hermes riêng cho dự án | Có, tải phần mềm |
| `run.ps1 Setup` | Cài adapter và cấu hình plugin | Có thể tải thư viện |
| `run.ps1 Update` | Chép code mới vào plugin, giữ model và `.env` | Không |
| `run.ps1 PrepareDocs` | Tạo tài liệu giả để thử nhắn riêng | Không |
| `run.ps1 PrepareSheets` | Tạo hai CSV để nhập vào Sheets | Không |
| `run.ps1 SetupSheets` | Cài thư viện xác thực Google | Có, tải thư viện |
| `run.ps1 SheetsCheck` | Kiểm tra mẫu và quyền đọc hai tab | Google, chỉ đọc |
| `run.ps1 FeatureDemo` | Mô phỏng tài liệu và nhắc việc | Không |
| `run.ps1 Report` | Tổng hợp lượt dùng/lỗi/token/chi phí còn lưu | Không |
| `run.ps1 Demo` | Mô phỏng luồng xử lý | Không |
| `run.ps1 Test` | Kiểm thử tự động | Không dùng Zalo/model thật |
| `run.ps1 TestHermes` | Kiểm tra plugin với Hermes đã cài | Không dùng Zalo/model thật |
| `run.ps1 Check` | Kiểm tra cấu hình cục bộ | Không |
| `run.ps1 ZaloCheck` | Kiểm tra token/webhook | Zalo, chỉ đọc |
| `run.ps1 Discover` | Nhận event để lấy ID trong 60 giây | Zalo, không gửi tin/không AI |
| `run.ps1 Model` | Cấu hình nhà cung cấp/model | Tùy phương thức đăng nhập |
| `run.ps1 Start` | Chạy bot | Zalo và model đã cấu hình |

Chi tiết cấu trúc API, nguồn chính thức và những điểm chưa chắc chắn: [docs/api-contract.md](docs/api-contract.md).

Kết quả kiểm thử trên máy và các cảnh báo còn lại của bản Hermes đã cài: [docs/validation.md](docs/validation.md). Trước khi nâng Hermes, chạy lại cả `Test` và `TestHermes`; adapter đang được kiểm chứng với commit cố định ở trên.
