# Hỏi bot bằng ngôn ngữ tự nhiên

Thành viên có thể dùng câu thường hoặc lệnh để tra tài liệu/quy định Lab và dùng các chức năng công việc đã bật. Bot không trò chuyện kiến thức chung. Trong nhóm đã được cấp quyền, chọn **@Bot LabCoach** hoặc trả lời tin của bot như trước. Đăng ký và đổi ca vẫn làm trong tin nhắn riêng.

## Câu thử

| Bạn nhắn | Bot xử lý |
| --- | --- |
| Mai mình có lịch không? | Tra lịch theo danh tính đã đăng ký; giữ mẫu câu hiện có |
| Chiều mai ai trực lab? | Tra lịch chung được cấp quyền |
| Xem lịch tuần sau của mình. | Tra lịch cá nhân theo tuần |
| Giúp kiểm tra lịch của mình ngày kế tiếp nhé | AI hiểu ý, code tính ngày theo giờ Việt Nam và tra lịch |
| Cho coi lịch hn / lịch tuần tới | Hiểu thêm `hn`, `ng mai`, `tmr`, `tuần tới` khi AI nhận diện đúng |
| Mình muốn đổi ca sáng ngày 16/10 với Lãm | Mở `/doica`, nhắc lại thông tin được nói tới; bạn chọn đúng ca và người trong luồng hiện có |
| Có yêu cầu đổi ca nào đang chờ mình đồng ý không? | Mở danh sách `/choxacnhan` trong DM |
| Mình muốn báo lỗi bot. | Hướng dẫn tạo issue khi chức năng được bật |
| Ghi nhận sự cố Không lưu, mô tả Bấm nút nhưng không có phản hồi | AI có thể tạo bản nháp từ nội dung bạn cung cấp; chưa ghi Sheets |
| Quy định xin nghỉ phép thế nào? | Tìm trong tài liệu được cấp quyền, trích đoạn kèm tên nguồn và dòng; không có thông tin thì báo rõ |
| Viết giúp mình một bài quảng cáo | Từ chối ngắn gọn, gợi ý `/menu`; không nhờ AI viết câu trả lời tự do |

AI có thể hiểu khác giữa các lần gọi, nhất là `openrouter/free`. Bảng trên là tình huống cần thử, không bảo đảm chất lượng của mọi model. Nếu chưa hiểu, dùng `/menu`, `/lich`, `/lichcuatoi`, `/doica`, `/choxacnhan`, `/issue`.

**LiteLLM / DeepSeek Flash:** xem [cấu hình model, JSON mode, token và hạn mức](model-litellm.md). Máy chủ đã kiểm tra dùng JSON mode; không áp nguyên cấu hình JSON Schema của OpenRouter.

## Bật trên Windows

1. Giữ bot Windows tắt nếu Railway đang chạy cùng Bot Token.
2. Cập nhật bản plugin cục bộ: `.\scripts\run.ps1 Update`.
3. Kiểm tra cấu hình model: `.\scripts\run.ps1 NluCheck`.
4. Kiểm tra một câu **giả lập** với model thật: `.\scripts\run.ps1 NluCheckLive`. Không đọc/ghi Sheets, không nhận/gửi Zalo. Lệnh có gọi API model đang cấu hình và chịu hạn mức/chi phí của tài khoản đó.
5. Khi kiểm tra đạt, thêm vào `.env`:

```dotenv
ZALO_NLU_ENABLED=true
ZALO_NLU_TIMEOUT_SECONDS=12
ZALO_AI_USER_DAILY_LIMIT=10
ZALO_AI_TOTAL_DAILY_LIMIT=300
```

6. Khởi động lại bot tại nơi được chọn chạy. Không chạy đồng thời Windows và Railway.

Không cần đổi Bot Token/API key hoặc model chỉ để bật tính năng. Nếu API key cục bộ bị từ chối, cập nhật trên máy qua cách cấu hình model hiện có; không gửi key vào chat. Key trên Windows và Railway có thể khác nhau.

## Cập nhật bot Railway

1. Triển khai mã mới lên **đúng service hiện tại**, giữ Volume `/data`, các biến model, phân quyền và dữ liệu như cũ. Chỉ đổi Variables mà không triển khai mã mới sẽ chưa có tính năng này. Xem [hướng dẫn Railway](deploy-railway.md).
2. Có thể kiểm tra trên container bằng `/opt/hermes/venv/bin/python /app/scripts/nlu_check.py --runtime --live`. Lệnh chỉ gửi một câu mẫu tới model, không khởi động gateway thứ hai.
3. Thêm `ZALO_NLU_ENABLED=true`, `ZALO_NLU_TIMEOUT_SECONDS=12`, `ZALO_AI_USER_DAILY_LIMIT=10`, `ZALO_AI_TOTAL_DAILY_LIMIT=300` trong Variables rồi triển khai lại để áp dụng. Hai hạn mức phải là số nguyên dương; không đặt `0` để bỏ giới hạn. Giữ nguyên `HERMES_MODEL` và `HERMES_PROVIDER`.
4. Thử các câu trong bảng bằng một thành viên đã đăng ký. Trong nhóm phải @bot. Với đổi ca/issue, kiểm tra bản xem trước trước khi quyết định xác nhận thao tác thật.

Muốn tắt bước AI: đặt `ZALO_NLU_ENABLED=false` rồi khởi động/triển khai lại. Lệnh, mẫu câu cũ và dữ liệu đã lưu vẫn dùng được; bot vẫn không mở lại trò chuyện kiến thức chung.

## Phạm vi và giới hạn

- Thứ tự: **lệnh → mẫu câu đã biết → AI phân loại câu chưa hiểu → kiểm tra dữ liệu/quyền → chức năng có sẵn**. Luồng chọn ca đang mở được ưu tiên; lý do đổi ca không gửi sang AI.
- AI chỉ phân loại ý định bằng model/provider hiện có. Code kiểm tra kết quả và gọi chức năng; không cho model tự chạy công cụ, viết Sheets hoặc tạo câu trả lời kiến thức chung. Câu ngoài phạm vi nhận hướng dẫn dùng `/menu`.
- Câu hỏi tài liệu dùng bộ tìm kiếm cục bộ hiện có: trả **trích đoạn nguyên văn kèm nguồn**, chưa dùng model diễn giải. Tài liệu phải được cấp quyền trong `manifest.json`. Không có nguồn được cấp quyền thì báo “Chưa có tài liệu được cấp quyền cho bạn trong cuộc trò chuyện này.”; không tìm thấy nội dung phù hợp thì báo thiếu thông tin, không chuyển sang AI tự trả lời.
- AI chỉ nhận tin nhắn hiện tại và tên các khả năng được phép. Không nhận user ID, chat ID, danh sách Workload, toàn bộ Sheets, lịch sử đăng ký hoặc hội thoại trước. Tin đăng ký/số điện thoại bị chặn trước bước AI.
- Danh tính cá nhân lấy từ đăng ký hiện có; tên trùng cần ánh xạ mã thành viên, không đoán. Tra tên người khác qua AI cần họ tên khớp duy nhất Workload; tên chưa rõ thì bot yêu cầu làm rõ.
- Thời gian do code tính theo UTC+7. Ngày dạng `16/10` dùng năm hiện tại như lệnh cũ. Ngày mơ hồ như “cuối tuần”, nhiều ngày/yêu cầu hoặc thiếu ngữ cảnh sẽ hỏi lại. Ngoài wizard đổi ca hiện có, **chưa có bộ nhớ hội thoại nhiều lượt**: hãy gửi lại câu đầy đủ.
- AI bắt đầu luồng đổi ca, **chưa tự điền/chọn ca hay người đổi cùng**. Dù đã nêu tên/ngày trong câu, bạn vẫn chọn các mục từ lịch thật. Chỉ `/doica gui MÃ` gửi yêu cầu; người nhận vẫn đồng ý/từ chối theo quy trình cũ.
- AI có thể chuẩn bị bản nháp issue; chỉ lệnh xác nhận của đúng người, đúng cuộc trò chuyện mới tạo issue. Chế độ `managed` lưu trên Railway và hiển thị ở Admin; `legacy` mới ghi tab Sheets đã cấu hình. Không có intent cho duyệt thành viên, tự đồng ý đổi ca, xóa dữ liệu hay chạy công cụ.
- Nhắc việc qua AI mới gồm xem, lịch sử và hướng dẫn tạo. Cú pháp tạo/hủy hiện có vẫn dùng trực tiếp. Tính năng chưa bật hoặc ngoài quyền không được AI mở thêm.
- Lịch sử tin nhận/gửi vẫn được lưu ở Admin khi `ZALO_CONVERSATIONS_ENABLED=true`, theo quyền và thời hạn lưu hiện có. Tin ngoài phạm vi không được đưa vào ngữ cảnh gửi model, bản tóm tắt hoặc ghi nhớ tự động. Phiên bản này không gọi nhánh model trò chuyện tự do; quản trị vẫn có thể xem lịch sử để kiểm tra lỗi.
- Hiện hỗ trợ provider Hermes `openrouter` và `custom` dùng Chat Completions qua HTTPS. Những API khác chưa được hỗ trợ ở bước phân loại, không tự chuyển provider/model.
- Mỗi câu chưa hiểu có một lượt phân loại, mặc định chỉ thử lại HTTP 429 tối đa 2 lần (cấu hình `ZALO_NLU_MAX_RETRIES=0..3`); chờ theo `Retry-After` hoặc tăng từ 1 giây, trong cùng timeout. Không tự đổi model. Mặc định yêu cầu JSON Schema; endpoint chỉ hỗ trợ JSON dùng `ZALO_NLU_RESPONSE_FORMAT=json_object`. Cả hai đều kiểm tra cấu trúc và nguồn thông tin ở code. Lỗi mạng, timeout, hết hạn mức hoặc model không tương thích: gợi ý dùng lệnh.
- Hạn mức ngày mặc định: **10 lượt AI/người, 300 lượt/toàn bot**, đổi ngày lúc 00:00 theo UTC+7. Dùng chung hạn mức của một tài khoản Zalo ở DM và các nhóm. Bộ đếm lưu bền vững trong `ZALO_STATE_DIR` trên Volume Railway; khởi động hoặc triển khai lại không làm mất lượt đã dùng. Lệnh, mẫu câu đã hiểu và trả lời tài liệu trực tiếp không tiêu tốn lượt phân loại AI.
- Một câu được đưa vào bước phân loại tính một lượt, kể cả câu ngoài phạm vi hoặc model lỗi. Thử lại HTTP 429 trong cùng lượt không tính thêm hạn mức nội bộ, nhưng nhà cung cấp vẫn áp dụng cách tính request/token của họ. Đây là giới hạn **số lượt**, không phải trần chi phí USD. Hết hạn mức thì bot gợi ý dùng lệnh; lệnh vẫn hoạt động.
- Giới hạn bổ sung: 2.000 ký tự đầu vào (quá giới hạn thì yêu cầu dùng lệnh, không cắt nội dung), 10 lượt/người/phút, 60 lượt/toàn bot/phút, timeout cấu hình 3–30 giây. Sau lỗi HTTP không khôi phục được tạm ngừng phân loại ít nhất 60 giây, có thể lâu hơn theo `Retry-After`. Chỉ bộ đếm phút trong bộ nhớ được đặt lại sau khởi động. `ZALO_NLU_MAX_TOKENS` mặc định 2.048; tham số suy nghĩ chỉ gửi nếu cấu hình `ZALO_NLU_THINKING=enabled/disabled` và endpoint hỗ trợ.
- Báo cáo thử nghiệm có mục `nlu` (số lượt, lỗi, thời gian). Bước phân loại không lưu nội dung câu/đầu ra model trong log. Chi phí lượt phân loại chưa gộp vào chi phí hội thoại Hermes; xem Usage tại nhà cung cấp để biết tổng chi phí.

## Kiểm thử

` .\scripts\run.ps1 Test ` chạy các kiểm thử mô phỏng, không gọi model/Zalo/Sheets thật. Cần kiểm tra schema sai, lỗi/timeout/hạn mức, quyền, trùng tên, tách người/cuộc trò chuyện, sự kiện trùng, các bước xác nhận, câu ngoài phạm vi và hạn mức ngày sau khởi động lại. `.\scripts\run.ps1 TestHermes` dùng Hermes đã cài, vẫn mô phỏng model và Zalo.

`NluCheckLive` chỉ kiểm tra một câu mẫu với model thật. **Chỉ xác nhận hoạt động Zalo sau khi thành viên tự nhắn và nhận được phản hồi thật.**

Tham chiếu kỹ thuật: [OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs), đối chiếu ngày 13/10/2026. Khả năng hỗ trợ phụ thuộc endpoint; với OpenRouter, request yêu cầu `require_parameters=true` và vẫn được kiểm tra lại cục bộ.
