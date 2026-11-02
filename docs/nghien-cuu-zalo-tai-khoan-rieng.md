# Nghiên cứu: có nên cho bot một số điện thoại / tài khoản Zalo cá nhân riêng?

*Cập nhật 31/10/2026. Phạm vi: chỉ nghiên cứu. Không chạy tài khoản Zalo thật nào, chưa triển khai code. Đánh dấu nguồn:*

- ✅ *đã đọc trực tiếp trang chính thức;*
- ⚠️ *chỉ có nguồn thứ cấp, hoặc trang chính thức render bằng JavaScript nên công cụ không đọc được nội dung, chỉ đọc được tiêu đề.*

## Tóm tắt

Chạy bot bằng tài khoản Zalo cá nhân qua thư viện không chính thức `zca-js` **gỡ được gần hết** giới hạn của Zalo Bot Platform:

- nhận file;
- thấy tin nhóm không @bot;
- gửi ảnh và file từ máy;
- gửi voice vào nhóm;
- reaction, thu hồi tin.

Đổi lại có ba cái giá:

- bị coi là vi phạm chính sách Zalo, nên tài khoản có thể bị khóa bất cứ lúc nào;
- phiên đăng nhập là cookie của một người thật;
- bot đọc tin nhóm của người không đồng ý, đụng tới Luật Bảo vệ dữ liệu cá nhân 2025.

Telegram Bot API đáp ứng chính thức gần như mọi thứ tài khoản cá nhân Zalo mang lại.

**Khuyến nghị:**

- **Sản phẩm:** Zalo Bot Platform.
- **Kịch bản cần file/nhóm:** Telegram, kênh chính thức thứ hai.
- **Tài khoản Zalo cá nhân:** chỉ thí nghiệm ngắn trên tài khoản phụ chính chủ, tắt mặc định.

## 1. Nguồn chính thức đã xác nhận

**Zalo Bot Platform** (docs.zaloplatforms.com, bản "Stable v2.5.0"):

- ✅ Webhook chỉ có 5 sự kiện:
  - `message.text.received`
  - `message.image.received` (`photo_url`, `caption`)
  - `message.sticker.received`
  - `message.voice.received` (chỉ có `voice_url`, không có thời lượng hay MIME)
  - `message.unsupported.received`

  **Không có sự kiện file.** Người gửi thuộc nhóm được bảo vệ (trẻ em, người khuyết tật…) sẽ ra `unsupported`.
- ✅ `chat_type` là `PRIVATE` hoặc `GROUP (Beta)`. Trang hướng dẫn nhóm ghi "đang trong giai đoạn thử nghiệm nội bộ". Bot **chỉ nhận** tin @bot hoặc tin trả lời (quote) tin của bot.
- ✅ API gồm getMe, getUpdates, setWebhook, testWebhook, deleteWebhook, getWebhookInfo, sendMessage, sendPhoto, sendSticker, sendChatAction, sendVoice. **Không có sendDocument.**
  - `sendPhoto.photo` là URL.
  - `sendVoice` cần URL `.aac` và **chỉ gửi được trong chat 1-1**. Nếu gửi vào nhóm, API có thể trả về thành công nhưng tin không tới.
  - `sendChatAction`: `upload_photo` mới ghi "sắp ra mắt".
- ✅ Trang `call_api` nói `multipart/form-data` "dùng khi cần tải lên file như ảnh, tài liệu", nhưng không có trang method nào có tham số file. **Upload ảnh trực tiếp vẫn phải thử với bot thật.** `api.py` đã cài sẵn nhánh multipart.
- ✅ Polling và webhook loại trừ nhau. Tài liệu khuyên dùng getUpdates cho dev, webhook cho production. **Không công bố giới hạn tần suất** và không có trang điều khoản riêng cho bot.

**Zalo Official Account** (developers.zalo.me):

- ⚠️ Các trang render bằng JS nên chỉ đọc được tiêu đề. Từ tiêu đề có thể biết:
  - có webhook "Sự kiện người dùng gửi tin nhắn";
  - có mục **"Nhóm chat GMF – Sự kiện tin nhắn được gửi tới nhóm"**, tức OA có thể tham gia nhóm chat;
  - có API "Upload file" để gửi file;
  - có webhook `anonymous_send_file` cho người dùng ẩn danh.
- ⚠️ Chưa xác nhận được qua trang chính thức:
  - sự kiện `user_send_file` (chỉ thấy trong SDK bên thứ ba);
  - khung tin tư vấn (nguồn ghi 48 giờ, có nguồn ghi 7 ngày);
  - điều kiện và phí của nhóm GMF;
  - giá gói OA.

**Điều khoản Zalo:** ⚠️ `zalo.me/zalo/dieukhoan` render bằng JS, `zalo.me/dieu-khoan` chuyển sang trang đăng nhập. **Chưa trích được điều khoản gốc về công cụ tự động.**

**Telegram** (core.telegram.org):

- ✅ Người dùng gửi được mọi loại tin cho bot, kể cả file và voice.
- ✅ Bot tải file tối đa 20 MB và gửi file tối đa 50 MB. Ảnh tối đa 10 MB, gửi được bằng multipart, URL hoặc file_id. Tự chạy Bot API server riêng thì giới hạn lên tới 2000 MB.
- ✅ Privacy mode bật mặc định. Tắt privacy mode hoặc cho bot làm admin thì bot nhận **mọi tin nhóm**, trừ tin từ bot khác; tắt xong phải thêm bot vào nhóm lại.
- ✅ Giới hạn tần suất công bố: khoảng 1 tin/giây mỗi chat, 20 tin/phút mỗi nhóm, khoảng 30 tin/giây khi gửi hàng loạt.

## 2. Thư viện cho tài khoản cá nhân

| Thư viện | Tình trạng (10/2026) | Cơ chế |
|---|---|---|
| **zca-js** (RFS-ADRENO, TypeScript, Node/Bun) | Còn hoạt động: ~704 sao, 327 fork, 670 commit, 55 issue mở, bản 2.x. Bản npm gần nhất được nhắc là 2.1.2 ⚠️ (npm trả 403) | Giả lập Zalo Web. Đăng nhập QR hoặc `cookie + imei (z_uuid) + userAgent`. Listener WebSocket |
| **zlapi** (Python) | **Archive 19/03/2025**, bản cuối 1.0.2 | cookie + imei |
| **zalo-api-final** | Chỉ thấy gói trên unpkg, có vẻ đóng gói lại zca-js ⚠️ | như zca-js |
| Wrapper | OpenClaw `zalouser` (thử nghiệm), node n8n, nhiều fork | — |

Tính năng của zca-js theo README và GitBook:

- **Gửi:** `sendMessage` có `attachments` là file cục bộ (một hoặc nhiều), `quote`, `mentions`. Ngoài ra có `sendVoice`, `sendSticker`, `addReaction`, `undo`, `deleteMessage`, `createPoll`.
- **Nhóm và bạn bè:** `createGroup`, `addUserToGroup`, `getGroupInfo`, `getAllGroups`, `sendFriendRequest`, `acceptFriendRequest`.
- **Listener:** `message` (tin 1-1 và tin nhóm), `reaction`, `undo`, `group_event`.

⚠️ Chưa có tài liệu xác nhận hai điểm:

- Tin **nhận** dạng file có URL tải về hay không.
- Listener có nhận **đủ** mọi tin nhóm hay không. OpenClaw lưu tin nhóm không @ làm "pending history", tức là có nhận được loại tin này.

## 3. So sánh theo chức năng P0–P1

| Chức năng | Zalo Bot Platform (hiện tại) | Zalo OA | Zalo cá nhân + zca-js | Telegram Bot API |
|---|---|---|---|---|
| P0-3 nhận file CSV | ✅ **Không** (link hoặc ảnh chụp bảng) | ⚠️ Có thể (`user_send_file` qua SDK bên thứ ba) | Có (cần thử payload) | ✅ Có, ≤20 MB |
| P0-3 gửi ảnh biểu đồ | ✅ URL; multipart chưa thử | ⚠️ Upload API | Có, file cục bộ | ✅ Upload ≤10 MB |
| P0-5 voice vào | ✅ `voice_url` | ⚠️ `user_send_audio` | Có | ✅ Có, kèm `duration`/`mime_type` |
| Voice ra | ✅ `.aac` URL, chỉ 1-1 | ⚠️ | Có, cả nhóm | ✅ OGG/MP3/M4A |
| P0-6 tin chủ động | Như tin thường; không công bố giới hạn | ⚠️ Khung tư vấn 48 giờ hoặc 7 ngày; ngoài khung tốn quota hoặc ZNS | Như người thường; dễ bị coi là spam | ✅ Gửi tự do cho người đã /start; 1 tin/giây mỗi chat |
| P1-10 nhóm | ✅ Beta, nội bộ; chỉ @ hoặc reply | ⚠️ Có GMF, điều kiện chưa rõ | Thành viên thật, gần như đủ tin | ✅ Ổn định; tắt privacy là nhận đủ |
| Tóm tắt cả nhóm | Không | ⚠️ | Có (vướng quyền riêng tư) | ✅ Có (vẫn cần đồng ý) |
| Chi phí, thủ tục | Miễn phí | ⚠️ Gói trả phí, cần xác thực tổ chức | 1 SIM chính chủ | Miễn phí |
| Ổn định, hợp lệ | Chính thức | Chính thức | **Không chính thức**, dễ hỏng | Chính thức |
| Người dùng mục tiêu ở VN | Rất cao | Rất cao | Rất cao | Thấp hơn nhiều |

## 4. Rủi ro

**Rủi ro vận hành (tài khoản cá nhân):**

- **Một listener mỗi tài khoản** (README zca-js): mở Zalo Web là bot mất kết nối. Không được chạy hai bản cùng lúc, ví dụ dev và cloud.
- **Không có xác nhận giao nhận, không phát lại tin bị lỡ** khi socket đứt (theo OpenClaw). Tin đến lúc mất kết nối là mất.
- **Giao thức có thể đổi bất kỳ lúc nào.** Zalo Web đổi là thư viện hỏng; phải theo bản vá cộng đồng, không có SLA.
- Phiên bị đá ra, phải quét QR hoặc lấy cookie lại thủ công, nên không chạy không người trông được lâu.
- Không có số liệu công bố về giới hạn tần suất. Rủi ro tăng khi gửi hàng loạt, kết bạn hàng loạt, dùng tài khoản mới, đổi IP liên tục hoặc chạy trên IP datacenter.
- Khi bị khóa, nhóm, bạn bè và lịch sử của bot mất theo; không có kênh hỗ trợ.

**Rủi ro điều khoản:**

- ⚠️ Chưa có trích dẫn điều khoản gốc. Bản tóm tắt trên báo (CafeF) nêu chế tài nhắc nhở, hạn chế tính năng, khóa hoặc hủy tài khoản.
- Chính các thư viện đều cảnh báo:
  - zca-js: *"could get your account locked or banned"*;
  - GitBook: "làm trái với chính sách của Zalo";
  - zlapi: *"may not comply with Zalo's Terms"*;
  - OpenClaw: *"may result in account suspension or ban"*.

  Vì vậy cần coi đây là vi phạm điều khoản.

**Bảo mật:**

- Bộ cookie, imei và userAgent tương đương mật khẩu, đọc được mọi tin của tài khoản. Lưu như secret (quyền 600), không ghi log.
- Tách khỏi mọi tiến trình mà model có thể chạy lệnh.

**Quyền riêng tư:**

- Bot đọc tin của mọi thành viên nhóm. Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 và Nghị định 356/2025/NĐ-CP (hiệu lực 01/01/2026, thay Nghị định 13/2023) lấy sự đồng ý làm nguyên tắc.
- Cần: thông báo rõ, chỉ vào nhóm đã đồng ý, không lưu mặc định, có thời hạn xóa.
- Gửi tin nhóm lên LLM bên thứ ba cũng là chuyển dữ liệu ra ngoài.
- Telegram khi tắt privacy mode cũng vướng đúng vấn đề này.

**SIM:**

- Nghị định 163/2024/NĐ-CP và Thông tư 08/2026/TT-BKHCN (hiệu lực 15/04/2026) yêu cầu xác thực thuê bao, kể cả khuôn mặt.
- Từ SIM thứ hai phải xác thực OTP qua SIM đã đăng ký. Đăng ký trực tuyến tối đa 3 số mỗi giấy tờ. Đổi thiết bị phải xác thực lại ⚠️ (mốc áp dụng chưa thống nhất).
- **Tự đăng ký SIM chính chủ cho tài khoản bot không trái luật viễn thông.** Người đứng tên chịu trách nhiệm nội dung bot gửi. Không dùng SIM đứng tên người khác.
- Không tìm thấy quy định cấm riêng việc dùng SIM cho tài khoản tự động ⚠️.

**Bảo vệ đồ án:**

- Hội đồng có thể hỏi về tính hợp pháp và đạo đức.
- Tài khoản có thể bị khóa giữa kỳ, nên khó tái lập thí nghiệm.
- Lõi SCER-AH (định tuyến model) không cần quyền mở rộng của Zalo, nên **không được phụ thuộc** vào kênh này.

## 5. Kiến trúc đề xuất (chưa triển khai)

```
Zalo Bot Platform ─► plugin zalo (hiện có) ─┐
Telegram Bot API ──► plugin telegram* ──────┼─► lớp sự kiện chuẩn (IncomingMessage + attachments, platform)
Zalo cá nhân ⇄ sidecar Node zca-js ⇄ plugin zalo-personal (tắt mặc định) ─┘      │
                                                         allowlist · ai_usage · conversations · reminders · pilot
* Hermes đã có adapter Telegram; chỉ cần nối lớp chuẩn hóa và chính sách của dự án.
```

Ràng buộc cho sidecar:

- Tiến trình riêng, không có quyền chạy lệnh.
- Chỉ có các lệnh `send_text`, `send_file`, `react`, `download_attachment`.
- Tự giữ cookie trong `secrets/` (quyền 600).
- Giới hạn tốc độ ngay tại sidecar, ví dụ ≤1 tin/2 giây mỗi chat, vài chục tin/giờ.
- Không kết bạn hay tạo nhóm tự động.
- Có công tắc tắt khẩn `ZALO_PERSONAL_ENABLED=false`.
- Tin không chắc đã gửi thì không gửi lại.

Các bước thử an toàn:

1. Tài khoản phụ, SIM chính chủ.
2. Chạy một bản duy nhất trên máy có IP ổn định.
3. Allowlist người dùng và nhóm thử có đồng ý bằng văn bản.
4. Không lưu tin nhóm mặc định; che số điện thoại trước khi gửi lên LLM.
5. Ghi lại: payload file, số lần mất kết nối, cảnh báo nhận được.

## 6. Khuyến nghị

1. **Giữ Zalo Bot Platform cho sản phẩm và số liệu SCER-AH.** Bù giới hạn bằng link CSV/Sheets hoặc ảnh chụp bảng, ưu tiên thử multipart `sendPhoto`; trong nhóm chỉ làm việc khi được @.
2. **Telegram là kênh chính thức thứ hai** cho các kịch bản cần nhận file, voice hoặc ngữ cảnh nhóm. Hợp lệ, miễn phí, giới hạn rõ ràng, lại đúng hướng đa kênh P2-15. Hạn chế: ít người dùng ở VN, nên dùng cho benchmark và demo hơn là pilot với người dùng thật.
3. **Tài khoản Zalo cá nhân: chỉ thí nghiệm ngắn, tùy chọn.** Ví dụ demo nhận CSV ngay trong Zalo, có đồng ý, ghi rõ là kênh không chính thức. Không dùng làm sản phẩm.
4. **Không dùng OA**: có phí, cần xác thực tổ chức, khung tin tư vấn hạn chế, tài liệu khó kiểm chứng.

## 7. Thiếu sót P0 và phụ thuộc (chưa triển khai)

| P0 | Thiếu sót hiện tại | Phụ thuộc / việc cần làm trước |
|---|---|---|
| 1 Định tuyến model | Chưa có router đo theo bước | Không phụ thuộc kênh |
| 2 Bộ nhớ cá nhân | Bộ nhớ Hermes đang tắt vì dùng chung cho cả home | Kho bộ nhớ theo `user_id` hoặc nhóm |
| 3 Dữ liệu → biểu đồ | Zalo **không nhận file**; `sendPhoto` multipart **chưa thử với bot thật**; Railway không có Docker cho `execute_code` | (a) thử multipart; nếu bị từ chối thì cần endpoint công khai phục vụ ảnh (qua Admin). (b) Sandbox Docker trên VPS hoặc backend `ssh`/`modal`. (c) Muốn nhận file thì cần kênh Telegram, hoặc thí nghiệm `zalo-personal` |
| 4 Tìm kiếm có nguồn | — | API key tìm kiếm cho `web_search` |
| 5 Ảnh và voice | `events.py` chưa xử lý `message.voice.received`; `sendVoice` không dùng được trong nhóm | Tải `voice_url` rồi chạy STT của Hermes; xác định định dạng âm thanh (tài liệu không ghi MIME) |
| 6 Routine | Toolset `cronjob` đang tắt; chưa nối `send()` của cron vào hàng đợi `reminders`; Zalo không công bố giới hạn tần suất | Tự đặt hạn mức gửi |
| 7 Duyệt hành động | Chưa tách mã 4 số từ luồng đổi ca thành cơ chế chung | — |
| Chung | Nhóm Zalo vẫn thử nghiệm nội bộ; chưa có lớp sự kiện chuẩn hóa nhiều kênh (`attachments`, `platform`) | Lớp chuẩn hóa là điều kiện để thêm Telegram hoặc `zalo-personal` |

**Chưa kiểm chứng, cần trình duyệt hoặc thử thật:**

- Nội dung developers.zalo.me: OA file, khung tư vấn, GMF, giá.
- Điều khoản Zalo gốc.
- Payload file của zca-js.
- `sendPhoto` multipart trên Zalo Bot.

## Nguồn

**Chính thức ✅**

- Zalo Bot Platform:
  - https://docs.zaloplatforms.com/docs/BOT
  - https://docs.zaloplatforms.com/docs/BOT/webhook
  - https://docs.zaloplatforms.com/docs/BOT/apis/getUpdates
  - https://docs.zaloplatforms.com/docs/BOT/apis/sendPhoto
  - https://docs.zaloplatforms.com/docs/BOT/apis/sendVoice
  - https://docs.zaloplatforms.com/docs/BOT/apis/sendSticker
  - https://docs.zaloplatforms.com/docs/BOT/apis/sendChatAction
  - https://docs.zaloplatforms.com/docs/BOT/best-practices/build-bot-interaction-with-group
  - https://docs.zaloplatforms.com/docs/BOT/call_api
- Telegram:
  - https://core.telegram.org/bots/faq
  - https://core.telegram.org/bots/features#privacy-mode
  - https://core.telegram.org/bots/api#sendvoice

**Chính thức nhưng render JS ⚠️ (chỉ đọc được tiêu đề)**

- https://developers.zalo.me/docs/official-account/webhook/tin-nhan/su-kien-nguoi-dung-gui-tin-nhan
- https://developers.zalo.me/docs/official-account/webhook/nhom-chat-gmf/messgae_webhook
- https://developers.zalo.me/docs/official-account/tin-nhan/tin-tu-van/gui-tin-tu-van
- https://developers.zalo.me/docs/official-account/tin-nhan/quan-ly-tin-nhan/upload-file
- https://developers.zalo.me/docs/api/developer-notification/thong-bao-them-moi-mot-so-api-va-webhook-giup-giao-tiep-voi-nguoi-dung-an-danh-post-6677
- https://zalo.me/zalo/dieukhoan

**Thư viện và bên thứ ba**

- zca-js:
  - https://github.com/RFS-ADRENO/zca-js
  - https://www.npmjs.com/package/zca-js
  - https://tdung.gitbook.io/zca-js/
- zlapi:
  - https://github.com/Its-VrxxDev/zlapi
  - https://pypi.org/project/zlapi
- zalo-api-final: https://app.unpkg.com/zalo-api-final@2.1.1/files/dist/cjs/index.cjs
- OpenClaw:
  - https://docs.openclaw.ai/channels/zalouser
  - https://docs.openclaw.ai/channels/zalo
- COCO: https://docs.coco.xyz/channels/zalo
- SDK bên thứ ba cho OA:
  - https://app.unpkg.com/@warriorteam/redai-zalo-sdk@1.32.17/files/docs/WEBHOOK_EVENTS.md
  - https://unpkg.com/@warriorteam/redai-zalo-sdk@1.11.1/docs/CONSULTATION_SERVICE.md
  - https://github.com/ardennguyen/zalo-agent-cli/wiki/Official-Account
- Viindoo: https://viindoo.com/documentation/17.0/applications/productivity/zalo/getting-started.html

**Báo chí và pháp lý (thứ cấp)**

- Giá gói OA:
  - https://viettelstore.vn/tin-tuc/zalo-oa-co-mat-phi-khong
  - https://cellphones.com.vn/sforum/dang-ky-zalo-business
- Điều khoản Zalo:
  - https://cafef.vn/thao-tac-co-the-khien-tai-khoan-zalo-cua-ban-bi-khoa-vinh-vien-188260910160926853.chn
  - https://e.vnexpress.net/news/tech/enterprises/users-confused-by-sudden-terms-of-use-update-at-vietnam-s-most-popular-messaging-app-zalo-4999823.html
- SIM chính chủ:
  - https://plo.vn/muon-khong-bi-khoa-sim-chu-thue-bao-phai-xac-thuc-sinh-trac-hoc-theo-nhung-cach-nay-post902849.html
  - https://thuvienphapluat.vn/hoi-dap-phap-luat/dang-ky-thue-bao-di-dong-co-phai-xac-thuc-khuon-mat-khong-138085794.html
  - https://tuoitre.vn/xac-thuc-thue-bao-di-dong-ngan-lua-dao-20260129220131208.htm
- Bảo vệ dữ liệu cá nhân:
  - https://www.pwc.com/vn/vn/publications/legal-news-brief/20260128-new-rules-personal-data-protection.html
  - https://www.ey.com/vi_vn/technical/tax/tax-and-law-updates/nghi-dinh-so-356-2025-nd-cp-quy-dinh-chi-tiet-mot-so-dieu-va-bien-phap-thi-hanh-luat-bao-ve-du-lieu-ca-nhan
