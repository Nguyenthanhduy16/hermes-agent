# Contract tích hợp giai đoạn 1

Đối chiếu tài liệu chính thức ngày **05/10/2026**. Những dữ liệu chưa có xác nhận thực tế được nêu riêng; fixture và MockTransport chỉ kiểm tra code theo contract đã đọc.

## Zalo Bot API

Địa chỉ API:

```text
https://bot-api.zaloplatforms.com/bot<BOT_TOKEN>/<method>
```

Client dùng POST JSON UTF-8, không theo redirect. Token nằm trong URL nên lỗi/log HTTP không được xuất URL thô. Các ID giữ nguyên dạng chuỗi. Envelope có `ok`, `result`; khi lỗi có `description` và `error_code`. Adapter không hiển thị `description` từ server vì nội dung có thể chứa dữ liệu nhạy cảm. [Nguồn: sử dụng API](https://docs.zaloplatforms.com/docs/BOT/call_api)

| Method | Body dự án gửi | Kết quả cần dùng | Nguồn |
| --- | --- | --- | --- |
| `getMe` | `{}` | `id`, `account_name`, `account_type`, `can_join_groups` | [getMe](https://docs.zaloplatforms.com/docs/BOT/apis/getMe) |
| `getWebhookInfo` | `{}` | `url`, `updated_at` | [getWebhookInfo](https://docs.zaloplatforms.com/docs/BOT/apis/getWebhookInfo) |
| `getUpdates` | `{"timeout":30}` | Một event trong `result` | [getUpdates](https://docs.zaloplatforms.com/docs/BOT/apis/getUpdates) |
| `sendMessage` | `{"chat_id":"...","text":"..."}` | `message_id`, `date` | [sendMessage](https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage) |

Kiểm tra `getMe` thật ngày 06/10/2026 thấy thêm `display_name`, khác với `account_name` dạng handle nội bộ. Trường này chưa xuất hiện trong response mẫu trên tài liệu tại thời điểm đối chiếu. Adapter nhận cả hai tên để tách mention ở đầu tin nhóm; nếu thiếu `display_name` vẫn dùng được `account_name` hoặc lệnh gửi bằng Trả lời tin bot. Không suy đoán tên từ người gửi hoặc bỏ một mention bất kỳ.

`getUpdates` chỉ tài liệu hóa tham số `timeout`; không có `offset`, `update_id`, danh sách batch hay cursor xác nhận theo kiểu Telegram. Bảng tham số ghi String nhưng code mẫu truyền số `30`; adapter theo code mẫu. HTTP read timeout đặt dài hơn thời gian polling.

Polling và webhook loại trừ nhau. Startup đọc `getWebhookInfo`, dừng nếu có webhook. Dự án không tự gỡ webhook. Zalo khuyến nghị polling cho phát triển và webhook cho vận hành thường xuyên. [Nguồn: getUpdates](https://docs.zaloplatforms.com/docs/BOT/apis/getUpdates)

Quan sát thực tế trên bot thử: `getMe` thành công nhưng `getWebhookInfo` trả HTTP 200, `ok:false`, `error_code:404`, `description:Not Found`. Tài liệu chưa giải thích trường hợp này. Dự án phân loại riêng là trạng thái webhook chưa xác minh và cho phép thử polling; không coi 404 là URL rỗng. Phản hồi có URL vẫn chặn polling, lỗi khác vẫn được báo và lỗi từ `getUpdates` không được bỏ qua. Chưa gửi hoặc nhận tin thật trong lần xác minh trạng thái này.

Nếu người vận hành chủ động chuyển từ webhook sang polling, Zalo có phương thức `deleteWebhook` với body `{}`. Phần mô tả nói trả True, nhưng mẫu response trả `result` có `url:""` và `updated_at`; dự án không phụ thuộc giả định return boolean. [Nguồn: deleteWebhook](https://docs.zaloplatforms.com/docs/BOT/apis/deleteWebhook)

## Event nhận được

Ví dụ minh họa theo cấu trúc được tài liệu hóa, dùng ID giả:

```json
{
  "ok": true,
  "result": {
    "event_name": "message.text.received",
    "message": {
      "from": {"id": "user.demo", "display_name": "Demo", "is_bot": false},
      "chat": {"id": "chat.demo", "chat_type": "PRIVATE"},
      "text": "Xin chào",
      "message_id": "message.demo",
      "date": 1750316131602
    }
  }
}
```

`chat_type` nhận `PRIVATE` hoặc `GROUP`. Định tuyến phản hồi bằng `message.chat.id`, cấp quyền người gửi bằng `message.from.id`. Các event ảnh, sticker, thoại và unsupported được bỏ qua ở giai đoạn 1. Tài liệu sample `date` dùng milliseconds; adapter không dựa vào timestamp để khử trùng. [Nguồn: Webhook/event schema](https://docs.zaloplatforms.com/docs/BOT/webhook)

Nhóm còn Beta: trang gói Basic quảng bá 3 nhóm, trong khi hướng dẫn nhóm ghi đang thử nghiệm nội bộ. Chỉ có thể xác minh quyền bot cụ thể bằng API/UI và thử thật. Quy trình mời nhóm xem README; bot nhận mention hoặc quote/reply tin bot, không phải mọi tin nhóm. [Gói Basic](https://bot.zapps.me/), [hướng dẫn nhóm](https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group)

## Gửi văn bản và xử lý lỗi

`sendMessage.text` giới hạn 1–2000 ký tự. Dự án gửi plain text, chia theo tối đa 2000 UTF-16 code units để giữ emoji nguyên vẹn. Đây là cách đếm bảo thủ của dự án: tài liệu giới hạn text chỉ nói “ký tự”; UTF-16 được nêu rõ cho offset của rich text. Không dùng `parse_mode` hoặc `text_styles` trong giai đoạn 1. [Nguồn: sendMessage](https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage)

| Mã Zalo công bố | Diễn giải trong tài liệu |
| --- | --- |
| `400` | Sai request hoặc API |
| `401` | Token không hợp lệ |
| `403` | Internal server error |
| `404` | Không tìm thấy |
| `408` | Timeout |
| `429` | Vượt hạn mức |

Đặc biệt, bảng Zalo gọi `403` là lỗi nội bộ, không mặc định diễn giải như quyền truy cập HTTP thông thường. [Nguồn: bảng mã lỗi](https://docs.zaloplatforms.com/docs/BOT/error_code)

**Chính sách do dự án chọn:**

- Read/poll retry có giới hạn với lỗi mạng, `403`, `408`, `429` và `5xx`, dùng thời gian chờ tăng dần và độ lệch ngẫu nhiên. Riêng `408` khi polling được xem là lượt chờ không có kết quả, sau đó vòng nhận tin tiếp tục.
- `401` dừng để sửa token. HTTP status lỗi được ưu tiên hơn body JSON.
- Với gửi tin, chỉ tự retry phản hồi từ chối rõ ràng `429`. Timeout, lỗi mạng, `403`, `408`, `5xx` hoặc response thành công nhưng không đọc được có thể là kết quả gửi chưa rõ; không gửi lại tự động.
- Nếu có HTTP `Retry-After`, client đọc giá trị và giới hạn thời gian chờ tối đa 60 giây. Đây là biện pháp xử lý header chuẩn, không phải tuyên bố Zalo cam kết cung cấp header này.
- Không đưa token, nội dung response hoặc nội dung tin vào exception có thể hiển thị.

Tài liệu đã đọc chưa quy định payload chính xác khi không có tin, lịch retry, thời gian lưu backlog, SLA giao event hoặc khóa idempotency cho `sendMessage`. Mã `408` idle và việc chấp nhận `result:null` khi polling là xử lý tương thích của adapter, cần đối chiếu thêm khi thử thật. Không khẳng định nhận/gửi chính xác một lần.

## Hermes và quyền trong dự án

Hermes được cài bằng trình cài native Windows chính thức, với `-NonInteractive -SkipBrowser -SkipComputerUse`. Script của repo cố định mã upstream tại commit `93c9360a8da592cee43e8895403e91c7a920b0ce`. `-HermesHome` và `-InstallDir` tách dữ liệu khỏi mã cài; `Model` dùng lệnh `hermes model`. [Nguồn: cài Hermes](https://hermes-agent.nousresearch.com/docs/getting-started/installation), [mã upstream cố định](https://github.com/NousResearch/hermes-agent/tree/93c9360a8da592cee43e8895403e91c7a920b0ce)

Adapter được nạp dưới dạng plugin nền tảng vào gateway Hermes; không fork toàn bộ Hermes vào repo ứng dụng. Hermes tài liệu hóa plugin `plugin.yaml` + `adapter.py`, kế thừa `BasePlatformAdapter`, triển khai kết nối/ngắt kết nối/gửi tin và chuyển event nhận vào `handle_message`. [Nguồn: Adding a Platform Adapter](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters)

Plugin và cấu hình chạy đặt trong `.runtime/hermes-home/`, mã upstream ở `.runtime/hermes-agent/`. Giai đoạn này chỉ cho hội thoại và vô hiệu hóa lệnh điều khiển qua Zalo.

Quy tắc ứng dụng:

- Tin riêng cần người gửi thuộc allowlist.
- Tin nhóm cần cả nhóm lẫn người gửi thuộc allowlist.
- Phiên model tách theo `(chat_type, chat_id, user_id)`. Câu trả lời nhóm vẫn công khai với thành viên nhóm.
- Khử trùng theo `(chat_type, chat_id, message_id)` và ghi dấu bền vững trước khi gọi Hermes.
- Ghi dấu trước ưu tiên không tự xử lý lặp. Tiến trình chết sau ghi dấu có thể để tin chưa hoàn thành; người dùng gửi yêu cầu mới khi cần. Đây là giới hạn có chủ đích, không phải bảo đảm end-to-end exactly-once.

## Những việc vẫn cần thử thật

Token hợp lệ, event nhắn riêng, response timeout polling thực tế, quyền nhóm, mention/quote, phạm vi ID, model và API key, giới hạn quota, hành vi mất mạng, gửi câu trả lời dài. Không có kết quả thật nào được suy ra từ fixture hoặc kiểm thử offline.
