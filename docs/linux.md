# Chạy trên Linux

`scripts/run.sh` tương đương `scripts/run.ps1` (cùng tên lệnh, thêm `Status`).

## Hai thứ cần phân biệt

| | Mặc định | Ghi chú |
|---|---|---|
| **Mã Hermes** (`HERMES_SRC`) | `~/.hermes/hermes-agent` — bản Hermes đã cài trên máy | Chạy qua launcher `.hermes/bin/hermes --run-module`, giống `run.ps1` |
| **Dữ liệu Hermes** (`HERMES_HOME`) | `.runtime/hermes-home` | Luôn tách khỏi `~/.hermes`: test và bot không đụng gateway/config/memory thật |

Các thư mục trong `.runtime/` (đã gitignore):

- `.runtime/venv`: venv của dự án (`python3 -m venv`)
- `.runtime/hermes-home`: `HERMES_HOME` của bot. Plugin Zalo nằm ở `plugins/zalo-platform`; runtime Python do Hermes tự dựng nằm ở `installs/`
- `.runtime/logs`: log test, log gateway và `last-tests.txt` (tóm tắt lần test gần nhất)

## Cài lần đầu

```bash
scripts/run.sh Setup        # venv dự án, chép plugin vào .runtime/hermes-home, tạo .env từ .env.example
                            # (SetupSheets: thêm google-auth và cryptography)
```

Lần đầu chạy `TestHermes`, Hermes sẽ tự dựng runtime trong `.runtime/hermes-home/installs` ("Preparing the isolated Hermes runtime…"). Bước này không sửa `~/.hermes`.

## Theo dõi

```bash
scripts/run.sh Status               # đang dùng Hermes nào, plugin đã cài chưa, gateway nào đang chạy, kết quả test gần nhất
tail -f .runtime/logs/gateway.log   # log của bot khi chạy Start
less .runtime/logs/TestHermes.log   # log đầy đủ của lần test gần nhất
```

## Lệnh thường dùng

```bash
scripts/run.sh Test          # unit test của dự án
scripts/run.sh TestHermes    # smoke test với Hermes thật (không gọi mạng hay model)
scripts/run.sh Check         # kiểm tra .env
scripts/run.sh ZaloCheck     # kiểm tra token Zalo
scripts/run.sh Discover
scripts/run.sh Model         # chọn provider/model cho .runtime/hermes-home, nhập API key
scripts/run.sh NluCheck      # cần chạy Model trước; NluCheckLive gửi một câu hỏi mẫu tới model
scripts/run.sh Start         # hermes gateway run (foreground, đồng thời ghi .runtime/logs/gateway.log)
scripts/run.sh Update        # chép lại plugin sau khi sửa code, rồi khởi động lại Start
```

## Thử với bản Hermes ghim giống cloud

Dockerfile ghim Hermes ở commit `93c9360a…`. Để kiểm tra đúng phiên bản đó:

```bash
scripts/install-hermes.sh                              # clone --shared vào .runtime/hermes-agent + venv Python 3.14 (uv)
HERMES_SRC=.runtime/hermes-agent scripts/run.sh TestHermes
```

`install-hermes.sh` không chạy installer chính thức của Hermes (installer đó ghi vào `~/.local/bin` và sửa `~/.bashrc`). Bản ghim không bắt buộc; xóa `.runtime/hermes-agent` là quay về dùng Hermes trên máy.

## Khác biệt so với Windows

- Venv dự án nằm ở `.runtime/venv`, không phải `.venv`.
- `scripts/launch.py` và `scripts/nlu_check.py` đọc `HERMES_SRC`; trên Linux launcher là `bin/hermes` thay cho `hermes.exe`.
