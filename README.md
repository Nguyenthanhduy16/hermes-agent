# hermes-zalo-bot

Bot hỗ trợ công việc trên **Zalo Bot Platform chính thức**: **tin Zalo → kiểm tra quyền → lệnh/mẫu câu hoặc AI nhận diện ý định → chức năng đã bật → trả lời đúng cuộc trò chuyện**. Dùng model/provider cấu hình trong Hermes; không mở trò chuyện kiến thức chung.

Bot đã triển khai trên Railway, giao diện Admin trên Vercel; người dùng đã xác nhận nhận/gửi Zalo và tra lịch. Có đăng ký Workload, tài liệu có nguồn, lịch, đổi ca có duyệt, nhắc lịch và Issue theo quyền. Phần đọc lịch hỗ trợ bảng mẫu và các tab **Lịch lab có ô gộp**; xem [cấu hình lịch hiện có](docs/huong-dan-tinh-nang.md#đọc-bảng-lịch-lab-hiện-có). Mỗi bản cập nhật vẫn cần phân biệt kiểm thử mô phỏng với thử nhận/gửi thật.

**Bot đã chạy được?** Xem [hướng dẫn tính năng mới theo từng bước](docs/huong-dan-tinh-nang.md). Bản tài liệu hiện tìm trích đoạn `.md`/`.txt` theo từ khóa, chưa có PDF/Word hoặc hỏi đáp ngữ nghĩa.

**Issue và đội hỗ trợ:** đã có chế độ quản lý trên Admin với vai trò **Giảng viên – Admin – Thành viên**: nhận việc, bàn giao, phản hồi Zalo, ghi chú nội bộ và estimate. Mặc định giữ nguồn Sheets cũ; chỉ chuyển sang `ZALO_ISSUES_MODE=managed` sau đối soát dữ liệu. Xem [lệnh thành viên, quyền, tạo Giảng viên và triển khai](docs/ISSUES.md). Quyền hội thoại/ghi nhớ vẫn cấp riêng.

**Menu:** `/menu` hiển thị **Tra lịch**, **Lịch của tôi**, **Đổi ca**, **Thông báo** và **Tạo issue** theo cấu hình và quyền của bạn. Đã ẩn mục `/nhac`; lệnh và các nhắc tự đặt trước đây vẫn được giữ. `/thongbao` giải thích các thông báo được bật và cách xem yêu cầu đổi ca đang chờ. **Tạo issue** dùng `/issue`, kèm **(chưa bật)** khi đã được cấp quyền nhưng tính năng đang tắt. Xem [bảng lệnh](docs/lenh-zalo.md).

**Nhắc lịch tự động:** khi bật, bot nhắn riêng **20:00 tối hôm trước** để tổng hợp lịch ngày mai và **30 phút trước mỗi ca** để chuẩn bị check-in. Chỉ gửi cho thành viên đã đăng ký thành công, khớp đúng người trong cột Lab Coach; không có ca thì không gửi. Tin nhắc không ghi nhận điểm danh. Xem [cách bật, giờ nhắc và giới hạn](docs/nhac-lich-tu-dong.md).

**Thành viên nhóm:** khi bật `ZALO_GROUP_ACCESS=members`, thành viên đăng ký hợp lệ trong nhóm được cho phép có thể **@bot hoặc trả lời tin bot** để hỏi công việc Lab và tài liệu được chia sẻ. Câu ngoài phạm vi nhận hướng dẫn `/menu`, không chuyển sang model trả lời kiến thức chung. Lệnh trực tiếp không tốn lượt AI; phân loại câu chưa hiểu có hạn mức mặc định **10 lượt/người/ngày, 300 lượt/toàn bot/ngày** theo giờ Việt Nam. Xem [hướng dẫn nhóm](docs/dung-trong-nhom.md) và [cấu hình AI](docs/ngon-ngu-tu-nhien.md).

**Đăng ký thành viên:** nhắn **riêng** `/dangky SỐ_ĐIỆN_THOẠI` đúng trong tab `Workload - Lab coach`. Khi bật `REGISTRATION_AUTO_APPROVE=true`, số khớp duy nhất được duyệt ngay; bot báo **“✅ Đăng ký thành công!”**, tên và lệnh `/menu`. Bot tự lưu `user_id`, chat riêng và mã thành viên; không thêm từng người vào `.env`. Người đang chờ gửi lại lệnh; đăng ký lặp không tạo bản ghi trùng. Số trùng hoặc đã liên kết tài khoản khác bị chặn; từng bị từ chối/thu hồi vẫn cần quản trị duyệt lại. Số có trong bảng không xác minh người gửi sở hữu số đó. Đăng ký thành công mở lịch, đổi ca, nhắc việc và góp ý trong chat riêng; issue/tài liệu vẫn theo cấu hình riêng. Quản trị dùng `/thanhvien` để xem và thu hồi. Không tự cấp quản trị hoặc quyền nhóm; tên trùng vẫn cần ánh xạ để hỏi lịch cá nhân/đổi ca. Đặt `REGISTRATION_AUTO_APPROVE=false` để dùng duyệt thủ công. Xem [cấu hình và cách dùng](docs/dang-ky.md).

**Có thể hỏi tự nhiên:** `Mình muốn đổi ca`, `Ai đang chờ mình xác nhận?`, `Xem thông báo` hoặc `Bot làm được gì?`. Bot mở đúng phần hướng dẫn hoặc danh sách khi nhận được mẫu câu đã hỗ trợ; lịch/quy định vẫn tra dữ liệu được cấp quyền. Đổi ca và tạo issue vẫn cần các bước xác nhận, không thực hiện bằng câu “đồng ý” tự do. Xem [ví dụ câu hỏi](docs/lenh-zalo.md#hỏi-tự-nhiên-và-trò-chuyện).

**Hiểu câu linh hoạt bằng AI (tùy chọn):** bật `ZALO_NLU_ENABLED=true` sau khi kiểm tra model. Lệnh và mẫu câu cũ vẫn chạy trực tiếp; câu chưa hiểu mới nhờ model đang cấu hình nhận diện ý định, sau đó gọi chức năng đã bật và kiểm tra quyền như cũ. Xem [cách bật, câu thử và giới hạn](docs/ngon-ngu-tu-nhien.md). Không cần đổi model hoặc cấp quyền công cụ cho Hermes.

**Đổi ca:** thành viên nhắn riêng `/doica` để chọn hai ca, lý do và gửi yêu cầu; B dùng mã bot gửi để đồng ý/từ chối. Chỉ chọn ca chưa bắt đầu, có chuyển trang khi danh sách dài. Với `lab_live` và `TICKETS_REQUIRE_MANAGER=true`, **B đồng ý → chờ Admin duyệt → mới cập nhật Sheets**. Admin mở **Duyệt đổi ca** để tìm kiếm, xem hai ca, duyệt hoặc từ chối có lý do; quyền `tickets` được cấp riêng. Xem [cách dùng cho thành viên và Admin](docs/duyet-doi-ca.md), [cấu hình và lệnh Zalo](docs/tickets-chat.md). Chế độ thử hai người `lab_live_test` vẫn giữ luồng cũ.

Bot hiển thị yêu cầu gọn và lệnh xác nhận bốn chữ số, ví dụ `/dongy 1234` hoặc `/tuchoi 1234` — dùng đúng mã bot gửi. Bước nhập, bản xem trước và mã xác nhận có tối đa 15 phút; yêu cầu đã gửi chờ tối đa 48 giờ và hết hạn sớm hơn khi ca bắt đầu. Không nhận được tin hoặc mã đã hết hạn: B gửi `/choxacnhan` để xem yêu cầu và lấy mã mới.

**Thử với lịch hiện có:** chế độ `lab_live_test` dành riêng cho hai người đã xác minh, chỉ cho chọn ca chưa bắt đầu. Ca hôm nay phải có giờ rõ ràng trong ô Buổi; nếu thiếu giờ, chỉ chọn ca từ ngày mai trở đi. B đồng ý sẽ **sửa thật hai ô D**; chế độ này vẫn bỏ kiểm tra trùng giờ. Xem [cấu hình và các bước thử](docs/tickets-chat.md#thử-nhanh-trên-lịch-thật-lab_live_test).

Chế độ `lab_live` mở đổi ca cho mọi thành viên đã duyệt, kiểm tra trùng giờ với phân công Lab Coach/Key Coach và chỉ đổi **hai ô Lab Coach ở cột D của lịch hiện tại**; không sửa Key Coach hoặc cột khác. Chỉ bật ghi sau khi xác nhận giờ buổi, có ca tương lai và thống nhất không sửa bảng đồng thời. Khi đăng ký Workload thành công qua tự duyệt hoặc quản trị duyệt, bot tự liên kết Zalo với thành viên đã đối chiếu. Gửi `/dangky` nhưng chưa thành công không cấp quyền đổi ca. Thông báo chỉ dành cho yêu cầu mới; các yêu cầu cũ vẫn xem bằng `/choxacnhan`. `sheets_test` với bảng thử riêng vẫn được giữ để kiểm thử. Thử hội thoại mô phỏng, không gọi Zalo/Google/model:

```powershell
.\scripts\run.ps1 TicketsChatDemo
```

Ngày 06/10/2026: đã kiểm tra đọc thật 3 tab Lịch lab và lệnh `/lich` cục bộ cho ba ngày thuộc ba tab. Tạo issue đang tắt trong cấu hình máy thử. Cần khởi động lại bot rồi gửi `/lich 2026-10-06` trên Zalo để xác nhận luồng trả lời thực tế.

## Chạy liên tục trên Railway

**Model LiteLLM / DeepSeek Flash:** [cấu hình kết nối, giới hạn token và xử lý 429](docs/model-litellm.md). Khóa nằm trong Railway Variables; không đưa file bàn giao vào Git.

Repo có Dockerfile, cấu hình Railway và mẫu biến môi trường. Xem [hướng dẫn deploy và chuyển dữ liệu Windows](docs/deploy-railway.md): dùng **một bot long polling + Volume `/data`**. Ngày 13/10/2026 đã build, chuyển dữ liệu và chạy trên Railway; người dùng xác nhận bot trả lời, nhận đúng thành viên đã đăng ký. Giữ bot Windows tắt khi Railway đang chạy. Chi phí Railway và AI phụ thuộc mức dùng. Bản triển khai hiện tại được tải từ mã cục bộ qua CLI; các thay đổi Docker/Railway chưa được commit/push lên GitHub.

## Trang Admin — Thành viên, Đổi ca, Issue & Hội thoại

Trang [Admin trên Vercel](https://hermes-zalo-bot-vercel-admin.vercel.app/) kết nối API Railway, có quản lý thành viên, duyệt đổi ca, issue, hội thoại, ngữ cảnh và ghi nhớ theo quyền riêng. API yêu cầu khóa máy chủ, đăng nhập và đúng quyền. Xem [hướng dẫn Admin](docs/admin.md) và [cấu hình Vercel](deploy/vercel-admin/README.md). `run.ps1 AdminDemo` mở dữ liệu giả trên máy, không gọi Zalo/Sheets/model; có sẵn yêu cầu đổi ca để thử duyệt. Quản trị thành viên không tự có quyền đọc chat riêng.

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

Đây là bước cấp quyền ban đầu cho quản trị. Nếu bật [đăng ký qua Workload](docs/dang-ky.md), thành viên mới tự nhắn riêng đăng ký; danh sách chờ duyệt có ID bot đã thu nhận. Không cần chạy `Discover` hoặc thu ID thủ công cho từng thành viên. Duyệt xong tự có quyền thành viên trong chat riêng: lịch, đổi ca, nhắc việc, góp ý; issue cần bật toàn hệ thống và tài liệu cần được chia sẻ. Không thêm từng thành viên vào `.env` hoặc khởi động lại sau mỗi lần duyệt. Người đã duyệt từ bản trước được áp dụng sau một lần cập nhật/khởi động lại.

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

Mở Zalo, dùng người đã cấp quyền gửi `/menu`, rồi hỏi lịch. Thử câu ngoài phạm vi như “Viết lời chúc sinh nhật”: bot phải trả hướng dẫn phạm vi, không viết lời chúc.

**Dừng:** bấm `Ctrl+C` trong cửa sổ đang chạy. **Bật lại:** chạy `Start`. Sau khi sửa `.env`, dừng rồi bật lại.

Biến môi trường đã đặt trong cửa sổ PowerShell được ưu tiên hơn `.env`. Nếu trước đó bạn đã đặt `ZALO_*` bằng tay, xóa các giá trị của cửa sổ hiện tại trước khi chạy lại để dùng `.env` (lệnh không xóa file hay token trên Zalo):

```powershell
Remove-Item Env:ZALO_BOT_TOKEN, Env:ZALO_ALLOWED_USERS, Env:ZALO_ALLOWED_GROUPS -ErrorAction SilentlyContinue
```

AI chỉ phân loại yêu cầu công việc theo schema; không có đường gọi model để trò chuyện tự do. Công cụ thao tác máy, duyệt web và các lệnh điều khiển Hermes qua Zalo bị vô hiệu hóa. Lệnh công việc do adapter xử lý trực tiếp. Phiên được tách theo loại chat, ID cuộc trò chuyện và ID người gửi.

## Các tính năng công việc

| Tính năng | Lệnh trên Zalo | Chuẩn bị |
| --- | --- | --- |
| Đăng ký thành viên | `/dangky SỐ_ĐIỆN_THOẠI`, `/dangky trangthai` trong chat riêng | Workload, quản trị duyệt, [hướng dẫn](docs/dang-ky.md); mặc định tắt |
| Tài liệu | `/tailieu`, `/hoi Câu hỏi` | `PrepareDocs` tạo mẫu; manifest cấp quyền từng file |
| Nhắc việc | `/nhac sau 1 phut \| Nội dung`, `/nhac xem`, `/nhac huy MA` | Bot/máy phải chạy; giữ trạng thái khi bật lại |
| Lịch | `/lich homnay`, `/lich YYYY-MM-DD` | Google Sheets, tab `Lich`, quyền đã cấp |
| Issue | `/issue tao Tiêu đề \| Mô tả`, `/issue xacnhan MA`, `/issue huy MA` | Google Sheets, tab `Issues`; bản nháp hết hạn 10 phút |
| Dùng thử | `/phanhoi Nội dung`, `/trogiup` | Chạy `Report` trên máy để tổng hợp |

Hướng dẫn cài, mẫu Sheets, quyền và giới hạn: [docs/huong-dan-tinh-nang.md](docs/huong-dan-tinh-nang.md). Bài thử cho nhóm nhỏ: [docs/pilot-checklist.md](docs/pilot-checklist.md).

Với `ZALO_ISSUES_MODE=managed`, Issue được quản lý trên trang Admin và lưu ở Volume Railway; không cần tạo tab `Issues` trên Sheets. Thành viên nhắn riêng, xem bản nháp rồi xác nhận trong 10 phút. Xem [Issue và đội ngũ hỗ trợ](docs/ISSUES.md). Chế độ `legacy` mới dùng tab Sheets `Issues`; menu không tự bật quyền ghi.

## 5. Thử trong nhóm Zalo

**Quyền nhóm còn cần xác minh trên bot thật.** Trang giới thiệu công bố Basic có 3 nhóm Beta, nhưng hướng dẫn nhóm vẫn ghi tính năng đang thử nghiệm nội bộ. Kiểm tra kết quả `can_join_groups` từ `ZaloCheck` và giao diện Bot Creator; không xem đây là quyền đã có sẵn cho mọi tài khoản. [Gói Basic](https://bot.zapps.me/), [hướng dẫn nhóm](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Khi tài khoản có tính năng:

1. Mở **Zalo Bot Creator**, chọn bot.
2. Tại **Mời Bot vào nhóm**, chia sẻ link vào nhóm thử.
3. **Trưởng nhóm** mở link và xác nhận thêm bot.
4. Chạy `Discover`, rồi **@mention bot** hoặc trả lời một tin bot đã gửi.
5. Lấy `chat_id` có `chat_type: GROUP` thêm vào `ZALO_ALLOWED_GROUPS` và `ZALO_SHEETS_GROUPS` để nhóm được tra lịch; giữ các ID cũ, ngăn cách bằng dấu phẩy. Dùng `ZALO_GROUP_ACCESS=members` để các thành viên được hỏi trong nhóm; chế độ `allowlist` yêu cầu thêm từng `user_id` vào `ZALO_ALLOWED_USERS`.
6. Chạy `Check`, sau đó `Start`; gửi một câu hỏi mới bằng @mention.

**Khi bot đang chạy trên Railway:** không chạy `Start` hoặc `Discover` trên Windows song song với server. Để lấy ID nhóm mới, người vận hành tạm đặt `BOT_START_ENABLED=false` ở service Railway và đợi deployment dừng polling; sau đó mới chạy `Discover` trên máy có đúng Bot Token. Gửi `@Bot LabCoach /menu` trong nhóm để lấy ID (Discover không trả lời). Dừng Discover, cập nhật hai danh sách nhóm trên **Railway Variables**, đặt lại `BOT_START_ENABLED=true` và đợi deployment thành công. Giữ bot Windows tắt. Việc này thực hiện một lần cho mỗi nhóm, không phải từng thành viên. `/ketnoinhom` không phải lệnh tích hợp thường trực của phiên bản hiện tại.

Trước khi mời cả nhóm dùng: kiểm tra Workload cột C là số điện thoại dạng văn bản đủ 10 số hoặc `+84…`, cột D là tên; thử 2–3 người đăng ký riêng, xem đúng tên và lịch, rồi thử `@bot /menu` và `@bot /lich homnay`. Đổi ca tiếp tục qua DM; người nhận đồng ý rồi Admin duyệt mới sửa Sheets. Xác minh hai mốc nhắc ca thực tế và sao lưu trước khi mở rộng. Nhóm nhiều người chưa được kiểm thử tải đồng thời; triển khai dần và theo dõi độ trễ/chi phí.

Giới hạn hiện tại: tối đa **5 lượt tra đăng ký/người/giờ và 60 lượt/toàn bot/giờ**, tính cả lần nhập sai. Chia đợt khoảng 20–30 người/giờ để còn lượt sửa lỗi; không mời hàng trăm người đăng ký đồng thời. Long polling xử lý tin tuần tự, câu trả lời AI chậm có thể làm các tin sau chờ. Zalo khuyến nghị webhook cho vận hành production; endpoint Admin Railway hiện tại không phải webhook nhận tin Zalo. Không tự điền URL Admin vào Bot Creator.

Bot nhận tin nhóm khi được nhắc tên hoặc có người trả lời tin của bot; không nhận toàn bộ hội thoại nhóm. ID dùng gửi về nhóm là `chat.id`. [Quy tắc tương tác nhóm](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

Nhóm luôn cần được cấp quyền. Với `members`, thành viên có thể hỏi dữ liệu đã chia sẻ và hỏi công việc Lab. Khi bật đăng ký Workload, người chưa đăng ký thành công chỉ nhận hướng dẫn nhắn riêng `/dangky SỐ_ĐIỆN_THOẠI`, dùng **số điện thoại hiện tại trên Workload**; đăng ký xong mới dùng bot trong nhóm. Nhắn riêng cần allowlist hoặc đăng ký đã duyệt đúng chat. Duyệt tự cấp quyền thành viên trong chat riêng: lịch, đổi ca, nhắc việc, góp ý và issue khi đã bật. Tài liệu dùng chung cần được chia sẻ rõ trong manifest; các quyền nhóm và tài liệu riêng giữ nguyên. Với `allowlist`, cả nhóm và người gửi đều phải có trong danh sách. **Không gửi số điện thoại đăng ký trong nhóm.** Dù ngữ cảnh model được tách theo người, **câu trả lời gửi trong nhóm vẫn hiển thị cho các thành viên của nhóm**.

## 6. Bài thử trước khi cho nhóm dùng

| Bài thử | Kết quả cần thấy |
| --- | --- |
| Người được cấp quyền nhắn riêng | Bot trả lời đúng cuộc trò chuyện |
| Người chưa được cấp quyền nhắn riêng | Không gọi Hermes; khi đã bật đăng ký Workload, chỉ tiếp nhận đăng ký/xem trạng thái |
| Hai người hỏi riêng nội dung khác nhau | Không lẫn ngữ cảnh hội thoại |
| Nhóm và người đều được cấp quyền @bot | Bot trả lời đúng nhóm |
| Nhóm chưa được cấp quyền @bot | Không xử lý câu hỏi |
| Người chưa có trong danh sách user @bot trong nhóm, chế độ `allowlist` | Không xử lý câu hỏi |
| Thành viên @bot trong nhóm được phép, chế độ `members` | Nếu bật đăng ký: chưa đăng ký chỉ nhận hướng dẫn nhắn riêng; đăng ký thành công mới tra dữ liệu đã chia sẻ hoặc hỏi công việc Lab; tác vụ ghi vẫn cần quyền riêng |
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
| `run.ps1 TicketsChatDemo` | Mô phỏng hội thoại đổi ca A → B → quản lý | Không dùng Zalo/Google/model thật |
| `run.ps1 TicketsLabCheck` | Đối chiếu tên trong CSV với cột Lab Coach | Google, chỉ đọc |
| `run.ps1 TicketsLinkUsers` | Kiểm tra tên rồi tạo liên kết Zalo đã xác minh | Google chỉ đọc; ghi tài khoản/liên kết trên máy |
| `run.ps1 Report` | Tổng hợp lượt dùng/lỗi/token/chi phí còn lưu | Không |
| `run.ps1 Demo` | Mô phỏng luồng xử lý | Không |
| `run.ps1 Test` | Kiểm thử tự động | Không dùng Zalo/model thật |
| `run.ps1 TestHermes` | Kiểm tra plugin với Hermes đã cài | Không dùng Zalo/model thật |
| `run.ps1 Check` | Kiểm tra cấu hình cục bộ | Không |
| `run.ps1 ZaloCheck` | Kiểm tra token/webhook | Zalo, chỉ đọc |
| `run.ps1 Discover` | Nhận event để lấy ID trong 60 giây | Zalo, không gửi tin/không AI |
| `run.ps1 Model` | Cấu hình nhà cung cấp/model | Tùy phương thức đăng nhập |
| `run.ps1 Start` | Chạy bot và các tính năng đã bật | Zalo, model và Google nếu đã cấu hình |

Chi tiết cấu trúc API, nguồn chính thức và những điểm chưa chắc chắn: [docs/api-contract.md](docs/api-contract.md).

Kết quả kiểm thử trên máy và các cảnh báo còn lại của bản Hermes đã cài: [docs/validation.md](docs/validation.md). Trước khi nâng Hermes, chạy lại cả `Test` và `TestHermes`; adapter đang được kiểm chứng với commit cố định ở trên.
