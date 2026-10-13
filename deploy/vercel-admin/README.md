# LabCoach Admin trên Vercel

Vercel phục vụ giao diện và chuyển các yêu cầu Admin được cho phép tới Railway. Bot Zalo, API, tài khoản quản trị và dữ liệu vẫn ở Railway. Trình duyệt chỉ gọi `/api/...` trên cùng tên miền Vercel.

## 1. Cấu hình project Vercel

Import **repo hiện tại**, sau đó đặt:

| Mục | Giá trị |
| --- | --- |
| Root Directory | `deploy/vercel-admin` |
| Include source files outside of the Root Directory in the Build Step | **Bật** |
| Framework Preset | **Other** |
| Node.js Version | **22.x** |
| Build Command | `npm run build` |
| Output Directory | `public` |

Build lấy đúng ba file giao diện từ `src/hermes_zalo/admin_static`; không tạo bản giao diện riêng để bảo trì. Chỉ các file HTML/CSS/JS được đưa vào `public`. `vercel.json` đã cấu hình API gateway, chống cache và `NODEJS_HELPERS=0` để giữ nguyên dữ liệu JSON trước khi backend kiểm tra.

## 2. Thêm ba biến môi trường phía server

Trong **Settings → Environment Variables**, đặt cho môi trường **Production**:

| Biến | Nội dung |
| --- | --- |
| `LABCOACH_API_URL` | Địa chỉ HTTPS gốc của API Railway, ví dụ `https://your-service.up.railway.app` |
| `LABCOACH_FRONTEND_URL` | Địa chỉ HTTPS chính thức của trang Admin, ví dụ `https://your-admin.vercel.app` |
| `LABCOACH_PROXY_SECRET` | Khóa ngẫu nhiên riêng, từ 32–256 ký tự ASCII in được, không có khoảng trắng |

Chỉ nhập địa chỉ gốc, không thêm đường dẫn, query hoặc thông tin đăng nhập. Dùng khóa gateway riêng; không dùng Bot Token, khóa Google hoặc khóa AI.

**Không thêm tiền tố `NEXT_PUBLIC_` hoặc `VITE_` vào các biến này.** Khóa gateway chỉ được dùng trong hàm server; build không chèn biến môi trường vào giao diện.

Trên Railway, `ZALO_ADMIN_PROXY_SECRET` phải có **cùng giá trị** với `LABCOACH_PROXY_SECRET`. `ZALO_ADMIN_PUBLIC_URL` vẫn là địa chỉ **Railway** khớp với `LABCOACH_API_URL`. API phải chạy qua HTTPS và được bảo vệ bằng khóa gateway trước khi nối Vercel.

Truy cập Admin bằng đúng `LABCOACH_FRONTEND_URL`. API cố ý từ chối tên miền Preview, URL deployment khác và tên miền phụ chưa cấu hình. Thay biến môi trường cần triển khai lại bản tương ứng để có hiệu lực.

## 3. Kiểm tra trên máy

Từ thư mục repo:

```powershell
cd deploy/vercel-admin
npm test
npm run build
```

Kiểm thử dùng dữ liệu mô phỏng, không gọi Zalo, Railway, Sheets hoặc model; không cần khóa thật. Build thành công chỉ xác nhận gói giao diện được tạo, chưa xác nhận luồng đăng nhập Vercel → Railway hoạt động.

Sau khi triển khai và cấu hình đủ, kiểm tra đăng nhập, xem thành viên, đăng xuất rồi đăng nhập lại. Dùng tài khoản Admin do backend tạo; gateway không tạo tài khoản hoặc thay đổi phân quyền.

## 4. Lỗi build thường gặp

**`No python entrypoint found`**, kèm `src/hermes_zalo/tickets/web.py`: Vercel đang build phần Python thay vì trang Admin. Kiểm tra commit được deploy đã có `deploy/vercel-admin/vercel.json` và ba file trong `src/hermes_zalo/admin_static`. Các file mới chỉ có trên máy, chưa commit/push, sẽ chưa có trong bản deploy từ GitHub.

Vào **Settings → Build and Deployment**, đặt **Root Directory** là `deploy/vercel-admin`, **Framework Preset** là **Other**, rồi lưu và deploy lại commit chứa bản cập nhật. Không thêm Python entrypoint để xử lý lỗi này; bot và API đã chạy ở Railway.

**`Frontend source missing`**: bật **Include source files outside of the Root Directory in the Build Step** và kiểm tra commit có đủ ba file giao diện nêu trên.

Tài liệu nền tảng: [Vercel Node.js Functions](https://vercel.com/docs/functions/runtimes/node-js), [truy cập nguồn ngoài Root Directory](https://vercel.com/docs/monorepos/monorepo-faq), [cấu hình Node.js helpers](https://vercel.com/docs/functions/runtimes/node-js/advanced-node-configuration).
