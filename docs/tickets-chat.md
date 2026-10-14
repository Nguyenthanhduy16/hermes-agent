# Đổi ca bằng tin nhắn riêng trên Zalo

Thành viên tạo và xác nhận đổi ca trong chat riêng với bot. Với `lab_live` và `TICKETS_REQUIRE_MANAGER=true`, luồng là **A gửi → B đồng ý → Admin duyệt trên trang quản trị → cập nhật Sheets**. B đồng ý chưa thay đổi lịch. Xem [hướng dẫn duyệt đổi ca](duyet-doi-ca.md).

Chế độ `lab_live` dành cho thành viên đã đăng ký thành công và liên kết đúng lịch. Cờ `TICKETS_REQUIRE_MANAGER=false` vẫn hỗ trợ luồng cũ chỉ cần hai người xác nhận; không dùng cờ này khi cần Admin duyệt.

Có ba chế độ dữ liệu, không dùng lẫn cấu hình:

| Chế độ | Phạm vi |
| --- | --- |
| `lab_live_test` | Thử đổi **hai ô D trên lịch thật** giữa hai người được chỉ định; chỉ ca chưa bắt đầu, không kiểm tra trùng giờ. Ca hôm nay cần giờ rõ ràng trong ô Buổi; thiếu giờ thì chỉ chọn ngày tương lai |
| `lab_live` | Mọi thành viên đã duyệt/liên kết; kiểm tra trùng giờ Lab Coach và Key Coach. Chỉ đổi hai ô **Lab Coach ở cột D** của yêu cầu đã xác nhận |
| `sheets_test` | Bảng thử riêng chuẩn hóa tám cột; giữ lại ở phần phụ lục để kiểm thử |

`lab_live` chỉ được bật ghi sau khi người quản lý lịch cung cấp **giờ bắt đầu và kết thúc chính xác cho từng buổi** và bảo đảm không ai sửa bảng đồng thời. Không suy ra giờ từ chữ “Sáng/Chiều/Tối”, tên ca hoặc giờ trong dữ liệu demo. Quản trị phải xác minh người thật; gửi `/dangky` nhưng chưa được duyệt không cấp quyền đổi ca.

## Cách cấp quyền hiện tại: đăng ký và duyệt ngay trong Zalo

Với `lab_live` hoặc `lab_live_test`, ưu tiên [đăng ký Workload](dang-ky.md):

1. Mỗi người nhắn **riêng** bot `/dangky SỐ_ĐIỆN_THOẠI` đã có trong Workload. Bot tự lưu ID người gửi và chat riêng.
2. Khi bật `REGISTRATION_AUTO_APPROVE=true`, số khớp duy nhất Workload được duyệt ngay. Nếu dùng duyệt thủ công, quản trị nhắn riêng `/dangkychoduyet`, xác minh rồi dùng `/duyetdangky DK-…`. Số có trong Workload không tự xác minh quyền sở hữu số.
3. Duyệt xong tự có các quyền thành viên trong chat riêng, gồm lịch và đổi ca theo chế độ đã bật. **Không thêm từng người vào `.env`, không tạo CSV, không chạy `Discover` hoặc import cho họ.** Người đã duyệt từ bản trước cũng được áp dụng sau một lần cập nhật/khởi động lại bot.
4. Khi Tickets lịch Lab đã bật, bot tự tạo liên kết Tickets sau khi quản trị duyệt; A dùng `/doica`, B nhận thông báo hoặc xem `/choxacnhan`. Nếu tra nguồn hoặc liên kết chưa thành công, quản trị nhận hướng xử lý; danh tính đã duyệt vẫn được giữ. Sau khi khắc phục, thành viên mở `/tickets` để thử liên kết lại.

Quản trị xem `/thanhvien` hoặc `/thanhvien 2`, sao chép `/thuhoidangky MÃ_THÀNH_VIÊN` khi cần thu hồi; không khởi động lại sau mỗi lần duyệt/thu hồi. Tên phải xác định duy nhất trên nguồn hiện tại; trùng tên hoặc dữ liệu danh tính thay đổi sẽ bị chặn để quản trị kiểm tra.

Duyệt thành viên mở quyền nhắc việc, góp ý, trò chuyện và issue khi tính năng issue đã bật/có tab đích hợp lệ trong chat riêng. Duyệt **không tự bật ghi lịch**, đổi chế độ, mở nhóm mới, tài liệu riêng hoặc cấp vai trò quản lý. Chế độ thử `lab_live_test` vẫn chỉ cho hai người đã cấu hình; muốn dùng nhiều người thật phải chuẩn bị chế độ `lab_live` cùng các điều kiện bên dưới. Cách liên kết CSV cũ chỉ cần khi không dùng đăng ký Workload; được ghi riêng ở mục 4.2 và phụ lục.

## Thử nhanh trên lịch thật: `lab_live_test`

Đợt thử này dành riêng cho **Trần Văn Tài và Ngô Đắc Lãm** đã được quản trị duyệt Workload và liên kết đúng Zalo theo luồng trên; không cần thêm hai ID vào `.env`. Nếu chưa dùng đăng ký Workload, có thể giữ cách liên kết thủ công cũ bên dưới. Đây là **ghi thật vào hai ô cột D**, không phải dữ liệu mô phỏng. Không ai chỉnh bảng đồng thời trong lúc thử.

Quản trị dừng bot, chọn chế độ sau trong `.env` trên máy:

```dotenv
TICKETS_CHAT_MODE=lab_live_test
TICKETS_REQUIRE_MANAGER=false
TICKETS_LAB_TEST_MEMBERS_JSON=["Trần Văn Tài","Ngô Đắc Lãm"]
TICKETS_LAB_TEST_WRITE_ENABLED=true
TICKETS_LAB_TEST_EXCLUSIVE_WRITER=true
```

Chế độ này dùng bảng/tab/khóa Google đã cấu hình cho lịch Lab; hai cờ thử ở trên độc lập với `TICKETS_LAB_WRITE_ENABLED` và `TICKETS_LAB_EXCLUSIVE_WRITER` của chế độ thường, nên giữ hai cờ thường ở `false`. Không bắt buộc cấu hình giờ chung trong chế độ thử này. Bot chỉ cho chọn **ca chưa bắt đầu**: nếu ô Buổi ghi giờ rõ ràng, bot kiểm tra theo giờ đó; nếu thiếu giờ, chỉ cho chọn ngày từ ngày mai trở đi, không đoán giờ của ca hôm nay. Chế độ thử vẫn **bỏ kiểm tra trùng giờ**, kể cả với Key Coach. Vì vậy, chỉ dùng để thử thao tác đã thống nhất; không coi kết quả là xác nhận lịch phù hợp cho việc đi làm. Kiểm tra cấu trúc, đúng người, đúng hai ô D và đọc lại kết quả vẫn giữ nguyên.

**Nếu dùng Workload, bỏ qua bước CSV sau:** bật đúng chế độ thử rồi duyệt thành viên để bot tự tạo liên kết. Người đã được duyệt từ trước có thể mở `/tickets` hoặc `/doica` để bot tự liên kết vào chế độ hiện tại nếu chưa có. Chế độ thử lưu riêng ở `TICKETS_DATA_DIR/lab_live_test.sqlite3`; liên kết hoặc yêu cầu của `lab_live` không tự dùng chung.

**Chỉ với cách thủ công cũ, khi đăng ký Workload đang tắt:** quản trị nhập danh tính đã xác minh vào đúng chế độ thử bằng lệnh sau, trong lúc bot đang dừng. Lệnh chỉ tạo tài khoản/liên kết trên máy, không đổi lịch:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli import-zalo-users --mode lab_live_test --csv data/tickets-users.csv
```

Với cách import thủ công này, CSV phải chứa đúng hai tên trong cấu hình và cả hai `user_id` phải có trong `ZALO_ALLOWED_USERS`. Điều kiện import không áp dụng cho luồng Workload đã duyệt tự liên kết ở trên.

Sau khi cấu hình và liên kết đã sẵn sàng, cập nhật và chạy bot:

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 Start
```

1. **Tài:** gửi `/doica` trong chat riêng, chọn ca của mình → chọn Lãm → chọn ca của Lãm → nhập lý do hoặc `bỏ qua`.
2. Đọc bản xem trước, đặc biệt **ngày, buổi và vị trí hai ô D**. Nếu đúng, sao chép nguyên lệnh `/doica gui MÃ_4_SỐ` bot đưa ra.
3. **Lãm:** nhận thông báo riêng từ bot, kiểm tra hai ca rồi gửi `/dongy MÃ_4_SỐ` hoặc `/tuchoi MÃ_4_SỐ` đúng như bot đưa ra. Không thấy tin hoặc mã hết hạn thì gửi `/choxacnhan`. **Đồng ý ở bước này bắt đầu sửa lịch thật.**
4. Hai người xem trạng thái và đối chiếu hai ô D trong bảng. Nếu kết quả chưa rõ, dùng `/doisoat` khi bot cung cấp; không gửi lại lệnh ghi.

Không cần web hoặc Mini App. Kết thúc thử, quản trị đặt `TICKETS_CHAT_MODE=off`, `TICKETS_LAB_TEST_WRITE_ENABLED=false` rồi khởi động lại bot. Tắt chế độ thử không tự hoàn tác hai ô đã đổi; muốn đổi lại cần một yêu cầu mới được hai người xác nhận.

## 1. Thành viên dùng như thế nào?

### A gửi yêu cầu

1. Mở **chat riêng** với bot, dùng mục **Đổi ca** trong `/menu` hoặc gửi `/doica` để bắt đầu chọn ca.
2. Bot liệt kê các ca **chưa bắt đầu** của bạn; gửi đúng **số chọn** hiển thị cạnh ca muốn đổi. `/tickets` vẫn mở menu đổi ca hoặc tiếp tục bước đang nhập.
3. Bot liệt kê người đổi cùng. Gửi số của B, rồi gửi số cạnh ca của B ở bước tiếp theo. Số chọn thay đổi giữa các bước: luôn dùng số trong danh sách mới nhất, không gửi lại số cũ theo thói quen.
4. Nhập lý do, hoặc `bỏ qua` nếu không muốn ghi lý do. Kiểm tra bản xem trước: hai người, hai ca, ngày giờ, khóa, phòng và lý do.
5. Nếu đúng, sao chép **nguyên lệnh `/doica gui MÃ_4_SỐ`** bot gửi. Nếu chưa đúng, dùng `quay lại` để chọn lại hoặc `thoát` để bỏ bản nháp.
6. Chờ bot báo **Đã gửi yêu cầu** và giữ mã yêu cầu để xem lại trạng thái. Bot xếp thông báo gửi riêng cho B đã được liên kết; tạo bản nháp chưa gửi thông báo. Tạo yêu cầu chưa làm thay đổi lịch; với `TICKETS_REQUIRE_MANAGER=false`, bot bắt đầu ghi khi B xác nhận đúng lệnh.

Danh sách dài: dùng lệnh **trang trước/trang tiếp** bot hiển thị, ví dụ `/doica trang 2`; hoặc nhắn `tiếp`/`trước`. Lệnh chuyển trang áp dụng cho danh sách đang chọn, không bắt đầu lại quy trình. Nhắn `tìm tên hoặc từ khóa` để lọc. Riêng `/doica` không kèm trang bắt đầu bản nháp mới và bỏ bản nháp cũ; lệnh này không hủy yêu cầu đã gửi. Muốn hủy yêu cầu đã gửi, mở yêu cầu và dùng lệnh `/huydoica` bot cung cấp nếu còn được phép.

### B xác nhận

- B nhận tin riêng có người đề nghị đổi, hai ca và các lệnh `/dongy MÃ_4_SỐ` hoặc `/tuchoi MÃ_4_SỐ`. Đọc kỹ nội dung rồi gửi một trong hai lệnh bot cấp. Đây là lệnh tin nhắn, bản này chưa dùng nút bấm.
- Nếu chưa thấy thông báo hoặc mã hết hạn, B gửi `/choxacnhan`. Nếu chỉ có một yêu cầu, bot mở ngay nội dung và lệnh xác nhận; nếu có nhiều yêu cầu, chọn bằng lệnh `/yeucau` dưới mục cần xem.
- Với cấu hình B là người xác nhận cuối (`TICKETS_REQUIRE_MANAGER=false`), B đồng ý sẽ bắt đầu cập nhật hai ô Lab Coach. Không cần tạo tài khoản quản lý hoặc chờ `/duyet`. Nếu dùng chế độ có quản lý ở đợt thử khác, quản lý mới dùng `/choduyet` và `/duyet` sau khi B đồng ý.
- Mở chi tiết bằng `/yeucau MÃ_YÊU_CẦU`. Bot gửi lệnh ngắn như `/dongy 1234`, kèm các thao tác khác theo quyền. Mã bốn chữ số gắn với đúng người, chat riêng và phiên bản yêu cầu; luôn dùng mã bot vừa cung cấp. Thiếu mã, sai mã hoặc gửi lại mã đã dùng không đổi lịch. Nếu bot báo nội dung đã thay đổi hoặc hết hạn, xem lại rồi lấy lệnh mới.
- Thông báo áp dụng cho yêu cầu mới gửi sau khi cập nhật bot; không tự gửi lại các yêu cầu cũ. A xem lại yêu cầu để biết kết quả; chưa có thông báo tự động cho A khi hoàn tất.
- Mã trong thông báo chỉ xử lý đúng yêu cầu được gửi kèm. Nhận tin hoặc xác nhận bằng mã này không bỏ bản nháp đổi ca khác B đang nhập; gửi `/tickets` để tiếp tục bản nháp còn hiệu lực.

**Để B nhận được tin:** B cần nhắn riêng với bot trước, được cấp quyền và liên kết đúng tài khoản/chat riêng. Bot phải đang chạy và Zalo cho phép gửi tin. Tin được gửi bằng [API `sendMessage` chính thức](https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage); mã xác nhận không tự thay đổi lịch. Nếu B chặn bot, thiếu quyền hoặc gửi tin có kết quả không rõ do mất mạng, yêu cầu vẫn còn để B xem bằng `/choxacnhan`; bot không tự gửi lặp một thông báo có kết quả chưa rõ.

**Thời hạn:** mỗi bước nhập, bản xem trước và lệnh xác nhận có tối đa **15 phút**. Hết hạn thì mở lại yêu cầu hoặc bắt đầu chọn lại để lấy mã mới. Yêu cầu **đã gửi** vẫn chờ phản hồi tối đa **48 giờ**, không bị rút xuống 15 phút; hết hạn sớm hơn khi một ca bắt đầu. Với ca thiếu giờ trong chế độ thử, chỉ xử lý trước ngày diễn ra ca. Khi xem yêu cầu thủ công hoặc xem trước lại, dùng mã của màn hình mới nhất; mã từ thông báo riêng vẫn gắn độc lập với đúng yêu cầu được gửi kèm và thời hạn của nó.

### Các lệnh cần nhớ

| Lệnh | Dùng để |
| --- | --- |
| `/tickets` | Xem menu đổi ca |
| `/doica` | Bắt đầu tạo yêu cầu theo từng bước |
| `/doica trang 2` | Sang trang 2 của danh sách đang chọn; dùng số trang bot hiển thị |
| `/yeucau` | Xem danh sách yêu cầu trong phạm vi được phép |
| `/yeucau MÃ_YÊU_CẦU` | Xem chi tiết, lịch sử và lệnh thao tác được phép |
| `/choxacnhan` | B xem các yêu cầu chờ mình xác nhận |
| `/lichsudoica` | Xem lịch sử đổi ca |
| `/lichsudoica MÃ_YÊU_CẦU trang 1` | Xem lịch sử thao tác của một yêu cầu, phân trang |
| `/choduyet` | Chỉ dùng trong chế độ có quản lý, không cần khi `TICKETS_REQUIRE_MANAGER=false` |
| `/dongy`, `/tuchoi` | B đồng ý hoặc từ chối theo lệnh bot gửi |
| `/huydoica` | A hủy khi trạng thái còn cho phép |
| `/duyet` | Chỉ dùng khi bật yêu cầu quản lý duyệt |
| `/doisoat` | Người được bot cấp thao tác kiểm tra lại kết quả ghi chưa rõ, chỉ đọc |

Với yêu cầu cụ thể, dùng đúng mã và lệnh đầy đủ bot hiển thị. Bot không coi câu “đồng ý” bất kỳ là quyền thay đổi lịch. Trong nhóm, bot chỉ hướng dẫn nhắn riêng; nội dung và thao tác Tickets không được xử lý tại chat nhóm.

Danh sách yêu cầu hỗ trợ phân trang, ví dụ `/yeucau trang 2`; tương tự `/choxacnhan trang 2`, `/choduyet trang 2` và `/lichsudoica trang 2`. Dùng lệnh xem tiếp bot gửi khi còn kết quả.

Chi tiết yêu cầu tập trung vào ca nhận sau khi đổi, trạng thái và lệnh thao tác. Lịch sử được mở riêng bằng `/lichsudoica MÃ_YÊU_CẦU`, mỗi trang tối đa **5 thao tác**, mới nhất trước; dùng lệnh trang tiếp bot cung cấp. Việc xem lịch sử vẫn kiểm tra quyền của A, B hoặc quản lý.

Nếu kết quả ghi **chưa rõ**, hai ca vẫn được giữ cho yêu cầu đó. Dùng `/doisoat` khi bot hiển thị thao tác này: bot chỉ đọc lại, không gửi lệnh đổi ca lần nữa. Nếu chưa khớp hoặc không có quyền đối soát, kiểm tra bảng và lịch sử với quản trị; không cố xác nhận lặp.

## 2. Thử hội thoại hoàn toàn cục bộ

Tại thư mục repo, chạy:

```powershell
.\scripts\run.ps1 TicketsChatDemo
```

Lệnh này mô phỏng hội thoại và dữ liệu thử ngay trên máy, không gửi tin Zalo, không gọi Google hoặc model AI. Đây là bài thử luồng xử lý, chưa xác minh bot hoặc quyền Google thật.

Nếu Windows PowerShell báo `running scripts is disabled`, chạy bằng chính sách chỉ áp dụng cho tiến trình đó:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 TicketsChatDemo
```

Các lệnh `run.ps1` bên dưới dùng cùng cách này khi cần, thay tên thao tác cuối. Không cần đổi chính sách thực thi toàn máy.

Không có tài khoản demo trên bot Zalo thật: `TICKETS_CHAT_MODE` nhận `off`, `sheets_test`, `lab_live` hoặc `lab_live_test`. Để `off` khi chưa hoàn tất cấu hình và cấp quyền. Dữ liệu hoặc giờ trong demo không phải giờ của lịch Lab thật.

## 3. Cấu hình lịch Lab thật: chỉ cột D

Chế độ `lab_live` đọc bảng và tab đã chọn bởi `ZALO_SHEETS_ID`, `ZALO_CALENDAR_TABS_JSON`, với `ZALO_CALENDAR_FORMAT=lab`; dùng khóa Google ở `ZALO_GOOGLE_CREDENTIALS`. Không đổi ID hoặc tab sang một bảng khác khi đang có yêu cầu chờ. Service account cần quyền sửa đúng bảng khi bật ghi; dữ liệu, token và file khóa vẫn chỉ giữ trên máy.

Các cờ mới trong `.env` trước khi hoàn tất giờ thực tế:

```dotenv
TICKETS_CHAT_MODE=off
TICKETS_DATA_DIR=.runtime/tickets
TICKETS_REQUIRE_MANAGER=false
TICKETS_LAB_WRITE_ENABLED=false
TICKETS_LAB_EXCLUSIVE_WRITER=false
TICKETS_LAB_PERIODS_JSON={}
```

Quản trị xác nhận giờ của **các buổi thực tế xuất hiện trong lịch**, gồm cả phân công Key Coach dùng kiểm tra trùng giờ. `TICKETS_LAB_PERIODS_JSON` nhận một hoặc nhiều tên **Sáng, Chiều, Tối**, mỗi buổi có hai khóa `start`, `end`; giờ theo Việt Nam, dạng `HH:MM`. Nếu chỉ có Sáng/Chiều thì không cần khai báo Tối. Ví dụ **cấu trúc cần điền, chưa phải cấu hình dùng được**:

```text
{"Sáng":{"start":"GIỜ_BẮT_ĐẦU","end":"GIỜ_KẾT_THÚC"},"Chiều":{"start":"GIỜ_BẮT_ĐẦU","end":"GIỜ_KẾT_THÚC"}}
```

Thay từng chỗ trống bằng giờ đã được người quản lý lịch xác nhận, không dùng giờ đoán. Ca tương lai phải có giờ rõ trong ô Buổi hoặc ánh xạ giờ tương ứng; thiếu cả hai thì bot chặn chọn/đổi ca, vẫn cho kiểm tra tên để chuẩn bị tài khoản. Nếu một tên buổi được dùng cho nhiều khung giờ khác nhau trong cùng lịch, phải làm rõ quy tắc trước khi bật; không ép chúng vào một khung giờ tùy ý.

Nếu ô Buổi ghi đầy đủ `Sáng HH:MM–HH:MM`, `Chiều HH:MM–HH:MM` hoặc `Tối HH:MM–HH:MM`, bot dùng giờ ghi rõ tại ô đó thay cho giờ chung của buổi. Dấu `-` cũng được chấp nhận; các mô tả như “khoảng” hoặc giờ thiếu sẽ không được suy đoán. Nếu tất cả ca cần kiểm tra đều ghi giờ rõ trong ô Buổi, có thể để ánh xạ chung `{}`. Không lấy giờ trong demo để điền vào lịch thật.

**Phạm vi ghi:** chương trình chỉ thay `Lab Coach` ở cột D của đúng hai dòng ca đã xác nhận. Không ghi Key Coach, trạng thái, ngày, khóa, phòng, công thức hoặc định dạng; không thêm cột/mã vào lịch thật. Ô D có dữ liệu không thể xác định thành một người duy nhất phải được kiểm tra, không tự tách hoặc đoán tên.

Mã ca được gắn với tab, dòng và dấu kiểm tra cấu trúc lịch. Khi thêm/xóa/di chuyển dòng, đổi ô gộp hoặc sửa dữ liệu ngoài cột D trong phạm vi đang dùng, yêu cầu cũ có thể không còn hợp lệ: xem lại và tạo yêu cầu mới. Không cố tái sử dụng mã ca hoặc xác nhận cũ sau khi sửa lịch.

### Vì sao cần một bên duy nhất sửa bảng?

Hai ô D được gửi trong cùng một lệnh `spreadsheets.batchUpdate`, nên Google bảo đảm các cập nhật trong lệnh đó được áp dụng cùng nhau. Tuy nhiên, Google cũng nói các thay đổi đồng thời của người cộng tác có thể làm kết quả khác đi. Đọc lại trước/sau không khóa được thao tác sửa tay xen giữa. [Tài liệu batchUpdate chính thức](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)

`UpdateCellsRequest` không có điều kiện so sánh giá trị cũ hoặc phiên bản ô trong cấu trúc yêu cầu được công bố; không thể coi batch ghi là thao tác “chỉ ghi nếu lịch chưa thay đổi” đối với người sửa trực tiếp. [Cấu trúc UpdateCellsRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#UpdateCellsRequest)

Vì vậy, chỉ đặt `TICKETS_LAB_EXCLUSIVE_WRITER=true` khi đã thống nhất **một tiến trình Tickets là bên duy nhất ghi bảng trong lúc đổi ca**. Người dùng và automation khác phải ngừng chỉnh sửa đồng thời; cờ này không tự khóa giao diện Google Sheets. Không đáp ứng được điều kiện này thì giữ ghi tắt. Trước và sau khi sửa cấu trúc lịch thủ công, dừng bot, xử lý các yêu cầu chờ rồi tạo yêu cầu mới trên lịch đã kiểm tra.

## 4. Liên kết thành viên Zalo cho `lab_live`

### 4.1. Cách hiện tại: duyệt đăng ký Workload

Làm theo phần đăng ký và duyệt ở đầu trang. Bot lấy ID thật từ tin nhắn riêng, tự lưu quyền thành viên và tự tạo liên kết Tickets khi duyệt trong chế độ lịch Lab đã bật. Không cần điền danh sách thành viên trong `.env` hoặc chuẩn bị `tickets-users.csv`. Quản trị vẫn cấu hình nguồn lịch, chế độ ghi và giờ ca một lần cho hệ thống. Cả A và B phải được duyệt và liên kết thành công trước khi gửi yêu cầu; nếu bot báo lỗi nguồn khi duyệt, sửa lỗi rồi để người đó mở `/tickets` thử lại. Người đã được duyệt từ trước cũng có thể mở `/doica` để tự liên kết, không cần đăng ký lại.

### 4.2. Cách thủ công cũ: chỉ khi chưa dùng đăng ký Workload

Dừng bot/web đang dùng dữ liệu Tickets trước khi quản trị theo cách thủ công. Dùng `Discover` như hướng dẫn README để lấy `user_id` và `chat_id` từ **tin nhắn riêng thật** của từng người; quản trị xác minh đó là Tài và Lãm. Không suy ra danh tính từ tên hiển thị hoặc đăng ký tên tự khai cũ.

Tạo file UTF-8 `data/tickets-users.csv` trên máy theo cấu trúc sau, thay các ID mẫu bằng ID thực đã xác minh:

```csv
nguoi,user_id,chat_id,member_name
A,USER_ID_A,CHAT_ID_A,Trần Văn Tài
B,USER_ID_B,CHAT_ID_B,Ngô Đắc Lãm
```

File này chứa liên kết danh tính, giữ trong `data/` đã loại khỏi Git; không gửi lên chat hoặc GitHub. `member_name` phải khớp đúng tên Lab Coach đọc được ở cột D, không dùng so khớp tên gần giống. A/B là nhãn chuẩn bị danh sách, không phải quyền được người dùng tự nhận.

Kiểm tra tên trên bảng hiện tại, chỉ đọc và không đổi lịch:

```powershell
.\scripts\run.ps1 TicketsLabCheck
```

Với cách thủ công cũ này, sau khi kết quả khớp đúng cả hai người, nhập cả hai `user_id` đã kiểm tra vào `ZALO_ALLOWED_USERS` trong `.env` và lưu lại. Bước import sẽ từ chối nếu thiếu quyền và không tự thêm ID; thành viên đã duyệt Workload dùng mục 4.1 thay cho bước này. Sau đó nhập tài khoản và liên kết Zalo vào dữ liệu cục bộ:

```powershell
.\scripts\run.ps1 TicketsLinkUsers
```

Hai lệnh chuẩn bị này không cần bật ghi hoặc điền giờ. Kiểm tra tên chỉ đọc Google; import chỉ ghi tài khoản/liên kết trên máy, không đổi lịch. Chạy lại cùng danh sách đã nhập không tự tạo danh tính mới hoặc làm mất yêu cầu đang chờ. Dữ liệu `lab_live` lưu riêng ở `TICKETS_DATA_DIR/lab_live.sqlite3`, không trộn với `sheets_test` hoặc demo.

Cả hai lệnh dùng `data/tickets-users.csv`. Nếu quản trị cần chọn file khác, lệnh đầy đủ là `python -m hermes_zalo.tickets.cli check-lab --csv ĐƯỜNG_DẪN_CSV` và `python -m hermes_zalo.tickets.cli import-zalo-users --mode lab_live --csv ĐƯỜNG_DẪN_CSV`, chạy bằng Python trong `.venv` của dự án.

Liên kết Tickets gắn với đúng người gửi, đúng chat riêng và bot hiện tại; đổi bot hoặc ID phải được quản trị xác minh lại. Với `TICKETS_REQUIRE_MANAGER=false`, không cần tạo tài khoản quản lý.

## 5. Bật `lab_live` sau khi đủ điều kiện

Chỉ thực hiện sau khi đã có giờ thực tế, thành viên đã được xác minh/duyệt, quyền Google phù hợp và thỏa thuận không sửa bảng đồng thời. Đây là chế độ mở cho mọi thành viên đã duyệt, không phải danh sách hai người thử. Giữ nguyên giá trị `TICKETS_LAB_PERIODS_JSON` đã điền đúng giờ, rồi đặt:

```dotenv
TICKETS_CHAT_MODE=lab_live
TICKETS_REQUIRE_MANAGER=false
TICKETS_LAB_WRITE_ENABLED=true
TICKETS_LAB_EXCLUSIVE_WRITER=true
```

`lab_live` có cơ sở dữ liệu riêng; yêu cầu đang chờ trong `lab_live_test` không chuyển sang chế độ mới. Hoàn tất/hủy các yêu cầu thử trước khi chuyển, giữ dữ liệu cũ để tra lịch sử. Khi mở Tickets, bot tự đối chiếu và khôi phục liên kết cho mọi thành viên đã duyệt; người đổi cùng không cần mở bot trước hoặc đăng ký lại. Dữ liệu từng người sai/trùng sẽ được bỏ qua, không chặn người hợp lệ. Lỗi kết nối sẽ thử lại sau 60 giây; sau khi sửa dữ liệu riêng của một người, dùng `/tickets` hoặc gửi lại lệnh duyệt để thử liên kết.

Cập nhật và chạy bot tại thư mục repo:

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 Start
```

A gửi `/doica`, kiểm tra đúng ca và người; B đọc thông báo riêng (hoặc `/choxacnhan`), kiểm tra hai ca rồi gửi nguyên lệnh xác nhận bot đưa ra. Bước xác nhận của B **ghi lịch thật**, chỉ làm khi cả hai thực sự muốn đổi hai ca đó. Kiểm tra kết quả bot đọc lại và hai ô D trong bảng. Không cần mở web và không chạy `TicketsDemo`/`TicketsTest` cùng cơ sở dữ liệu.

Chỉ ca **chưa bắt đầu** mới được chọn. Nếu lịch của A/B chỉ còn các ca đã qua, bot không tự dời ngày hoặc thêm ca để có dữ liệu thử; người quản lý lịch cần xếp ca tương lai thực tế trước khi dùng chức năng đổi ca.

Timeout hoặc mất kết nối sau khi gửi cập nhật không chứng minh ghi thất bại. Nếu bot báo chưa rõ kết quả, A hoặc B mở yêu cầu, dùng lệnh `/doisoat` có mã xác nhận bot cung cấp để đọc lại; không gửi lại lệnh ghi và không sửa tay hai ô khi chưa biết kết quả. Thao tác đối soát chỉ đọc, không thay thế một lần đồng ý mới. Kiểm thử cục bộ hoặc kiểm tra đọc không chứng minh đã đổi thành công trên lịch thật.

### Lưu ý quản trị khi nâng cấp mã xác nhận

Mỗi chế độ trong một cơ sở dữ liệu có **1.000 mã bốn chữ số** để cấp mới. Mã không được tái sử dụng; khi hết mã, bot dừng cấp xác nhận mới. **Không xóa cơ sở dữ liệu để lấy lại mã**, vì còn chứa yêu cầu, liên kết tài khoản và lịch sử. Mã sáu chữ số đã cấp trước khi nâng cấp vẫn dùng được đến hạn ban đầu; mở lại yêu cầu để nhận mã bốn chữ số mới.

## Phụ lục A. Bảng thử chuẩn hóa `sheets_test`

Các bước dưới đây dành riêng cho chế độ bảng thử cũ với liên kết thủ công, khi đăng ký Workload đang tắt. Tự liên kết Workload hiện chỉ áp dụng cho `lab_live`/`lab_live_test`, không tự ghép sang bảng `sheets_test` có nguồn và mã riêng. Các bước này không thay thế cấu hình `lab_live` ở trên và không dùng để ghi bảng Lab hiện tại.

Tạo một spreadsheet **khác ID với `ZALO_SHEETS_ID`** của lịch Lab đang đọc. Đặt tab `TicketsTest`. Bảng sao chép nguyên xi còn ô gộp không dùng được; chuẩn hóa một số ca thử thành đúng tám cột A:H:

| assignment_id | member_id | member_name | start | end | course | room | period |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shift-a-001 | member-a | Coach A | 2026-10-20T08:00:00+07:00 | 2026-10-20T10:00:00+07:00 | Track 1 | H201 | sáng |
| shift-b-001 | member-b | Coach B | 2026-10-21T08:00:00+07:00 | 2026-10-21T10:00:00+07:00 | Track 1 | H202 | sáng |

Đây là dữ liệu minh họa; chọn ngày tương lai và tên được phép dùng. Định dạng tám cột thành **văn bản thuần** trước khi dán. Ngày giờ phải có múi giờ như mẫu, giờ kết thúc sau giờ bắt đầu. Không dùng công thức, ô gộp hoặc ngày dạng số.

- `assignment_id`: mã duy nhất của ca, giữ nguyên khi sắp xếp lại dòng.
- `member_id`: mã ổn định của một người; cùng người dùng cùng mã và tên nhất quán. Mã này không phải tên người dùng tự khai trong Zalo.
- Tối đa 1.000 ca, tab tối đa 1.001 dòng kể cả tiêu đề. Dịch vụ đổi người phụ trách của hai ca; mã ca, ngày giờ, khóa, phòng và buổi giữ nguyên.

Chia sẻ bảng thử cho service account dùng riêng cho Tickets. Cất file khóa trong `secrets/`; không gửi khóa, Bot Token hoặc API key vào chat.

### A.1. Cấu hình bảng thử trên máy quản trị

Cài hỗ trợ Google nếu chưa có:

```powershell
.\scripts\run.ps1 SetupSheets
```

Thêm các mục sau vào `.env` trên máy. Điền ID bảng thử thật vào trường trống; giữ `ZALO_SHEETS_ID` là ID lịch Lab hiện tại để kiểm tra hai bảng khác nhau:

```dotenv
TICKETS_CHAT_MODE=off
TICKETS_DATA_DIR=.runtime/tickets
TICKETS_REQUIRE_MANAGER=true
TICKETS_TEST_SHEETS_ID=
TICKETS_TEST_SHEETS_TAB=TicketsTest
TICKETS_TEST_GOOGLE_CREDENTIALS=secrets/tickets-test-service-account.json
TICKETS_TEST_WRITE_ENABLED=false
TICKETS_TEST_EXCLUSIVE_WRITER=false
```

`TICKETS_REQUIRE_MANAGER=true` là mặc định A → B → quản lý. Chỉ đặt `false` khi nhóm đã thống nhất B là người xác nhận cuối.

Dữ liệu tài khoản, liên kết Zalo, yêu cầu và hội thoại được lưu trong thư mục `TICKETS_DATA_DIR`; mặc định `.runtime/tickets`. Giữ thư mục này ngoài Git và dùng cùng một thư mục khi quản trị tài khoản và khi chạy bot.

### A.2. Xác minh và cấp quyền cho chế độ bảng thử

**Dừng bot hoặc web đang dùng dữ liệu Tickets trước khi quản trị tài khoản và liên kết.** Sau khi hoàn tất, khởi động lại bot. Quản trị làm riêng cho A, B và quản lý:

1. Xác minh người thật và mã `member_id` trong bảng thử. Vai trò quản lý do quản trị cấp, không tự nhận qua tin nhắn.
2. Tạm dừng bot nhận tin. Chạy `.\scripts\run.ps1 Discover`, yêu cầu đúng người gửi một tin **riêng** mới cho bot; ghi lại `user_id` và `chat_id` được hiển thị trên máy. Lặp lại nếu cần. Không chạy `Discover` cùng lúc với `Start`.
3. Với chế độ bảng thử và liên kết thủ công trong phụ lục này, thêm các `user_id` đã kiểm tra vào `ZALO_ALLOWED_USERS` trong `.env`, ngăn cách bằng dấu phẩy. Đây không phải bước cho thành viên Workload đã duyệt dùng lịch Lab.
4. Tạo tài khoản Tickets dùng chat, rồi liên kết đúng tài khoản với `user_id` và `chat_id` riêng của người đó. Liên kết còn gắn với bot hiện tại trong `.env`; đổi Bot Token cần quản trị kiểm tra và liên kết lại.

Lệnh tạo tài khoản chỉ dùng chat, không yêu cầu đặt mật khẩu web:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --mode sheets_test --username coach-a --name "Coach A" --member-id member-a --role member --chat-only
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --mode sheets_test --username coach-b --name "Coach B" --member-id member-b --role member --chat-only
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli add-user --mode sheets_test --username manager --name "Quan ly" --role manager --chat-only
```

Thay tên và mã bằng giá trị đã xác minh. Tài khoản thành viên cần mã tồn tại trong bảng thử; quản lý không cần mã ca. Tên từ `/dangky`, việc vào nhóm Zalo hoặc tự gửi mã thành viên không tạo liên kết này.

Chạy lại `add-user` cho tài khoản có sẵn là cập nhật tài khoản và xóa liên kết Zalo cũ. Vì vậy, sau khi cập nhật hoặc cấp lại tài khoản, quản trị phải xác minh và chạy `bind-zalo` lại; liên kết cũ không tự phục hồi. `revoke-user` cũng xóa liên kết Zalo.

Liên kết từng tài khoản với ID lấy từ tin nhắn riêng. Thay toàn bộ các giá trị `USER_ID_...` và `CHAT_ID_...` bằng ID thật đã kiểm tra trên máy:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli bind-zalo --mode sheets_test --username coach-a --user-id USER_ID_A --chat-id CHAT_ID_A
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli bind-zalo --mode sheets_test --username coach-b --user-id USER_ID_B --chat-id CHAT_ID_B
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli bind-zalo --mode sheets_test --username manager --user-id USER_ID_QUAN_LY --chat-id CHAT_ID_QUAN_LY
```

Các lệnh mặc định đọc `.env`; `--env-file` chọn file cấu hình khác khi cần. Nếu bỏ `--data-dir`, lệnh dùng `TICKETS_DATA_DIR` hoặc `.runtime/tickets`. Bản chat không yêu cầu người dùng gửi mật khẩu, token hoặc mã tự cấp vào Zalo.

Khi cần gỡ riêng liên kết Zalo, dùng đúng cặp ID và Bot Token trong file cấu hình lúc liên kết:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli unbind-zalo --mode sheets_test --user-id USER_ID_A --chat-id CHAT_ID_A
```

Khi cần thu hồi tài khoản Tickets hoàn toàn:

```powershell
.\.venv\Scripts\python.exe -m hermes_zalo.tickets.cli revoke-user --mode sheets_test --username coach-a
```

Với liên kết thủ công khi đăng ký Workload đang tắt, quyền nhắn bot trong `ZALO_ALLOWED_USERS` vẫn độc lập; nếu cần thu hồi cả quyền bot, quản trị sửa danh sách đó và khởi động lại bot. Còn với thành viên Workload đã duyệt, dùng `/thanhvien` và `/thuhoidangky MÃ_THÀNH_VIÊN` ngay trong chat riêng; không cho thành viên tự chọn hoặc nhận liên kết của người khác.

### A.3. Bật chế độ bảng thử trên Zalo

Chỉ bật ghi khi **bảng thử dành riêng cho một tiến trình Tickets**. Google Sheets không có thao tác bảo đảm “chỉ ghi nếu các ô vẫn bằng giá trị đã đọc” để ngăn người khác sửa trực tiếp đúng lúc dịch vụ ghi. Vì vậy, `TICKETS_TEST_EXCLUSIVE_WRITER=true` là cam kết vận hành: không người, ứng dụng hoặc automation khác chỉnh sửa bảng thử đồng thời; biến này không khóa giao diện Google Sheets.

Sau khi bảng và quyền đã sẵn sàng, chủ động đặt:

```dotenv
TICKETS_CHAT_MODE=sheets_test
TICKETS_TEST_WRITE_ENABLED=true
TICKETS_TEST_EXCLUSIVE_WRITER=true
```

Nếu giữ `TICKETS_TEST_WRITE_ENABLED=false`, dịch vụ không được ghi đổi ca vào Sheets. Cập nhật plugin và khởi động lại bot để nhận cấu hình:

```powershell
.\scripts\run.ps1 Update
.\scripts\run.ps1 Start
```

Tắt cửa sổ web Tickets cũ nếu đang dùng cùng cơ sở dữ liệu. Chỉ một tiến trình được giữ dữ liệu Tickets tại một thời điểm; không chạy song song bot và web với cùng thư mục dữ liệu. Không cần chạy `TicketsDemo`, `TicketsTest` hoặc mở trình duyệt cho luồng chat.

**Bài thử thực tế:** A tạo yêu cầu → B nhận tin riêng, kiểm tra và đồng ý → quản lý xem `/choduyet` và duyệt → cả hai kiểm tra yêu cầu và bảng thử. Nếu chưa thấy tin, B xem `/choxacnhan`. Thử thêm tài khoản chưa liên kết, sai chat, từ chối, hủy và thao tác với xác nhận cũ. Chỉ kết luận Zalo/Google hoạt động sau khi bài thử này hoàn tất; không suy ra từ demo cục bộ.

## Phụ lục B. Xử lý các tình huống thường gặp

| Tình huống | Cách xử lý |
| --- | --- |
| Bot yêu cầu nhắn riêng | Mở chat riêng với đúng bot rồi gửi `/tickets` |
| Chưa có quyền hoặc chưa liên kết | Dùng Workload: xem `/dangky trangthai`, quản trị kiểm tra `/thanhvien` và lỗi liên kết khi duyệt; sau khi khắc phục, thành viên thử lại `/tickets`, không thêm ID vào `.env`. Với cách thủ công cũ: kiểm tra `ZALO_ALLOWED_USERS` và liên kết đúng người gửi/chat riêng/bot |
| Bản cũ báo cần cấp quyền bot và Sheets dù đã duyệt | Cập nhật và khởi động lại bot để nạp quyền thành viên tự động; không cần đăng ký lại hoặc thêm từng ID vào `.env` |
| Chưa bật Tickets | Hoàn tất cấu hình và quyền của chế độ định dùng, cập nhật và khởi động lại bot; riêng `lab_live_test` không yêu cầu cấu hình giờ |
| B chưa thấy thông báo | B gửi `/choxacnhan`; kiểm tra A đã nhận “Đã gửi yêu cầu”, bot đang chạy, B không chặn bot và đã liên kết đúng chat riêng. Không tự gửi thông báo cho các yêu cầu cũ |
| Quản lý chưa thấy yêu cầu | Chỉ áp dụng nếu `TICKETS_REQUIRE_MANAGER=true`; cấu hình `false` để B là người xác nhận cuối |
| Ca đã đổi hoặc xác nhận hết hạn | Xem lại yêu cầu và dùng lệnh mới bot gửi |
| Không có ca để chọn | Kiểm tra đúng tên, liên kết và ca chưa bắt đầu. `lab_live` cần giờ rõ trong ô Buổi hoặc ánh xạ buổi phù hợp; `lab_live_test` thiếu giờ thì chỉ chọn từ ngày mai trở đi. Bot không tự dời ngày hoặc thêm ca |
| Đang có tiến trình Tickets khác | Dừng cửa sổ bot/web cũ sử dụng cùng dữ liệu; không xóa file khóa khi tiến trình đang chạy |
| Kết quả ghi chưa rõ | Dùng `/doisoat` nếu bot cấp thao tác hoặc nhờ quản trị kiểm tra; không tự ghi lại |

Bản giao diện web cũ chỉ còn là [phụ lục thử nghiệm cục bộ](tickets.md), không thuộc các bước dùng Tickets trong Zalo.
