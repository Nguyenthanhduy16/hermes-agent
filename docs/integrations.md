# Tích hợp: Connectors (MCP) và Reports

Nhóm **Integrations** trong Agent Console có hai trang:

- **Connectors** — danh mục các kết nối (MCP server) cho mạng xã hội, Zalo OA,
  Telegram, Google, Notion, GitHub và MCP tùy chỉnh. Từ đây chủ bot cài đặt,
  nhập khóa, bật/tắt, gỡ bỏ và kiểm tra kết nối.
- **Reports** — báo cáo định kỳ (ví dụ “Tóm tắt mạng xã hội hằng ngày”, “Báo cáo
  Zalo OA hằng tuần”) được tạo thành các job lịch chạy của Hermes.

Mọi thay đổi đều đi qua các dịch vụ sẵn có của console (trang Tools (MCP), API
keys và Schedules), nên áp dụng đúng cùng một bộ quy tắc an toàn và cùng cơ chế
revision chống ghi đè. Trang chỉ dành cho **chủ hệ thống (system owner)** và mọi
thao tác ghi đều được ghi nhật ký kiểm toán (chỉ tên trang và tên thao tác, không
bao giờ ghi giá trị khóa).

## 1. Trạng thái của một connector

| Trạng thái | Ý nghĩa |
| --- | --- |
| **Available** | Có thể cài ngay (MCP server từ xa) hoặc đã đủ khóa cho server cục bộ. |
| **Needs credentials** | Thiếu biến môi trường mà cấu hình cần (ví dụ `${NOTION_TOKEN}` chưa có trong `.env`). |
| **Installed (disabled)** | Đã có trong `config.yaml` nhưng đang tắt. |
| **Enabled** | Đã bật; Hermes nạp công cụ của server ở lần khởi động gateway tiếp theo. |
| **Bridge needed** | Nền tảng chưa có MCP server chính thức; cần tự chạy một “bridge”. |

Trạng thái được tính từ `config.yaml` (`mcp_servers`) và sự **có mặt** của biến
trong `.env` của bot home (hoặc biến môi trường của tiến trình). Giá trị khóa
không bao giờ được trả về trình duyệt.

Mỗi thẻ còn ghi rõ **agent có thể làm gì** với nhãn rủi ro:
*Read only*, *Changes data*, *Can post publicly*, *Can send messages*. Hãy ưu tiên
token chỉ đọc cho báo cáo.

## 2. Thêm một connector

### MCP server từ xa (HTTP)
1. Mở **Connectors**, chọn thẻ (ví dụ **GitHub**) → **Install GitHub**.
2. Tên và URL đã được điền sẵn; nhập **Bearer token** (ô mật khẩu, chỉ ghi).
   Token được lưu vào `.env` của bot home dưới tên `MCP_<TÊN>_TOKEN` và cấu hình
   chỉ tham chiếu `Authorization: Bearer ${MCP_<TÊN>_TOKEN}`.
3. Nên để **Enable now** tắt, bấm **Test <tên>** để xem danh sách công cụ, rồi mới bật.
4. Khởi động lại gateway để Hermes áp dụng.

URL phải là `https://` (chỉ cho phép `http://` với localhost). Không đặt mật khẩu
hay biến `${...}` trong URL.

### MCP server cục bộ (stdio) — “install on the server”
Console **không bao giờ chạy tiến trình**. Thẻ loại này (Google Analytics, Notion)
hiển thị đoạn cấu hình mẫu. Người vận hành chép đoạn đó vào `config.yaml` trên
máy chủ (để `enabled: false`), đặt khóa (ở console với biến được phép, hoặc trên
máy chủ), rồi bật bằng nút **Enable** trên console.

### Nền tảng cần “bridge”
Facebook Pages, Instagram, Threads, X, TikTok, YouTube, LinkedIn, **Zalo
Official Account**, Telegram channel, Search Console, Gmail, Google Calendar,
Google Drive/Sheets hiện **không có MCP server chính thức** mà chúng tôi xác nhận
được. Thẻ mô tả API chính thức mà bridge cần bọc và đường dẫn tài liệu.

Riêng **Zalo OA**: Zalo có Open API REST chính thức (developers.zalo.me) nhưng
không có MCP server chính thức. Access token của OA hết hạn, bridge phải tự làm
mới bằng refresh token. `ZALO_OA_ACCESS_TOKEN` khác với `ZALO_BOT_TOKEN` của bot.

Khi đã chạy bridge (ví dụ `http://127.0.0.1:8790/mcp`), bấm **Install bridge URL**
trên thẻ tương ứng — từ đó nó được quản lý như mọi MCP server khác. Nên bắt đầu
với công cụ chỉ đọc (thống kê, bình luận); chỉ thêm công cụ đăng bài/gửi tin khi
thật cần.

## 3. Khóa và thông tin xác thực

- Ô khóa luôn là **chỉ ghi**: không hiển thị lại, không điền sẵn.
- Console chỉ ghi các biến mà danh mục khai báo rõ (ví dụ `NOTION_TOKEN`,
  `META_PAGE_ACCESS_TOKEN`, `ZALO_OA_ACCESS_TOKEN`) và có dạng
  `..._TOKEN/_KEY/_SECRET/_CLIENT_ID`. Các tên nhạy cảm (`PATH`, `HERMES_*`,
  `LD_*`, `ZALO_BOT_TOKEN`, …) luôn bị từ chối. Khóa nhà cung cấp mô hình vẫn quản
  lý ở trang **API keys**.
- Biến chỉ là đường dẫn tệp trên máy chủ (như `GOOGLE_APPLICATION_CREDENTIALS`)
  được đánh dấu “Set on the server” và không sửa được từ console.
- **Replace token** chỉ có cho token do console tạo (`MCP_<TÊN>_TOKEN`). Server có
  header tự viết thì sửa trên máy chủ, hoặc gỡ và cài lại.
- Hermes đọc `.env` khi khởi động: mọi thay đổi cần **khởi động lại gateway**.

## 4. Quy tắc cô lập (isolation rule)

Hermes đưa công cụ của mọi MCP server đang bật cho **mọi kênh chat** không liệt kê
`no_mcp` trong `platform_toolsets`. Bot này phục vụ nhiều người, nên console
**từ chối bật** một server nếu người dùng chat (Zalo, Telegram, …) sẽ nhận được
công cụ của nó; server vẫn có thể lưu ở trạng thái tắt. Thẻ hiển thị cảnh báo
“Chat users on … would receive its tools”. Muốn bật, người vận hành thêm `no_mcp`
vào toolsets của các kênh đó bằng script cài đặt trước.

## 5. Test connection

Nút **Test <tên>** (cần xác nhận) gửi một lần `initialize` + `tools/list` của MCP
(Streamable HTTP) tới đúng URL đã cấu hình:

- chỉ HTTPS, hoặc HTTP tới localhost; không dùng proxy; **không theo redirect**;
- giới hạn 5 giây mỗi bước, 15 giây tổng, phản hồi tối đa 256 KB;
- header cấu hình (ví dụ Authorization) chỉ gửi tới chính server đó;
- kết quả (số công cụ, tên công cụ) chỉ giữ trong bộ nhớ, không ghi đĩa.

Không có kiểm tra tự động nào khác; OAuth không được thực hiện bởi console.

## 6. Báo cáo (Reports)

Mỗi mẫu báo cáo có mô tả, các connector cần dùng (kèm trạng thái sẵn sàng), và
form: tên tài khoản/trang, chỉ số tập trung, lịch chạy, ngôn ngữ (Tiếng Việt /
English), ghi chú thêm, tạo ở trạng thái tạm dừng. Nếu chưa có connector nào bật,
báo cáo mặc định được tạo **tạm dừng**.

- Lịch chạy dùng cú pháp của trang Schedules: cron 5 trường (`0 8 * * *`), khoảng
  (`every 1d`) hoặc cụm từ (`every monday 8am`).
- Job được đặt tên `Report · <mẫu> · <tài khoản>` và hướng dẫn bắt đầu bằng dòng
  `Hermes report: <mã mẫu>` — đây là cách trang Reports nhận biết job của mình.
  Đổi tên job ở trang Schedules sẽ làm nó biến khỏi trang Reports (vẫn chạy bình thường).
- Hướng dẫn tạo ra yêu cầu báo cáo có các mục rõ ràng (Tóm tắt, Chỉ số chính, …,
  Thiếu dữ liệu), **chỉ đọc** (không đăng bài, trả lời, gửi tin hay sửa dữ liệu),
  không bịa số liệu và không đưa dữ liệu cá nhân.
- **Báo cáo đi đâu?** Giao nhận luôn là `local`: mỗi lần chạy lưu một tệp Markdown
  trong `<bot home>/cron/output/<job id>/`. Trang Reports và Schedules hiển thị
  đoạn trích (đã che thông tin nhạy cảm). Không có gì được gửi cho ai.
- Job lịch chạy dùng các MCP server đang bật, trừ khi `platform_toolsets.cron`
  có `no_mcp`.
- Tạm dừng, tiếp tục, chạy ngay (khi phát hiện scheduler đang chạy) và xóa được
  ủy quyền cho dịch vụ Schedules. Sửa nội dung hướng dẫn thực hiện ở trang Schedules.

## 7. Mở rộng danh mục

Danh mục nằm trong `src/hermes_zalo/integration_catalog.py` (chỉ là dữ liệu):

- `CONNECTORS`: mỗi mục có `id`, `name`, `category` (một trong `CATEGORIES`),
  `initials` (chữ trên logo), `description`, `kind` (`remote` | `local` |
  `bridge` | `custom`), `server_name`, `url` (chỉ với `remote`), `api`,
  `capabilities` (`read`/`write`/`public`/`messages`/`unknown`), `credentials`
  (`writable=False` cho biến chỉ đặt trên máy chủ), `docs` (chỉ https), `snippet`
  (với `local`) và `note`.
- `REPORTS`: `id`, `name`, `short` (dùng trong tên job), `description`,
  `connectors`, `requires` (`any` hoặc `optional`), `schedule`, `period`,
  `focus`, `sections` (tiêu đề tiếng Anh/tiếng Việt và hướng dẫn).

Nguyên tắc trung thực: chỉ thêm liên kết và tên gói mà bạn chắc chắn tồn tại;
nền tảng chưa có MCP server chính thức thì để `kind: bridge`. Biến khóa mới phải
qua được kiểm tra tên an toàn (module sẽ báo lỗi khi import nếu không). Sau khi
sửa, chạy:

```bash
PYTHONPATH=src:. .runtime/venv/bin/python -m unittest tests.test_admin_integrations
cd deploy/vercel-admin && node --test tests/console-integrations.test.js
```

## 8. Chế độ demo

Với `admin_cli demo`, hai trang dùng dữ liệu tổng hợp trong bộ nhớ (GitHub, một
bridge Facebook và Zalo OA mẫu, hai báo cáo mẫu). Không có gì được ghi ra đĩa và
nút Test trả về kết quả giả lập, không truy cập mạng.
