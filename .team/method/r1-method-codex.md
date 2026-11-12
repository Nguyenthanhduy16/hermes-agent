# Vòng 1 — method-codex

## 1. Lỗ hổng, theo mức nghiêm trọng

1. **Nhãn sai nghĩa:** “tầng tối thiểu đủ” giả định năng lực lồng nhau; Specialist có thể đúng khi Frontier sai. Vì vậy tổng tích lũy không mặc nhiên là xác suất thành công; P_Frontier=1 còn khiến logit vô hạn. Chạy Fast/Frontier không gán được nhãn Specialist. Đúng một bước cũng chưa bảo đảm đúng cả quỹ đạo.
2. **Thống kê:** 300 bước trong 20–30 tác vụ không phải 300 mẫu độc lập. Chia ngẫu nhiên bước hoặc biến thể cùng mẫu gây rò rỉ; skill sinh từ test làm nhiễm warm-start. Ablation −Skill đồng thời bỏ đặc trưng và tiêm prompt nên không tách được nguyên nhân.
3. **Chi phí/quyết định:** giá/token × số bước không cùng đơn vị với giá/token × token. Mất cache phụ thuộc provider/prefix; đổi toolset/skill cũng đổi prefix. Entropy cao biểu thị phân tán, chưa chứng minh cần model mạnh. Luật lỗi lần hai ở §4.3 mâu thuẫn leo thang ngay ở §4.5; bước “định dạng” chưa có cách nhận diện đáng tin.
4. **Phạm vi/tính mới:** 14 cấu hình ×100 tác vụ ×3 lần =4.200 lượt, chưa tính pilot. [RouteLLM](https://arxiv.org/abs/2406.18665) đã học routing, [FrugalGPT](https://arxiv.org/abs/2305.05176) đã học cascade; [Jev Router](https://openrouter.ai/docs/guides/routing/routers/jev-router) đổi model/effort theo tiến trình. Đóng góp nên là lợi ích đo được của trạng thái harness và tái dùng skill; chưa đủ căn cứ tuyên bố thuật toán đầu tiên. Jev cũng thấy lỗi tool được đưa vào hội thoại.

## 2. Thuật toán sửa

- MVP hai tầng Fast/Frontier; Specialist là mở rộng. Học q_F=P(checker bước đạt|s,harness), không ép thứ tự năng lực. Dùng logistic L2 với pha, token, loại lỗi, skill-match và lịch sử thành công có làm trơn; bỏ PCA/LightGBM lúc đầu.
- Lấy trạng thái trước quyết định từ cả quỹ đạo Fast và Frontier; snapshot sandbox rồi chạy Fast trên mẫu phân tầng. Gán y_F bằng checker; chỉ gọi Frontier để cứu lỗi và kiểm toán ngẫu nhiên. Không lấy Frontier làm đáp án chuẩn. Kiểm tra lại bằng chạy toàn tác vụ vì replay bước không đo tác động dây chuyền.
- Học trên pilot, chọn ngưỡng trên dev tách theo tác vụ/họ; log mọi quyết định. Jev chỉ là đối chứng tùy ngân sách. Nếu giữ, fit logit(q)=a·logit(clip(P))+b+u·skill−v·error bằng regularization trên train, a>0; chọn ngưỡng riêng trên dev. Không mặc nhận xác suất đúng miền ([OpenRouter](https://openrouter.ai/blog/tutorials/how-to-use-jev/)). Bỏ entropy khỏi MVP.
- Tính C_k=(r_in·n_uncached+r_read·n_cached+r_write·n_write+r_out·n_out)/10⁶, các nhóm token không trùng nhau. Dự báo output bằng trung vị theo pha, cache theo provider/prefix thực đo; chi phí chuyển đã nằm trong C_k, không cộng lại. Bỏ L_remain trong MVP: đang Fast thì giữ nếu q_F≥τ; đang Frontier chỉ hạ nếu q_F≥τ và C_F+δ<C_cur; còn lại Frontier. Nếu cần nhìn xa, ước lượng bước còn lại bằng trung vị theo pha trên train.
- Lỗi schema: sửa cục bộ một lần; lỗi thực thi/checker: Frontier bước kế, giới hạn bước/tiền. Hết ngân sách thì dừng có trạng thái. Chưa có skill không bắt buộc Frontier; không hạ Fast vô điều kiện khi “định dạng”. AST không thay sandbox; giữ chốt an toàn ở mọi tầng.

## 3. Điểm móc đã kiểm chứng

- Entry: `plugins/assistant-core/__init__.py:4`; đăng ký hook tại `src/assistant_core/hermes_plugin.py:184`. `src/assistant_core/hermes_plugin.py:146` chỉ ghi telemetry; `src/assistant_core/hermes_plugin.py:133` veto sau khi model chọn tool, chưa phải prune schema hay đổi model.
- Đề xuất proxy OpenAI-compatible cho MVP model-routing; truyền state có session/turn/request ID từ adapter/core. Đổi prompt/schema và kiểm tra kết quả sandbox cần adapter/core, chưa có bằng chứng hook hiện tại hỗ trợ. `.runtime/hermes-agent/` vắng mặt: chưa thể chỉ dòng vòng lặp upstream.
- `src/assistant_core/telemetry.py:23` có token/cache nhưng thiếu router/features/skill-version/checker-label; hook bỏ result ở `src/assistant_core/hermes_plugin.py:142`. `src/assistant_core/settings.py:12` chặn `skill_manage`: học skill ngoại tuyến, duyệt rồi đóng băng.
- `src/assistant_core/bench.py:607` chỉ chấm văn bản; thêm kiểm tra bảng số/artifact. Tái dùng nối session ở `src/hermes_zalo/admin_bench.py:286` và giá cache ở `src/hermes_zalo/admin_observe.py:433`. Theo `docs/tro-ly-zalo.md:57`, demo dùng link CSV thay gửi file.

## 4. Thực nghiệm 10 tuần

Tuần 1–2: checker số liệu/artifact, ghim runtime/model/giá; 20 pilot, 20 dev, 60 test theo họ/dataset. Tuần 3–4: proxy, telemetry, nhãn. Tuần 5–6: router và skill ngoại tuyến. Tuần 7–8: test khóa kín. Tuần 9–10: phân tích, demo, báo cáo.

So Fast, Frontier, luật+cascade, Jev nếu khả dụng, SCER trên harness chung; ablate trạng thái/tiêm-skill/switch riêng trên tập con định trước. Lặp ba lần hai cấu hình chính; bootstrap ghép cặp theo tác vụ/họ, không theo bước. Báo TSR, tổng tiền kể cả thất bại/router/học-skill, tiền/thành-công, p95, Brier. Không tuyên bố tương đương khi khoảng tin cậy rộng. Warm-start thử riêng biến thể họ đã học; test tổng quát dùng họ chưa thấy và kho skill khóa. Pilot quyết định trần USD và số lượt trước chạy; dự toán gồm nhãn, ablation, dự phòng 20%; quá trần thì giảm lượt theo thứ tự chốt trước.

## 5. Xin ý kiến thầy

1. Hai tầng có đủ phạm vi khoa học?
2. Ưu tiên chứng minh trạng thái harness hay vòng học skill?
3. Chấp nhận giảm TSR tối đa bao nhiêu?
4. Ngân sách và quy mô test tối thiểu là bao nhiêu?
