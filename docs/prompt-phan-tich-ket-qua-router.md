# Prompt: phân tích kết quả bước 3 (dev-env1) để tìm thuật toán router

Cách dùng: chạy bằng chính Hermes với profile `research`, model `gpt-6.1-sol` đi qua gateway xingchi. Profile này đã cấu hình sẵn:
- tắt router assistant-core;
- tối đa 500 bước, giới hạn 6 giờ;
- code chạy trong Docker `hermes-bench-sandbox:2`, đã tắt mạng, có pandas, scikit-learn và statsmodels;
- có `web_search` và `web_extract` để tra paper. Hai tool này chạy từ tiến trình Hermes trên máy thật, nên sandbox vẫn tắt mạng;
- thư mục nghiên cứu được mount vào `/workspace`.

```bash
R=.runtime/research/router-$(date +%Y%m%d)        # phải khớp docker_volumes trong profiles/research/config.yaml
mkdir -p $R && cp .runtime/exports/dev-env1/*.csv docs/thuat-toan-router-chuyen-mot-lan.md \
  docs/phan-bien-chot-router.md docs/ban-chat-va-thiet-ke-lai-router.md $R/
sed -n '/^---$/,$p' docs/prompt-phan-tich-ket-qua-router.md | tail -n +2 > $R/PROMPT.md
cd $R && HERMES_HOME=$OLDPWD/.runtime/hermes-home/profiles/research hermes chat --yolo --query-file PROMPT.md
```

Nếu dùng ngày khác, sửa đường dẫn trong `docker_volumes`. Khi agent đã xong, có thể hỏi tiếp trong cùng phiên chat.

Tạo lại hai file CSV:

```bash
HERMES_HOME=.runtime/hermes-home/profiles/bench PYTHONPATH=src .runtime/venv/bin/python -m assistant_core.bench export \
  20261114-082827-fb81ce 20261114-110700-a493f4 20261114-195351-238ba7 \
  20261115-014437-e34293 20261115-070435-86e9e8 20261115-121146-4622cd --out .runtime/exports/dev-env1
```

Số liệu trong mục 3 tính trên đủ 6 run, xuất ngày 16/11/2026.

---

Bạn là nhà nghiên cứu về tác tử LLM, ra quyết định tuần tự và suy luận nhân quả. Bạn làm việc **tự chủ** trong một sandbox có Python và dữ liệu thực nghiệm thật (mô tả ở mục 2). Không ai trả lời câu hỏi giữa chừng: khi gặp chỗ mơ hồ, chọn giả định hợp lý nhất, ghi lại, rồi làm tiếp.

## 0. Mục tiêu

> **Tìm ra một ý tưởng router mới, có bằng chứng thực nghiệm offline, có khả năng giữ điểm DA-Code trong khoảng 0,05 so với always-strong mà giảm ít nhất 30% chi phí, và thắng các lịch đơn giản. Rồi lập báo cáo nghiên cứu cho tôi.**

"Mới" nghĩa là không chỉ chỉnh tham số của các hướng đã có (CIR, macro-action, router một lần chuyển, trong các file đính kèm nếu có). Ý tưởng có thể đến từ thuật toán, từ cách hai model chia việc, hoặc từ cách định nghĩa lại bài toán. Bạn được phép kết luận rằng không ý tưởng nào vượt được một lịch đơn giản. Đó vẫn là kết quả hợp lệ, miễn là có bằng chứng.

Hãy để dữ liệu quyết định. Đừng chọn ý tưởng trước rồi tìm số liệu minh họa.

Mục tiêu hoàn thành khi có đủ bốn thứ:
0. Một bản tổng quan tài liệu (`literature.md`) với ít nhất 12 công trình liên quan đã được đọc thật.
1. Ít nhất 8 ý tưởng đã được sàng lọc, mỗi ý tưởng có lý do giữ lại hoặc loại bỏ dựa trên một thực nghiệm.
2. Một đến ba phương án cuối có ước lượng offline về điểm và chi phí, kèm khoảng tin cậy, và so với các lịch đơn giản.
3. Báo cáo theo mục 6, có code tái tạo được mọi con số.

## 1. Bài toán

Hermes Agent giải task phân tích dữ liệu (benchmark DA-Code) theo vòng lặp: mỗi bước gọi một LLM, LLM sinh code hoặc lệnh, harness chạy tool, kết quả quay lại ngữ cảnh. Task dừng khi agent trả lời (`<answer>` hoặc file kết quả), chạm trần 25 bước hoặc hết giờ. Scorer DA-Code chấm điểm trong [0,1], gần như nhị phân.

Router được gọi trước **mỗi** lần gọi model và chọn một trong hai:

- **strong (S):** `gpt-6.1-sol`, giá 2,00 / 10,00 / 0,10 USD mỗi 1M token (input / output / cache read).
- **cheap (C):** `deepseek-flash`, giá 0,15 / 0,60 / 0,003 USD mỗi 1M token.

Router chỉ thấy metadata thực thi (không đọc nội dung tin nhắn), ví dụ: số bước, lỗi tool ở bước trước, timeout, thời lượng tool, chuỗi lệnh ngắn liên tiếp, độ tăng ngữ cảnh và lựa chọn ở bước trước. Mỗi bước gọi model đúng một lần, nên không thể thử C rồi lùi về S trong cùng bước. Muốn nâng lên S thì phải đợi tới bước sau.

Mục tiêu: điểm gần bằng always-strong (thua không quá 0,05) và chi phí giảm ít nhất 30%. Router phải thắng các lịch đơn giản, không chỉ thắng always-strong.

## 2. Dữ liệu đính kèm

Tập dev gồm 100 task: 23 `data`, 24 `di`, 22 `dm` và 31 `ml` (lấy từ phần sau của `task_id`, ví dụ `dc-ml-multi-003`). Có 6 run, mỗi run chạy đủ 100 task một lần, tuần tự, cùng môi trường và cùng config:

| label | chính sách |
|---|---|
| dev-S | always-strong |
| dev-C | always-cheap |
| dev-R25 / dev-R50 / dev-R75 | random: mỗi bước chọn S với xác suất 0,25 / 0,5 / 0,75, độc lập theo bước (propensity đã biết) |
| dev-L | rule: S ở bước đầu của lượt và ở bước ngay sau một tool call lỗi, C ở các bước còn lại |

`tasks.csv` có một dòng cho mỗi run × task, gồm các cột `label`, `policy`, `task_id`, `category`, `score`, `error`, `latency_s`, `steps`, `strong_steps`, `cheap_steps`, `switches`, số token, `cost_usd` (giá giờ thấp điểm), `cost_usd_billed` (DeepSeek tính ×2 vào giờ cao điểm), `tool_calls`, `tool_errors` và `tool_timeouts`.

`steps.csv` có một dòng cho mỗi bước gọi model, gồm các cột:

- `step_index`, `choice` (strong/cheap), `reason`, `switched` (1 nếu khác bước trước) và `retries`;
- `input_tokens` (phần **không** cache), `cache_read_tokens`, `output_tokens`, `reasoning_tokens`, `duration_s` và `cost_usd`;
- các tool call do bước này yêu cầu: `tool_calls`, `tool_errors`, `tool_timeouts`, `tool_max_s`;
- nhãn của cả task: `task_score`;
- các đặc trưng router thấy **trước** khi quyết định (`f_*`): `f_step`, `f_prev_tool_errors`, `f_prev_tool_timeout`, `f_prev_tool_duration_log`, `f_short_exec_streak`, `f_ctx_growth`, `f_prev_choice`, `f_chars`, `f_messages`, `f_elapsed_log`, `f_tool_time_cum`, `f_cum_tool_failures`, `f_error_streak`, `f_last_tool`.

Ghép hai file theo (`run_id`, `task_id`, `attempt`).

## 3. Những gì tôi đã thấy (hãy kiểm tra lại, đừng tin ngay)

| run | điểm | USD/task (TB / trung vị) | bước trung vị / p90 | tỉ lệ bước S | số lần đổi model / task |
|---|---|---|---|---|---|
| dev-R75 | 0,682 | 0,1069 / 0,0764 | 6 / 13 | 0,76 | 2,6 |
| dev-S | 0,675 | 0,0648 / 0,0484 | 5 / 10 | 1,00 | 0 |
| dev-R25 | 0,658 | 0,0485 / 0,0360 | 8,5 / 22 | 0,23 | 3,4 |
| dev-L | 0,573 | 0,0589 / 0,0335 | 14,5 / 25 | 0,15 | 2,8 |
| dev-R50 | 0,563 | 0,0608 / 0,0465 | 7 / 14 | 0,54 | 3,9 |
| dev-C | 0,535 | 0,0062 / 0,0041 | 10 / 25 | 0 | 0 |

1. **Độ lệch ghép cặp so với S (bootstrap theo task, 95%):**
   - C thua 0,140 [0,055; 0,224];
   - R50 thua 0,111 [0,030; 0,194];
   - L thua 0,102 [0,029; 0,179];
   - R25 thua 0,017 [−0,052; 0,086];
   - R75 thắng 0,007 [−0,037; 0,053].

   Điểm **không tăng đơn điệu** theo tỉ lệ bước S: R25 cao hơn hẳn R50. Mỗi cấu hình chỉ có 1 lần lặp. Trong pilot, C rất nhiễu (SD chênh hai lần lặp ≈ 0,32), còn S ổn định (≈ 0,002).
2. **Cấu trúc theo task:**
   - 46 task cả 6 run đều đúng và 19 task cả 6 run đều sai, nên chỉ 35 task có thể thay đổi.
   - 21 task S đúng mà C sai, và 4 task ngược lại.
   - Lợi thế của S tập trung ở nhóm `ml`: S 0,82, R75 0,76, R25 0,70, C 0,48.
   - Ở nhóm `data`, C bằng hoặc hơn S (0,81 so với 0,77).
3. **Trên 19 task S thắng rõ (S 0,91, C 0,00):**
   - R25 đạt 0,52 với 23% bước S;
   - R50 chỉ đạt 0,34 dù dùng 54% bước S;
   - R75 đạt 0,76 với 75% bước S.

   Trong ba run random, tương quan giữa tỉ lệ bước S của task và (điểm − điểm C) là 0,02. Câu hỏi mở: trộn model từng bước có gây hại không (do đổi kế hoạch, lệch phong cách, hay gãy mạch ngữ cảnh), hay R50 chỉ xui?
4. **Lựa chọn ở bước 1 trong các run random:** bắt đầu bằng C có 151 task, điểm 0,638. Bắt đầu bằng S có 149 task, điểm 0,630. Chưa thấy bằng chứng "S ở bước đầu" có lợi.
5. **Chi phí: trộn model không rẻ hơn, có khi còn đắt hơn.**
   - L chỉ dùng 15% bước S nhưng tốn 91% chi phí của S. R25 tốn 75%. R75 tốn **165%**.
   - Nguyên nhân thứ nhất có thể là **mất prompt cache**:
     - Ở dev-S, bước S có khoảng 70% token input được đọc từ cache, mỗi bước 0,0100 USD.
     - Ở R75, kể cả bước S không đổi model cũng chỉ có 54% đọc từ cache, mỗi bước 0,0171 USD. Có thể vì các bước C xen giữa đã thêm nội dung mà cache của S chưa có.
     - Ở L, bước S ngay sau khi đổi từ C tốn 0,0338 USD, so với 0,0162 USD cho bước S không đổi.
   - Nguyên nhân thứ hai là bước S rơi vào cuối quỹ đạo, khi ngữ cảnh đã dài.
   - Nguyên nhân thứ ba là quỹ đạo dài hơn khi C cầm lái: L có trung vị 14,5 bước, so với 5 ở S.
   - Đuôi chi phí nặng: vài task `ml-competition` ở R75 tốn 0,4–0,96 USD mỗi task.
6. **Cận trên:**
   - Oracle chọn S hoặc C cho cả task (chọn C khi C ≥ S) đạt 0,719 điểm với 0,0306 USD/task.
   - Luật "S cho task `ml`, C cho task khác" đạt 0,640 với 0,0362 USD/task. Router không thấy được nhóm task, nhưng con số này cho thấy tín hiệu cấp task đáng giá đến đâu.
7. **Chất lượng dữ liệu:**
   - 15 kết quả của R25 và R75 từng bị chấm 0 do provider trả 502/503 sau 3 lần thử. Chúng đã được chạy lại, nên một số task của R25/R75 có thời điểm chạy khác phần còn lại.
   - R50 mất 6 task vì câu trả lời thiếu thẻ `<answer>` mở đầu. Đây là lỗi của model và vẫn được tính.
   - Mỗi run có tối đa 1 task timeout. Có task chạm trần 25 bước, chủ yếu ở các run có C.

## 4. Quy trình tự nghiên cứu

Làm theo năm giai đoạn: L (tài liệu, xen kẽ với A), rồi A, B, C và D. Mỗi thực nghiệm là một thư mục `experiments/NN-ten-ngan/` chứa `run.py` (chạy lại được từ đầu, có cố định seed) và `result.md`. Trước khi chạy, ghi vào `result.md`:
- giả thuyết;
- phương pháp;
- **tiêu chí loại bỏ**, tức là kết quả nào sẽ khiến bạn bỏ ý tưởng.

Sau khi chạy, ghi kết quả, kết luận và quyết định tiếp theo. Mỗi thực nghiệm cũng thêm một dòng vào `journal.md`: thời gian, mã thực nghiệm, giả thuyết, kết quả một câu, quyết định. Giữ cả kết quả âm tính. Không xoá hay sửa kết quả cũ. Nếu một thực nghiệm có lỗi, tạo thực nghiệm mới ghi rõ nó thay thế thực nghiệm nào.

### Giai đoạn A: nền tảng (bắt buộc, làm trước)

1. **Kiểm tra và đo nhiễu.** Tái tạo bảng ở mục 3. Với mọi so sánh, dùng khoảng tin cậy ghép cặp theo task. Nói rõ chênh lệch nào thật, chênh lệch nào nằm trong nhiễu. Ước lượng cần bao nhiêu lần lặp hoặc bao nhiêu task để phát hiện chênh lệch 0,05.
2. **Mổ xẻ lợi thế của S.** Lợi thế đến từ đâu? Xét theo nhóm task, theo kiểu thất bại của C (không trả lời, chạm 25 bước, sai đáp án, lỗi tool lặp lại), và theo vị trí trong quỹ đạo. Có dấu hiệu metadata nào ở vài bước đầu tách được task "C sẽ hỏng" khỏi task "C làm được" không? Hãy định lượng bằng AUC hoặc độ chính xác, có cross-validation theo task.
3. **Hiệu ứng nhân quả từ các run random.** Propensity đã biết, nên hãy dùng IPW hoặc doubly robust. Ước lượng:
   - (a) hiệu ứng của việc chọn S ở bước k (theo vị trí và theo giai đoạn) lên điểm cuối và lên chi phí còn lại;
   - (b) hiệu ứng của **việc đổi model** tách riêng khỏi việc dùng S (điểm, số bước, cache);
   - (c) hiệu ứng có thay đổi theo đặc trưng `f_*` không.

   Nói rõ lực thống kê có đủ không. Cảnh báo về độ lệch phân phối: dữ liệu học dưới policy random đổi model liên tục, còn router thật thì hiếm khi đổi.
4. **Mô hình chi phí.** Lập công thức chi phí của một quỹ đạo theo số bước, độ dài ngữ cảnh, tỉ lệ cache và số lần đổi model. Kiểm chứng công thức trên dữ liệu. Suy ra loại lịch nào có thể rẻ hơn 30% mà vẫn giữ điểm.
5. **Đường cơ sở.** Ước lượng offline điểm và chi phí của các lịch đơn giản:
   - always-cheap;
   - S cho k bước đầu rồi C;
   - C rồi chuyển hẳn sang S một lần khi gặp tín hiệu;
   - chọn một model cho cả task.

   Đây là mức mọi ý tưởng phải vượt. Nói rõ lịch nào ước lượng được hợp lệ từ dữ liệu này và lịch nào không, và vì sao.

### Giai đoạn L: tổng quan tài liệu (làm song song với A, và quay lại bất cứ lúc nào cần)

Dùng `web_search` để tìm và `web_extract` để đọc (ưu tiên arXiv, OpenReview, ACL Anthology, trang của các hội nghị NeurIPS, ICML và ICLR). Các mảng cần tìm:
- LLM routing và cascade: RouteLLM, FrugalGPT, AutoMix, Hybrid LLM, router theo chất lượng hoặc chi phí, và các benchmark router;
- định tuyến hoặc chọn model **theo bước** trong tác tử nhiều bước, model lớn lập kế hoạch còn model nhỏ thực thi, và cách hai model chuyển giao cho nhau;
- dự đoán sớm thất bại của tác tử, hoặc tự đánh giá độ tự tin, từ quỹ đạo;
- đánh giá policy offline: IPW, doubly robust, off-policy evaluation cho policy tuần tự, kèm vấn đề ESS và hỗ trợ dữ liệu;
- bandit hoặc RL có chi phí, value of information, optimal stopping;
- chi phí suy luận: prompt caching, độ dài ngữ cảnh, và chi phí đổi model;
- benchmark tác tử khoa học dữ liệu (DA-Code, DSBench, DA-Agent và các benchmark tương tự).

Ghi vào `literature.md`, mỗi công trình một mục, gồm:
- tên, tác giả, năm và nơi công bố;
- link (arXiv id nếu có);
- ý chính trong 2–3 câu;
- **điều áp dụng được, hoặc không áp dụng được, cho bài toán này, và vì sao**;
- bạn đã đọc phần nào (abstract, phương pháp hay kết quả).

**Chỉ trích dẫn công trình bạn đã mở được bằng `web_extract`.** Không trích dẫn theo trí nhớ. Một con số lấy từ paper phải ghi rõ lấy ở đâu. Mỗi phần nội dung đọc được, hãy ghi ghi chú vào file ngay để không mất khi ngữ cảnh bị nén. Trong báo cáo, ghi rõ ý tưởng nào lấy từ tài liệu (kèm trích dẫn) và ý tưởng nào là mới. Kiểm tra xem ý tưởng "mới" của bạn đã có ai làm chưa.

Đừng để tổng quan chiếm hết thời gian: đọc khoảng 12–25 công trình là đủ. Sau đó quay lại tìm thêm khi một thực nghiệm cần.

### Giai đoạn B: sinh ý tưởng

Từ kết quả giai đoạn A và tổng quan tài liệu, viết ra ít nhất 8 ý tưởng, khác nhau về bản chất. Ví dụ về hướng (không bắt buộc theo):
- phát hiện sớm task mà C sẽ hỏng;
- giao toàn bộ task cho một model rồi kiểm tra lại;
- để S lập kế hoạch rồi C thực thi;
- tối ưu theo cache và độ dài ngữ cảnh;
- dừng sớm hoặc giới hạn số bước của C;
- học thời điểm chuyển model;
- các ý tưởng từ bandit, POMDP hoặc value of information.

Với mỗi ý tưởng, ghi một đoạn ngắn: cơ chế, bằng chứng từ giai đoạn A ủng hộ hay chống lại, và thực nghiệm rẻ nhất có thể loại bỏ nó.

### Giai đoạn C: vòng thực nghiệm

Lặp lại các bước sau:
1. Chọn ý tưởng có tỉ lệ (khả năng thành công × giá trị) trên chi phí thực nghiệm cao nhất.
2. Chạy thực nghiệm rẻ nhất có thể loại bỏ nó.
3. Loại, giữ hoặc tinh chỉnh ý tưởng.
4. Ghi lại vào journal.

Ý tưởng sống sót được đào sâu hơn: học từ dữ liệu, ước lượng offline đúng cách, phân tích độ nhạy.

Các loại thực nghiệm hợp lệ trong sandbox:
- phân tích lại dữ liệu;
- học mô hình với cross-validation theo task;
- đánh giá policy offline bằng IPW, doubly robust, hoặc giữ các quỹ đạo random khớp với policy;
- mô phỏng bán tổng hợp, hiệu chỉnh trên dữ liệu thật (nói rõ giả định của bộ mô phỏng, và đừng coi kết quả mô phỏng là bằng chứng ngang dữ liệu thật);
- phân tích lực thống kê.

Dừng vòng lặp khi có 1–3 phương án đủ mạnh, hoặc khi các ý tưởng còn lại đều không thể kiểm chứng thêm bằng dữ liệu này. Khi đó, ghi rõ cần dữ liệu gì thêm.

### Giai đoạn D: tổng hợp và kế hoạch kiểm chứng thật

Cho mỗi phương án cuối, nêu:
- luật quyết định chính xác và đặc trưng dùng;
- cách huấn luyện;
- ước lượng điểm và chi phí kèm khoảng tin cậy;
- so sánh với đường cơ sở;
- điều kiện khiến nó thua;
- ước tính công sức triển khai vào `src/assistant_core/router.py`. Một policy mới được cắm vào qua `register_policy`, nhận các đặc trưng `f_*` và trả về "strong" hoặc "cheap".

Sau đó đề xuất ít run thật nhất có thể để xác nhận:
- mỗi run khoảng 3–4 phút/task, chạy tuần tự, tổng ngân sách vài trăm USD;
- tập test 155 task chỉ chạy một lần, không dùng để chọn tham số;
- nêu tiêu chí thành công trước khi chạy.

## 5. Ràng buộc

- Không fine-tune LLM. Router chỉ đọc metadata (nếu đề xuất đọc nội dung, tách riêng thành phương án mở rộng và nêu chi phí). Không dùng tập test để chọn bất cứ thứ gì.
- Đề tài là đồ án tốt nghiệp, nên phương án phải triển khai và kiểm chứng được trong vài tuần.
- Chỉ dùng số liệu bạn tính được từ CSV. Không bịa con số. Khi một kết luận dựa trên dưới khoảng 20 task, ghi rõ là "gợi ý, chưa đủ bằng chứng".
- **Sandbox:**
  - Chỉ làm offline trên hai file CSV.
  - Không gọi API model và không chạy benchmark thật. Run thật chỉ được đề xuất ở giai đoạn D.
  - Sandbox chạy code không có mạng và không cài thêm được gì. Đã có sẵn pandas, numpy, scipy, scikit-learn và statsmodels. Internet chỉ dùng qua `web_search` / `web_extract`, để đọc tài liệu.
  - Không đưa dữ liệu từ CSV lên web, và không đưa nó vào truy vấn tìm kiếm.
  - Thư mục làm việc là `/workspace`, chứa hai file CSV và các tài liệu đính kèm. Mọi file bạn tạo nằm trong đó. Không sửa hai file CSV gốc.
  - Mỗi lệnh chạy tối đa 15 phút. Một tính toán dài hơn thì chia nhỏ ra hoặc ghi kết quả trung gian ra file.
- **Chống tự lừa:**
  - Mọi lựa chọn mô hình hay tham số đều làm bằng cross-validation theo task, hoặc trên một nửa task được giữ riêng. Báo cáo kết quả trên phần không dùng để chọn.
  - Ghi số ý tưởng và biến thể đã thử, để người đọc biết mức độ "chọn sau khi xem".
  - Với đánh giá offline, báo cáo kích thước mẫu hiệu dụng (ESS).

## 6. Sản phẩm bàn giao

Trong thư mục làm việc:

- `report.md` là báo cáo chính, viết bằng tiếng Việt, gồm:
  1. **Tóm tắt (tối đa 10 dòng):** điều dữ liệu nói chắc chắn, phương án bạn khuyên, mức tin cậy, và việc tôi nên làm tiếp.
  2. **Tổng quan tài liệu:** 1–2 trang, nêu các hướng chính và vị trí của bài toán này so với chúng, kèm trích dẫn.
  3. **Kết quả giai đoạn A:** bảng số, khoảng tin cậy, và kết luận một câu cho từng mục.
  4. **Bảng sàng lọc ý tưởng:** mỗi ý tưởng một dòng, gồm ý tưởng, cơ chế, thực nghiệm, kết quả chính, quyết định (loại / giữ / chưa kiểm chứng được), nguồn (trích dẫn hoặc "mới") và mã thực nghiệm.
  5. **Các phương án cuối:** chi tiết theo giai đoạn D, kèm bảng so sánh với đường cơ sở (điểm, chi phí, khoảng tin cậy, ESS).
  6. **Kế hoạch run thật và tiêu chí thành công.**
  7. **Hạn chế và mối đe doạ đến tính hợp lệ.**
  8. **Danh sách thực nghiệm**, kèm đường dẫn.
- `literature.md`: ghi chú về từng công trình đã đọc.
- `journal.md`: nhật ký theo thời gian.
- `experiments/`: mỗi thực nghiệm một thư mục, như mô tả ở mục 4.
- `reproduce.sh`: chạy lại toàn bộ thực nghiệm và tạo lại mọi bảng trong báo cáo.

Khi xong, in ra phần tóm tắt của `report.md`. Ghi file thường xuyên trong lúc làm, để nếu phiên bị ngắt thì chạy lại được và không mất kết quả.
