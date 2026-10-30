# Kiến trúc LLM: người dẫn Phật pháp đời sống

Cập nhật 2026-10-30. Chính sách vận hành: AI tự chọn, tự soạn và tự phát lời đạt kiểm tra. Không có bước duyệt của con người cho từng bình luận. `gpt-6-luna` là model cố định cho mọi lượt LLM theo yêu cầu chủ dự án.

## Các ranh giới

| Thành phần | Trách nhiệm |
| --- | --- |
| `LiveStateStore` | SQLite ledger cho comment, reply và speech job; khử trùng lặp; lease; retry; commit nguyên tử; phục hồi sau crash. |
| `LiveReplyEngine` | Lọc câu vô nghĩa/rủi ro, chọn và nhóm câu, tra nguồn, soạn lời, kiểm tra nội dung. |
| `OpenAIResponses` | Gọi Responses API, Structured Outputs và moderation. |
| `DharmaCatalog` | Chín thẻ nguồn Phật học với tóm tắt tiếng Việt do dự án viết và URL bài kinh. |
| `LiveReplyCoordinator` | Nhịp poll, điều phối LLM, TTS, video và phản hồi trạng thái media. |
| `SegmentStreamer` | Cấp hai track cho encoder, chỉ xác nhận khi cả hai thành công và publisher đang chạy. |

`comments.id` là ID chuẩn gồm tiền tố nguồn cho TikTok, Facebook cá nhân, dashboard và demo; Facebook Page dùng ID Graph gốc. `comments.source` lưu nguồn riêng. `reply_comments` biểu diễn một lời có thể trả lời một nhóm câu thật sự cùng ý. Một bình luận khác ý chuyển `deferred`, không đánh dấu đã đáp. `speech_jobs` có ID ổn định; đoạn thứ hai chỉ được nhận khi đoạn đầu đã `fed_to_encoder`.

Chế độ nói liên tục chạy một tác vụ tạo lời nền độc lập với tác vụ trả lời bình luận. Nó giữ sẵn khoảng bốn job, khởi động bằng một lời địa phương đã viết sẵn, rồi dùng Luna cho lời mới. Nếu Luna chậm hơn 8 giây hoặc lỗi, lời địa phương nối nhịp. Bình luận mới không cắt lời đang phát; câu trả lời được ưu tiên khi đã render xong. Để tránh khoảng dừng của hai clip cho cùng một ý, engine hiện viết một `utterance` trọn câu. Streamer chỉ chờ tối đa một giây khi bộ đệm đầu có một clip.

Lời giảng nền đi theo từng chương năm đoạn: mở một tình huống, giải thích một ý Phật học, làm rõ phản ứng hoặc hiểu lầm, gợi một bước thực tập, rồi khép ý và dẫn sang chủ đề tiếp theo. Engine đưa đoạn vừa nói và đoạn cuối của chương vào prompt để Luna viết tiếp. Chỉ khi lời mới được lưu vào hàng phát, chương mới tiến thêm một đoạn; nếu các đoạn đang chờ bị hủy vì cần phát câu trả lời bình luận, chương quay lại đoạn chưa phát. Lời mời chia sẻ và câu trả lời có thể xen giữa hai đoạn mà không đổi chủ đề đang giảng.

Chương trình Trạm Bình An có lời mở đầu cố định (danh hiệu Phật và lời mời chia sẻ ngắn), nhãn Avatar AI trên hình phát, lời mời chia sẻ khoảng 3 phút một lần và lời giới thiệu affiliate chỉ khi `AFFILIATE_CART_CONFIRMED=true`. Lời affiliate đầu tiên sau khoảng 5 phút; các lời sau cách ít nhất 15 phút và không phát trong 3 phút sau bình luận mới. Lời nói chỉ nhắc nhẹ vòng trong giỏ hàng; nhãn “Giỏ hàng: liên kết tiếp thị” hiển thị trên video khi bật. Kịch bản không gán phép hộ thân hay đổi vận cho vòng. Đầu ra LLM bị giữ lại nếu tự chen lời bán vào câu trả lời không hỏi về sản phẩm. Chi tiết văn bản ở [TRAM_BINH_AN_PROMPT.md](TRAM_BINH_AN_PROMPT.md).

## Quy trình một lượt

1. Lưu comment mới; hết hạn chờ chuyển `expired`. Giữ `legacy_processed` cho dữ liệu cũ không có bằng chứng phát.
2. Bỏ URL quảng cáo, lời chào đơn thuần, chuỗi lặp và lệnh điều khiển AI. Chạy moderation đầu vào. Tự hại hoặc đe dọa rõ ràng đi tới mẫu lời hỗ trợ an toàn; nội dung bị gắn cờ khác được giữ lại tự động.
3. Một câu hợp lệ đi thẳng; nhiều câu dùng Luna để chọn ID chính, các ID cùng ý, ID bỏ qua và truy vấn nguồn. Ứng dụng kiểm tra tất cả ID phải tồn tại và không chồng nhau.
4. Catalog tra từ khóa không dấu. Câu hỏi giáo lý thiếu nguồn được tự giữ lại; không phát một câu trả lời chung không đúng ý. Nội dung nguồn vào model là dữ liệu, không có quyền thay chính sách.
5. Luna viết một đoạn nói trọn ý. Prompt yêu cầu đáp đúng băn khoăn trước, ví dụ đời thường sau, không trích nguyên văn kinh hay bịa nguồn. Schema trả lời có `utterances`, `source_ids`, `withhold`, `withhold_reason`; `withhold` là tín hiệu **tự giữ lại**, không tạo hàng duyệt.
6. Ứng dụng kiểm tra độ dài, dấu kết câu, Markdown/URL, ID nguồn, trùng nội dung gần đây và kết quả moderation đầu ra. Một lượt sửa có giới hạn. Nếu vẫn không đạt, ghi `withheld` và không phát. Chỉ lời đã qua kiểm tra mới vào outbox.
7. TTS và video retry có giới hạn khi chưa vào feeder. Từ lúc feeder bắt đầu, lỗi hoặc crash chuyển `uncertain` và không tự nói lại. `fed_to_encoder` chỉ xác nhận đã cấp cho encoder, không là xác nhận người xem đã nghe.

Công tắc `PAUSE_AI_SPEECH` tạm ngừng tạo và dispatch lời AI; video idle vẫn hoạt động. Dashboard hiển thị comment, trạng thái, lý do, lời đã tạo và URL nguồn. Đổi nội dung giọng hoặc chủ đề áp dụng ở lượt sau. Cấu hình dashboard không thể thay đổi quy tắc an toàn trong code.

## Hợp đồng OpenAI

- Responses API với `text.format={type:json_schema, strict:true}`, schema có `additionalProperties:false` và trường bắt buộc đầy đủ. Từ chối/refusal và response chưa hoàn tất là lỗi, không phát text delta trực tiếp. [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
- `gpt-6-luna`, `reasoning.effort=none` cho chọn câu và `low` cho soạn. Mọi response dùng `store:false`; ứng dụng tự giữ context ngắn. [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [Reasoning](https://developers.openai.com/api/docs/guides/reasoning).
- `/moderations` phân loại đầu vào; Responses inline `moderation` kiểm tra đầu ra. Thiếu kết quả moderation đầu ra hoặc moderation lỗi thì không phát bản nháp. [Moderation](https://developers.openai.com/api/docs/guides/moderation).
- `instructions` chứa chính sách bất biến do code sở hữu; bình luận, tên, thẻ nguồn và tùy chọn phong cách được JSON hóa trong `input` như dữ liệu không đáng tin cậy. [Text generation](https://developers.openai.com/api/docs/guides/text).

## Nội dung Phật học

Bản đầu dựa vào SN 56.11 (Tứ diệu đế), AN 6.63 (tác ý/nghiệp), SN 22.59 (vô ngã), SN 36.6 (hai mũi tên), MN 10 (chánh niệm), SN 12.2 (duyên khởi), MN 58 (lời nói), Sn 1.8 (thiện tâm) và AN 3.136 (tình bạn). Catalog dùng diễn giải ngắn tiếng Việt do dự án viết, không sao chép dài bản dịch. Mỗi thẻ có lưu ý tránh hiểu sai. Nếu câu hỏi liên quan truyền thống khác hoặc vượt phạm vi chín nguồn này, model phải nói có giới hạn; ứng dụng không cho nhận một ID nguồn không có trong lượt truy xuất.

Những ca khủng hoảng được đáp bằng lời hỗ trợ thực tế và không gán nỗi đau cho nghiệp. Câu ngoài chủ đề vẫn có thể được trả lời ngắn nếu hữu ích; không ép mọi chuyện thành giáo lý. Nội dung riêng tư, chẩn đoán y tế, pháp lý hay tài chính không được trình bày như lời chuyên môn.

## Chất lượng và giới hạn đã biết

Bộ test trong repo kiểm tra ledger, chọn/hoãn, nguồn, mẫu khủng hoảng, lời bị giữ lại và xác nhận media. Đã có [phép thử với API Luna thật](LLM_QUALITY_EVAL_2026-10-30.md) qua vòng lặp điều phối và RTSP nội bộ không TTS. Mẫu thử còn nhỏ, chưa đo độ tự nhiên của giọng, đồng bộ môi hoặc độ trễ trên kênh phát thật. Khi chạy thật, ghi nhận các ca vô danh hóa để mở rộng test, đo P95 độ trễ, số lời bị giữ, `uncertain` và chi phí theo ngày. Việc này dùng để cải thiện chính sách và prompt tự động, không chèn bước duyệt thủ công vào mỗi lời.
