# Trang "System": vận hành máy chủ từ Console

Trang **System** (nhóm *More tools*, chỉ chủ hệ thống thấy) gom các việc vận hành VPS vào một chỗ:

- **Gateway**: đang chạy/dừng/lỗi, PID, thời gian chạy, kênh đang kết nối, nhịp của bộ lập lịch (cron), API server.
- **Server**: CPU, tải 1/5/15 phút, RAM, swap, ổ đĩa chứa Hermes home, uptime; biểu đồ 24 giờ.
- **Health checks**: kiểm tra cấu hình an toàn, kèm cách sửa.
- **Backups**: tạo, liệt kê, xóa bản sao lưu Hermes home. Khôi phục chỉ làm trên máy chủ.
- **Versions & updates**: phiên bản Hermes và console, kiểm tra bản mới. Console **không bao giờ tự cập nhật**.

Khi RAM > 90%, ổ đĩa > 90% hoặc tải 1 phút lớn hơn số CPU, đầu trang hiện cảnh báo màu vàng.

## Trạng thái gateway được xác định thế nào

Giống lệnh `hermes gateway status`:

1. Đọc `<HERMES_HOME>/gateway.pid` (PID + dấu thời điểm khởi động của tiến trình, để không nhầm khi PID bị tái sử dụng).
2. Đọc `<HERMES_HOME>/gateway_state.json` (trạng thái, kênh, phiên bản code). Nếu file này không được cập nhật quá 2 phút, trang ghi rõ là "stale".
3. Nếu đã bật điều khiển gateway (mục dưới), console hỏi thẳng `systemctl show` hoặc `docker inspect` và lấy kết quả đó làm chuẩn.

Nếu systemd báo unit đang chạy nhưng Hermes home này không có gateway sống, trang cảnh báo: rất có thể unit đang chạy với `HERMES_HOME` khác (ví dụ `~/.hermes` thay vì `.runtime/hermes-home`).

Biểu đồ 24 giờ lấy mẫu mỗi phút **trong bộ nhớ của console**: khởi động lại console là mất lịch sử. Đây không phải hệ thống giám sát; nếu cần cảnh báo qua đêm, hãy dùng công cụ giám sát riêng.

## Bật điều khiển gateway (tùy chọn)

Mặc định console **chỉ xem**, không bật/tắt tiến trình. Để có nút Start / Stop / Restart, đặt **một** biến môi trường cho tiến trình console rồi khởi động lại console:

```bash
HERMES_CONSOLE_GATEWAY_CONTROL=<kiểu>:<tên>
```

| Giá trị | Lệnh console chạy (không qua shell) |
|---|---|
| `systemd-user:hermes-gateway.service` | `systemctl --user --no-block start\|stop\|restart hermes-gateway.service` |
| `systemd:hermes-gateway.service` | `systemctl --no-block …` (chỉ dùng khi tài khoản console đã có quyền polkit) |
| `systemd-sudo:hermes-gateway.service` | `sudo -n systemctl --no-block …` (cần quy tắc sudoers bên dưới) |
| `docker:hermes-zalo-bot` | `docker start\|stop -t 60\|restart -t 60 hermes-zalo-bot` |

Quy tắc an toàn:

- Tên unit/container được kiểm tra chặt (chữ, số, `.`, `_`, `-`, `@`; unit phải là `.service`). Giá trị sai thì điều khiển **tắt** và trang hiện lý do.
- Trình duyệt chỉ chọn một trong ba nút cố định; không bao giờ gửi lệnh, đường dẫn hay tên dịch vụ lên server.
- **Stop** bắt buộc gõ đúng tên dịch vụ. Mọi thao tác được ghi audit (chỉ tên thao tác, không có giá trị).
- Lệnh có giới hạn thời gian; môi trường con chỉ có `PATH`, `HOME`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS` và biến `DOCKER_*`, không có token.
- Nếu console chạy **bên trong** tiến trình gateway (chế độ Zalo Lab), Stop sẽ đóng luôn trang này; khi đó phải start lại trên máy chủ.

### Ví dụ: systemd user service

```ini
# ~/.config/systemd/user/hermes-gateway.service
[Unit]
Description=Hermes gateway (bot)
After=network-online.target

[Service]
WorkingDirectory=/home/duy/2026.1/hermes-agent
ExecStart=/home/duy/2026.1/hermes-agent/scripts/run.sh Start
Restart=on-failure
TimeoutStopSec=60

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermes-gateway.service
sudo loginctl enable-linger "$USER"     # để service chạy cả khi bạn đã đăng xuất SSH
```

Console phải chạy cùng tài khoản và thấy được user bus (`XDG_RUNTIME_DIR`). Cách đơn giản nhất là chạy console cũng bằng một user service, trong đó có dòng:

```ini
Environment=HERMES_CONSOLE_GATEWAY_CONTROL=systemd-user:hermes-gateway.service
```

### Ví dụ: system service + sudo

Chỉ cho phép đúng ba lệnh, không hơn (`sudo visudo -f /etc/sudoers.d/hermes-console`):

```
duy ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block start hermes-gateway.service, /usr/bin/systemctl --no-block stop hermes-gateway.service, /usr/bin/systemctl --no-block restart hermes-gateway.service
```

Kiểm tra đường dẫn bằng `command -v systemctl`. Sau đó đặt `HERMES_CONSOLE_GATEWAY_CONTROL=systemd-sudo:hermes-gateway.service`.

### Ví dụ: Docker Compose

`compose.yaml` của dự án đặt tên container cố định `hermes-zalo-bot`, nên:

```bash
HERMES_CONSOLE_GATEWAY_CONTROL=docker:hermes-zalo-bot
```

Lưu ý: tài khoản có quyền dùng Docker gần như tương đương root. Chỉ bật khi console chạy trên host, bằng tài khoản vốn đã được phép dùng Docker; đừng mount Docker socket vào container chỉ để có nút này.

### Lệnh tương đương khi không bật

```bash
systemctl --user status|restart|stop|start hermes-gateway.service
docker restart hermes-zalo-bot
scripts/run.sh Start          # chạy foreground; dừng bằng Ctrl+C
scripts/run.sh Status
```

## Health checks

Trang tự chạy các kiểm tra nhẹ mỗi lần tải. Nút **Run checks** chạy đầy đủ (gồm cả ghi thử một file tạm vào Hermes home rồi xóa) và giữ kết quả 15 phút.

| Kiểm tra | Đạt khi | Cách sửa thường gặp |
|---|---|---|
| Hermes configuration | `config.yaml` có và đọc được | Sửa lỗi cú pháp YAML |
| `.env` | Chỉ chủ sở hữu đọc được (0600) | `chmod 600 .env` |
| Hermes home writable | Console ghi được | Sửa chủ sở hữu/quyền thư mục |
| Disk space | Còn ≥ 1 GB và dùng ≤ 90% | Xóa backup/log cũ |
| Assistant core plugin | Đã cài và có trong `plugins.enabled` | `scripts/run.sh Setup` |
| Usage telemetry | Có `assistant-core/telemetry.sqlite3` | Tự tạo sau lượt gọi model đầu tiên |
| Chat toolsets | Không có `computer_use`, `desktop_ui`, `connections`, `setup`, `project` trong `platform_toolsets` | Xóa các toolset đó |
| Command sandbox | `terminal.backend` khác `local` | Đặt `terminal.backend: docker` |
| Channel tokens | Kênh đang bật có token | Thêm `TELEGRAM_BOT_TOKEN` / `ZALO_BOT_TOKEN` vào `.env` |
| Console exposure | Console chỉ nghe `127.0.0.1`, hoặc có `ZALO_ADMIN_PUBLIC_URL` HTTPS | Đặt `ZALO_ADMIN_HOST=127.0.0.1` hoặc dùng reverse proxy HTTPS |
| Gateway running / Scheduler heartbeat | Gateway chạy, cron tick trong 3 phút | Khởi động lại gateway |
| Recent backup | Có backup trong 7 ngày | Bấm **Create backup** |

Kết quả chỉ ghi **tên** biến, không bao giờ hiện giá trị token hay khóa.

## Sao lưu Hermes home

**Create backup** tạo file `hermes-home-YYYYMMDD-HHMMSS.tar.gz` trong `<HERMES_HOME>/backups/console/` (thư mục 0700, file 0600). Việc nén chạy nền; trang tự cập nhật tiến độ. Chỉ một thao tác chạy cùng lúc.

- **Không** đưa vào: `cache/`, `installs/`, `tools/`, `audio_cache/`, `image_cache/`, `backups/`, `state-snapshots/`, `checkpoints/`, `__pycache__`, `.git`, `node_modules`, venv, file khóa/PID/socket, file log lớn hơn 5 MB, và mọi **symlink** (không đi theo symlink ra ngoài Hermes home).
- Cơ sở dữ liệu SQLite (`state.db`, `kanban.db`, …) được chụp nhất quán bằng SQLite backup API, nên có thể sao lưu khi gateway đang chạy.
- **Bí mật** (`.env`, `auth.json`) mặc định **bị loại**. Chỉ khi tick "Include secrets" mới có, và tên file thêm đuôi `-secrets`. Ai có file đó là dùng được token và API key của bạn.
- Giữ lại N bản mới nhất (mặc định 5, tối đa 50); bản cũ hơn tự xóa sau khi bản mới tạo thành công. Chỉ file do console tạo bị đụng tới; backup của chính Hermes (`backups/pre-update-*.zip`, `backups/config/`) giữ nguyên.
- Giới hạn: tối đa 4 GB dữ liệu đầu vào, 200 000 file, 30 phút; dừng nếu ổ đĩa còn dưới 512 MB. Bị dừng thì không để lại file dở.
- Mỗi archive có `MANIFEST.json` ở gốc: thời điểm, số file, các mục bị bỏ qua, có bí mật hay không, lệnh khôi phục.

Nên chép backup sang máy khác định kỳ, ví dụ:

```bash
rsync -a --chmod=F600 vps:/path/.runtime/hermes-home/backups/console/ ~/hermes-backups/
```

### Khôi phục (chỉ trên máy chủ)

Console cố ý không khôi phục qua web. Trên máy chủ:

```bash
# 1. Dừng gateway
systemctl --user stop hermes-gateway.service        # hoặc: docker stop hermes-zalo-bot

# 2. (Nên làm) xem trước nội dung và giữ một bản của trạng thái hiện tại
tar -tzf <HERMES_HOME>/backups/console/hermes-home-20261031-020000.tar.gz | less
cp -a <HERMES_HOME> <HERMES_HOME>.before-restore

# 3. Giải nén đè lên Hermes home
tar -xzpf <HERMES_HOME>/backups/console/hermes-home-20261031-020000.tar.gz \
    -C <HERMES_HOME> --strip-components=1 hermes-home

# 4. Nếu backup không có bí mật: kiểm tra .env / auth.json vẫn còn, rồi chạy lại gateway
systemctl --user start hermes-gateway.service
```

Trang Backups hiện sẵn đúng lệnh bước 1 và 3 cho bản mới nhất, kèm nút Copy. Các thư mục bị loại (cache, installs, …) Hermes tự dựng lại khi chạy.

## Kiểm tra cập nhật

Trang hiện phiên bản Hermes đang chạy (từ `gateway_state.json`), commit đã cài (`HERMES_SRC`) và commit của repo console. Nếu commit đang chạy khác commit đã cài, trang nhắc khởi động lại gateway.

**Check for updates** chỉ chạy khi mã nguồn là git checkout: `git rev-parse @{upstream}` và `git ls-remote` (giới hạn 15 giây, không hỏi mật khẩu, không fetch, không pull). Kết quả là "Up to date" hoặc "Update available". Cập nhật luôn do bạn chạy trên máy chủ, ví dụ:

```bash
HERMES_HOME=<HERMES_HOME> $HERMES_SRC/.hermes/bin/hermes update     # bản cài chính thức
git -C /path/hermes-agent pull --ff-only && scripts/run.sh Update   # repo console + plugin
systemctl --user restart hermes-gateway.service
```

Với Docker: đổi `HERMES_COMMIT` trong `Dockerfile` nếu cần, rồi `docker compose build --pull && docker compose up -d`.

## Biến môi trường liên quan

| Biến | Ý nghĩa |
|---|---|
| `HERMES_CONSOLE_GATEWAY_CONTROL` | Bật nút điều khiển gateway (xem trên). Bỏ trống hoặc `off` = tắt |
| `HERMES_SRC` | Thư mục mã Hermes để đọc commit và kiểm tra cập nhật |
| `HERMES_CONSOLE_CHAT_API_URL` hoặc `API_SERVER_ENABLED` / `API_SERVER_HOST` / `API_SERVER_PORT` | API server được kiểm tra `GET /health` (chỉ khi địa chỉ là loopback, timeout 2 giây, không gửi khóa) |
| `ZALO_ADMIN_HOST`, `ZALO_ADMIN_PUBLIC_URL` | Dùng cho kiểm tra "Console exposure" |

## Chế độ demo

`scripts/run.sh AdminDemo` hiển thị trang System với dữ liệu giả; các nút chỉ mô phỏng trong bộ nhớ, không chạy lệnh, không ghi đĩa, không gọi mạng.
