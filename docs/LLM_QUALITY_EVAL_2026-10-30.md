# Kiểm thử lời giảng và trả lời bình luận · 2026-10-30

## Phạm vi

Đã gọi API thật với [`gpt-6-luna`](https://developers.openai.com/api/docs/models/gpt-6-luna) qua Responses API, JSON schema và moderation như mã ứng dụng đang dùng. Key nằm trong `.env` cục bộ, file được Git bỏ qua và có quyền `0600`; tài liệu này không chứa key.

Đã chạy `LiveReplyCoordinator.run()` và các lượt điều phối với bình luận giả lập, nguồn Phật học thật trong catalog và SQLite thật. Kiểm tra tới `speech_jobs` ngay trước bước TTS. Video avatar có sẵn đã qua bước chuẩn bị clip chờ bằng FFmpeg. Một MediaMTX tạm trên máy nhận luồng RTSP từ `SegmentStreamer`; `ffprobe` đọc được hình H.264 320×180 và âm thanh im lặng Opus. **Không tạo giọng, dựng clip môi hoặc phát lên Facebook/TikTok**, nên số giây dưới đây là thời gian tạo và xếp lời, chưa phải độ trễ người xem nghe được.

## Kết quả

| Tình huống | Kết quả quan sát | Đánh giá nội dung |
| --- | --- | --- |
| Không có bình luận | Vòng lặp 11 giây xếp 1 lời mở đầu và 3 lời giảng nền; lời mở đầu chỉ xuất hiện một lần. Lời nền không chào lại. | Dễ hiểu, có ví dụ đi bộ, rửa bát, bạn bè và bước thực tập ngắn. Có một cặp lời gần ý nhau khi phép thử cố tình bắt đầu với danh sách chỉ một chủ đề. |
| Câu hỏi về cáu giận trong gia đình | Xếp lời đáp sau 3,7 giây, kèm nguồn `mn10-mindfulness`. | Trả lời ngay việc cần làm lúc về nhà; không giảng thuật ngữ dài. |
| Nhiều bình luận cùng lúc | Một lời giảng nền và hai lượt trả lời được xếp trong 14,2 giây. Câu khác ý chuyển sang `deferred`; lượt kế tiếp trả lời câu mất ngủ. Job của người xem được chọn trước job nền còn chờ. | Hai câu trả lời đúng trọng tâm, không chèn bán hàng. Một câu cùng ý vẫn được hoãn để xét tiếp; không bị bỏ mất. |
| Mất mẹ và tự trách vì nghiệp | Xếp lời đáp sau 4,1 giây, nguồn `an6.63-intention`. | Thừa nhận nỗi đau, bác bỏ việc quy mất mát thành lỗi nghiệp của người hỏi, gợi tìm người thân hỗ trợ. |
| Vô ngã và ranh giới | Xếp lời đáp sau 11,2 giây, nguồn `sn22.59-not-self`. | Giải thích vô ngã không phải “không tồn tại” hay phải chịu đối xử tệ, gợi đặt ranh giới. |
| Hỏi vòng có đổi vận, chữa bệnh không | Xếp lời đáp sau 4,5 giây. | Bác bỏ phép đổi vận, chữa bệnh; không thay thế chăm sóc y tế. |
| Chạy vòng lặp thật, có một bình luận mới | Trong 11 giây, có lời mở đầu, một lời nền và một câu trả lời ở SQLite; bình luận chuyển sang `queued`. | Nội dung ngắn, có thể đưa sang TTS. Phép thử cố ý không gọi TTS và dựng video. |
| Luồng RTSP nội bộ không TTS | `SegmentStreamer` phát clip chờ qua MediaMTX tạm; `ffprobe` xác nhận một track H.264 và một track Opus. | Xác nhận đường phát hình và âm thanh im lặng chạy được khi chưa có lời mới. |

## Kiểm tra mạch kể sau khi sửa

Phép thử ban đầu ở hàng “Không có bình luận” cho thấy từng lời riêng lẻ dễ hiểu nhưng các chủ đề đổi quá nhanh. Sau khi đổi sang một chương năm đoạn, đã gọi Luna thật sáu lượt liên tiếp với cùng lịch sử lời nói và không tạo TTS. Năm lượt đầu giữ chủ đề cơn giận trong gia đình; lượt thứ sáu mới chuyển sang chánh ngữ. Thời gian mỗi lượt từ 2,5 đến 7 giây; cả sáu lượt đều qua kiểm tra và được trả về để xếp phát.

1. **Tình huống:** “Trong một buổi tối, sau giờ làm mệt mỏi, ta nghe người thân nói một câu tưởng như trách móc rồi giọng bỗng cao lên. Cơn giận ấy thường không đến từ một câu nói đơn lẻ; quanh nó còn có nhiều điều kiện đang góp phần.”
2. **Cách nhìn:** “Nhìn theo duyên khởi, lời nói chạm vào cảm giác khó chịu, rồi tâm có thể vội kết luận rằng mình bị xem thường; chính cách hiểu ấy khiến giận tăng thêm. Thấy được mắt xích này giúp ta bớt quy hết lỗi cho một người.”
3. **Điều cần hiểu rõ:** “Cũng cần nhớ, nhận ra nhiều điều kiện không có nghĩa là phủ nhận trách nhiệm hay chấp nhận lời xúc phạm. Ta có thể nhìn cơn giận cho rõ, rồi chọn nói ranh giới của mình khi đã đủ bình tĩnh.”
4. **Bước thực tập:** “Ngay lần tới khi lời nói làm mình nóng lên, quý vị thử chỉ gọi tên điều đang có: ‘Tôi đang giận, và tôi nghe câu ấy như một lời trách.’ Nhận ra cách mình diễn giải giúp ta có một khoảng dừng trước khi đáp lời.”
5. **Khép ý:** “Khoảng dừng ấy không làm điều bất công biến mất, nhưng giúp ta bớt để cơn giận quyết định thay mình. Thấy rõ các điều kiện, ta có thể giữ lòng tôn trọng mà vẫn nói điều cần nói; đó cũng là bước để cùng nhìn cách nói một điều khó nghe.”
6. **Chủ đề kế:** “Chẳng hạn, trong bữa cơm gia đình, ta cần góp ý với người thân về một việc họ thường quên. Nếu mở lời bằng lời quy kết, họ dễ chỉ nghe thấy sự trách móc, còn điều cần thay đổi lại bị bỏ qua.”

Một lượt Luna thật khác được thử với câu trả lời bình luận chen giữa đoạn 2 và đoạn 3; lời dẫn tiếp tục nói về cơn giận và trách nhiệm, không đổi chủ đề. Đây là đánh giá bằng mẫu nhỏ, chưa chứng minh mọi lượt sinh lời đều mượt.

Các lời giảng về duyên khởi và chánh niệm là **ứng dụng đời sống** từ [SN 12:2](https://www.dhammatalks.org/suttas/SN/SN12_2.html) và [MN 10](https://www.dhammatalks.org/suttas/MN/MN10.html), không phải trích nguyên văn kinh. Cách giải thích nghiệp và vô ngã phù hợp với thẻ nguồn dựa trên [AN 6:63](https://www.dhammatalks.org/suttas/AN/AN6_63.html) và [SN 22:59](https://www.dhammatalks.org/suttas/SN/SN22_59.html).

## Lỗi tìm thấy và đã sửa

Lượt thử đầu có 5/7 quyết định bị giữ lại dù nội dung không bị moderation đánh dấu: bản nháp dài 320–420 ký tự, vượt giới hạn 260. Prompt hiện yêu cầu khoảng 210 ký tự và lượt sửa ghi rõ độ dài cần rút. Sau sửa, các tình huống trên đã xếp được lời phát.

Lượt chọn nhiều bình luận từng ghi một câu hỏi mất ngủ hợp lệ thành `ignored`. Mã điều phối hiện đưa câu khác ý vào `deferred`, và chỉ bỏ qua spam/lời chào thật. Mã lý do do Luna trả về cũng được chuẩn hóa để không lưu câu văn dài vào trạng thái.

## Giới hạn kết luận

Đây là bộ mẫu nhỏ với bình luận giả lập. Chất lượng câu chữ có thể thay đổi giữa các lượt Luna; chưa kiểm tra độ trễ, độ tự nhiên của giọng TTS, đồng bộ môi avatar hoặc tín hiệu Facebook/TikTok thật. Đã chạy 69 unit test, 1 test được bỏ qua, và phép thử kết nối Luna trả đúng JSON schema cùng moderation.
