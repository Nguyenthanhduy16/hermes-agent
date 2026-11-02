# Đưa Hermes Agent Console vào vận hành thật

Tài liệu này dành cho người vận hành muốn chạy **Agent Console** (trang quản lý trợ lý) ở chế độ thật, không phải demo. Giao diện luôn bằng tiếng Anh; tài liệu bằng tiếng Việt.

Có hai cách chạy console:

| Cách | Khi nào dùng | Lệnh | Tài khoản lưu ở |
| --- | --- | --- | --- |
| **Console độc lập** | Quản lý trợ lý/Hermes, xem báo cáo, kết nối… Không cần token Zalo | `admin_cli start` / `serve` | `<HERMES_HOME>/console-admin.sqlite3` |
| **Console trong bot Zalo** | Bot Zalo đang chạy (máy chủ, Docker, Railway) và cần quản lý thành viên/hội thoại | gateway với `ZALO_ADMIN_ENABLED=true` | `<thư mục state>/…admin.sqlite3` (tạo bằng `create-account`) |

Cả hai dùng chung một máy chủ HTTP (`src/hermes_zalo/admin_web.py`) và cùng các lớp bảo vệ bên dưới.

## 1. Yêu cầu

- Linux (hoặc macOS) có Python **3.11+**; Docker nếu chạy bằng container.
- Mã nguồn dự án và venv: `scripts/run.sh Setup` (tạo `.runtime/venv`, cài gói `hermes_zalo`).
- Muốn truy cập từ máy khác: một tên miền và **reverse proxy có HTTPS** (Caddy hoặc nginx). Console không tự làm TLS và **không bao giờ** được mở thẳng ra Internet bằng HTTP.
- Dung lượng: vài chục MB cho cơ sở dữ liệu; sao lưu định kỳ (mục 7).

## 2. Chạy lần đầu — một lệnh

```bash
scripts/run.sh Setup        # chỉ lần đầu
scripts/run.sh Console      # lần đầu hỏi tài khoản chủ sở hữu, sau đó mở console
```

Lần đầu, lệnh hỏi **tên đăng nhập** (mặc định `owner`) và **mật khẩu** (ẩn, ít nhất 12 ký tự, nhập 2 lần), tạo tài khoản chủ sở hữu (role `faculty`, đủ quyền), rồi mở <http://127.0.0.1:8787/>. Những lần sau lệnh chỉ khởi động console.

Lệnh tương đương không qua script (dữ liệu mặc định ở `.runtime/hermes-home`):

```bash
PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli start --home /duong/dan/hermes-home --port 8787
```

Tham số thêm được chuyển tiếp: `scripts/run.sh Console --port 8790`.

**Các chốt an toàn khi khởi động thật:**

- `start`/`serve` **từ chối chạy** nếu chưa có tài khoản nào, hoặc còn tài khoản dùng mật khẩu demo công khai (`LabCoach-Demo-2026`). Thông báo nêu tên tài khoản cần đổi; đổi bằng
  `scripts/run.sh ConsoleAccount --username TEN --replace`.
- Ngoài chế độ demo, mật khẩu demo **không thể** được đặt (CLI, trang Accounts, đổi mật khẩu) và **không thể đăng nhập**, kể cả khi ai đó sao chép kho tài khoản của bản demo sang. Điều này áp dụng cho cả console trong bot Zalo.
- Lần đầu chạy không có terminal tương tác (systemd, Docker) sẽ báo lỗi thay vì tự tạo tài khoản: tạo tài khoản trước bằng `create-console-account`.

Thêm tài khoản khác: `scripts/run.sh ConsoleAccount --username tenmoi --role admin --permissions conversations`.

## 3. Biến môi trường

Đặt trong `.env` ở thư mục chạy lệnh (CLI tự đọc), trong `EnvironmentFile` của systemd, hoặc trong `.env.docker`/Railway Variables.

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `HERMES_HOME` | `.runtime/hermes-home` | Thư mục dữ liệu của bot/console. `--home` ghi đè. Không trỏ vào `~/.hermes` cá nhân |
| `ZALO_ADMIN_HOST` | `127.0.0.1` | Chỉ `127.0.0.1` hoặc `0.0.0.0`. `0.0.0.0` **bắt buộc** có `ZALO_ADMIN_PUBLIC_URL` |
| `ZALO_ADMIN_PORT` (hoặc `PORT`) | `8787` | Cổng HTTP nội bộ |
| `ZALO_ADMIN_PUBLIC_URL` | trống | Origin HTTPS công khai, ví dụ `https://console.example.com` (không có đường dẫn). Khi đặt: chỉ chấp nhận đúng Host/Origin này, cookie có `Secure`, bật HSTS |
| `ZALO_ADMIN_PROXY_SECRET` | trống | Chỉ dùng với Vercel gateway (`deploy/vercel-admin`). Khi đặt, mọi request (trừ `/healthz`) phải có khóa này |
| `ZALO_ADMIN_ENABLED`, `ZALO_CONVERSATIONS_ENABLED` | `false` | Bật console **bên trong bot Zalo** (cần cả hai). Console độc lập không cần |
| `ZALO_CONVERSATION_RETENTION_DAYS` | `30` | Số ngày giữ hội thoại (1–365) |
| `BOT_START_ENABLED` | `false` (Docker) | Docker/Railway: `false` = chế độ chờ, không chạy bot/console |
| `HERMES_CONSOLE_*` | trống | Kết nối tùy chọn (chat, workspace). Xem `.env.example` và [admin.md](admin.md) |

Khóa bí mật (API key, token) chỉ đặt ở biến môi trường/file trên máy chủ; console không bao giờ trả về giá trị bí mật.

## 4. HTTPS với reverse proxy

Khuyến nghị: console nghe **127.0.0.1**, proxy trên cùng máy nhận HTTPS và chuyển vào. Trong `.env`:

```bash
ZALO_ADMIN_HOST=127.0.0.1
ZALO_ADMIN_PORT=8787
ZALO_ADMIN_PUBLIC_URL=https://console.example.com
```

**Caddy** (tự xin chứng chỉ Let's Encrypt), `/etc/caddy/Caddyfile`:

```caddy
console.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8787
}
```

**nginx** — phải giữ nguyên `Host` gốc, nếu không console trả `This request host is not allowed.`:

```nginx
server {
    listen 443 ssl http2;
    server_name console.example.com;
    ssl_certificate     /etc/letsencrypt/live/console.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/console.example.com/privkey.pem;

    client_max_body_size 64k;              # console nhận tối đa 32 KB mỗi request
    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;       # bắt buộc
        proxy_read_timeout 60s;
    }
}
server { listen 80; server_name console.example.com; return 301 https://$host$request_uri; }
```

Ghi chú:

- Console **không tin** `X-Forwarded-*`; Host/Origin phải đúng `ZALO_ADMIN_PUBLIC_URL`. Không cần thêm header CORS.
- Không đặt cache ở proxy cho `/api/`: console đã gửi `Cache-Control: no-store`.
- Mở link console từ e-mail/Zalo (điều hướng cross-site) hiển thị trang bình thường; cookie phiên `SameSite=Strict` nên có thể phải đăng nhập lại ở tab mới — đây là chủ ý.

## 5. Chạy nền bằng systemd (console độc lập)

Tạo tài khoản trước (mục 2), rồi tạo `/etc/systemd/system/hermes-console.service`:

```ini
[Unit]
Description=Hermes Agent Console
After=network-online.target
Wants=network-online.target

[Service]
User=hermes
WorkingDirectory=/opt/hermes-agent
Environment=PYTHONPATH=/opt/hermes-agent/src
Environment=HERMES_HOME=/var/lib/hermes/hermes-home
ExecStart=/opt/hermes-agent/.runtime/venv/bin/python -m hermes_zalo.admin_cli serve --home /var/lib/hermes/hermes-home
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/hermes

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-console
journalctl -u hermes-console -f           # xem log
curl -fsS http://127.0.0.1:8787/healthz   # {"status": "ok"}
```

`.env` trong `WorkingDirectory` được đọc tự động; có thể thay bằng `EnvironmentFile=/etc/hermes/console.env` (quyền `600`).

## 6. Docker / Compose / Railway (console trong bot Zalo)

Image chạy bot Zalo; console bật khi đặt trong `.env.docker`:

```bash
BOT_START_ENABLED=true
ZALO_ADMIN_ENABLED=true
ZALO_CONVERSATIONS_ENABLED=true
ZALO_ADMIN_HOST=0.0.0.0
ZALO_ADMIN_PORT=8787
ZALO_ADMIN_PUBLIC_URL=https://console.example.com
```

Trong `compose.yaml`, bỏ chú thích `ports` để chỉ mở cổng cho proxy trên cùng máy (`127.0.0.1:8787:8787`), rồi cấu hình Caddy/nginx như mục 4. Tạo tài khoản trong container (mật khẩu nhập ẩn):

```bash
docker compose exec bot python -m hermes_zalo.admin_cli create-account --username owner --role faculty \
  --permissions members,issues,conversations,memories,tickets
```

**Health check:** image có `HEALTHCHECK` gọi `python -m hermes_zalo.admin_cli healthcheck`. Lệnh này luôn đạt khi bot ở chế độ chờ hoặc console tắt; khi console bật, nó gọi `GET http://127.0.0.1:<cổng>/healthz`. Xem trạng thái: `docker inspect --format '{{.State.Health.Status}}' hermes-zalo-bot`.

**Railway:** làm theo [deploy-railway.md](deploy-railway.md). Chỉ thêm `healthcheckPath = "/healthz"` vào `railway.toml` khi console đã bật (`ZALO_ADMIN_ENABLED=true`, `BOT_START_ENABLED=true`); nếu không, deploy ở chế độ chờ sẽ thất bại vì không có cổng nào nghe. Giao diện trên Vercel: xem [admin.md](admin.md) và `deploy/vercel-admin`.

### Endpoint `/healthz`

- `GET`/`HEAD /healthz`, không cần đăng nhập, bỏ qua kiểm tra Host và khóa proxy (để probe của Docker/Railway/systemd dùng được).
- Trả `200 {"status": "ok"}` khi luồng HTTP, vòng lặp sự kiện của bot và kho tài khoản đều phản hồi; ngược lại `503 {"status": "unavailable"}`. Không bao giờ chứa dữ liệu, phiên bản hay cấu hình.

## 7. Sao lưu và khôi phục

Dữ liệu cần giữ là các tệp SQLite:

- Console độc lập: toàn bộ `HERMES_HOME` (trong đó `console-admin.sqlite3` là tài khoản; các tệp khác là dữ liệu trợ lý, báo cáo, kết nối).
- Bot Zalo/Docker: volume `/data` (`bot-data`). Railway có sao lưu tự động mã hóa: [backup-google-drive.md](backup-google-drive.md).

Sao lưu an toàn khi đang chạy (dùng API backup của SQLite, không copy thô tệp đang ghi):

```bash
BACKUP=/var/backups/hermes/$(date +%F); mkdir -p "$BACKUP"
find /var/lib/hermes/hermes-home -name '*.sqlite3' -o -name '*.db' | while read -r db; do
  out="$BACKUP/${db#/var/lib/hermes/hermes-home/}"; mkdir -p "$(dirname "$out")"
  python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' "$db" "$out"
done
```

Chạy hằng ngày bằng cron/systemd timer, giữ ít nhất 7 bản, lưu bản sao ở máy khác. Bản sao lưu chứa hash mật khẩu và hội thoại: đặt quyền `700`, không chia sẻ công khai.

Khôi phục: dừng dịch vụ, chép các tệp về đúng chỗ (giữ chủ sở hữu/quyền), xóa các tệp `*-wal`/`*-shm` cũ đi kèm, khởi động lại, kiểm tra `/healthz` và đăng nhập.

## 8. Nâng cấp

```bash
sudo systemctl stop hermes-console        # hoặc: docker compose down
# sao lưu (mục 7)
git pull
scripts/run.sh Setup                      # cập nhật venv/gói
PYTHONPATH=src .runtime/venv/bin/python -m unittest discover -s tests -q   # tùy chọn
sudo systemctl start hermes-console       # hoặc: docker compose up -d --build
curl -fsS http://127.0.0.1:8787/healthz
```

JS/CSS của console được gửi với `ETag` và `Cache-Control: no-cache`, nên trình duyệt tự lấy bản mới sau nâng cấp (không cần xóa cache). Lược đồ cơ sở dữ liệu được nâng cấp tự động khi khởi động; giữ bản sao lưu đến khi chắc chắn bản mới chạy ổn. Quay lại bản cũ: `git checkout <commit cũ>`, khôi phục bản sao lưu nếu bản mới đã ghi dữ liệu.

## 9. Bảo mật đã có sẵn

- Cookie phiên `HttpOnly; SameSite=Strict` (+ `Secure` khi có `ZALO_ADMIN_PUBLIC_URL`), hết hạn sau 8 giờ; mọi thao tác ghi cần CSRF token và Origin đúng.
- `/api/*` và mọi `POST` từ chối Origin lạ và `Sec-Fetch-Site: cross-site`; chỉ điều hướng trang cấp cao nhất (mở link) được phép từ site khác.
- Header: CSP nghiêm (`script-src 'self'`, không inline), `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` tắt camera/micro/vị trí/thanh toán/USB, `Cross-Origin-Opener-Policy`/`Cross-Origin-Resource-Policy: same-origin`, `X-Robots-Tag: noindex`, HSTS khi chạy qua HTTPS.
- Tối đa 16 request đồng thời, body tối đa 32 KB; đăng nhập sai 5 lần/10 phút bị khóa tạm.
- HTML không được lưu cache (`no-store`), nên nút Back sau khi đăng xuất không hiện lại dữ liệu; JS/CSS được nén gzip khi trình duyệt hỗ trợ.

## 10. Xử lý sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
| --- | --- |
| `No console owner exists…` | Chạy `scripts/run.sh Console` trong terminal tương tác một lần, hoặc `ConsoleAccount --username owner` |
| `Refusing to start: … public demo password` | Đổi mật khẩu: `scripts/run.sh ConsoleAccount --username TEN --replace` |
| Đăng nhập báo `…public demo password and is blocked…` | Tài khoản sao chép từ demo; đặt lại mật khẩu như trên |
| `This request host is not allowed.` | Proxy không giữ `Host` (nginx thiếu `proxy_set_header Host $host`), hoặc truy cập bằng địa chỉ khác `ZALO_ADMIN_PUBLIC_URL` |
| `This request origin is not allowed.` khi bấm nút | Trang mở qua địa chỉ khác origin cấu hình (IP, http, cổng khác). Mở đúng `ZALO_ADMIN_PUBLIC_URL` |
| `Listening beyond localhost requires ZALO_ADMIN_PUBLIC_URL…` | Đặt `ZALO_ADMIN_HOST=127.0.0.1` khi dùng proxy cùng máy, hoặc khai báo URL HTTPS |
| `Address already in use` | Cổng đang bận: đổi `--port`/`ZALO_ADMIN_PORT`, hoặc dừng tiến trình cũ (`ss -ltnp | grep 8787`) |
| `/healthz` trả 503 | Vòng lặp bot bị treo hoặc kho tài khoản lỗi/đầy đĩa: xem log, `df -h`, khởi động lại dịch vụ |
| Docker báo `unhealthy` | Bot chưa kết nối Zalo nên console chưa mở; xem `docker compose logs bot` |
| Đăng nhập liên tục bị đẩy ra | Đồng hồ máy chủ sai, hoặc dùng HTTP thuần với `ZALO_ADMIN_PUBLIC_URL` (cookie `Secure` không được gửi) |

Không dán mật khẩu, token hay nội dung `.env` vào chat/issue khi nhờ hỗ trợ; log của console không ghi body request.
