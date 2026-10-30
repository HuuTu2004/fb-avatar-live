# Refactor người dẫn Phật pháp đời thường

Cập nhật 2026-10-30. Đây là kế hoạch kiêm đối chiếu triển khai. Thiết kế chi tiết nằm trong [LLM_ARCHITECTURE.md](LLM_ARCHITECTURE.md), nghiên cứu API trong [openai-live-comment-responder-research.md](openai-live-comment-responder-research.md).

## Mục tiêu

Người dẫn nói như đang trò chuyện: hiểu đúng nỗi băn khoăn, giải thích một ý bằng ví dụ gia đình/công việc/sinh hoạt, rồi gợi một việc nhỏ có thể thử. Câu hỏi thực sự hữu ích được ưu tiên; các câu khác được hoãn chứ không bị ghi là đã đáp. Không bịa lời kinh, không gán khổ đau của một người cho nghiệp, không đọc thông tin riêng tư. Mọi lời nói đều được tạo và kiểm tra tự động; không có hàng chờ duyệt của con người. Nút tạm dừng chỉ là quyền điều khiển vận hành khi muốn ngưng tiếng AI.

## Luồng đã triển khai

```text
Facebook Page / Facebook cá nhân / TikTok / demo
 → SQLite comments: khử trùng lặp, hạn chờ, lease
 → lọc spam và tín hiệu nguy cơ
 → Luna chọn một câu và gom câu thật sự cùng ý
 → tra catalog tóm tắt kinh đã đối chiếu nguồn
 → Luna soạn 1–2 câu nói bằng Responses + Structured Outputs
 → kiểm tra ID/nguồn/độ dài/nội dung/moderation
 → SQLite replies + speech_jobs trong một transaction
 → ZeroTTS → MuseTalk → streamer
 → cả audio và video cấp xong cho encoder: fed_to_encoder
```

`LiveReplyEngine` dùng `gpt-6-luna` cho tất cả lời gọi tạo nội dung. Chọn câu dùng `reasoning.effort=none`, soạn lời dùng `low`. `omni-moderation-latest` là bộ phân loại an toàn, không phải LLM viết lời. Source catalog có chín thẻ tóm tắt tự viết từ các bài kinh có URL gốc. Cấu hình giọng trên dashboard là dữ liệu gửi cho model; chính sách ứng dụng nằm trong code.

Bình luận nhạy cảm rõ ràng nhận lời hỗ trợ an toàn có sẵn. Nếu moderation chặn loại khác, bản nháp không vượt kiểm tra sau một lần sửa, hoặc câu hỏi giáo lý thiếu nguồn, hệ thống tự giữ lại câu trả lời. Không cần người duyệt. API lỗi thì lease được trả về hàng đợi để thử lại có giới hạn. Chưa có key thì không tạo lời đáp LLM cho bình luận thật; chế độ nói liên tục có thể dùng lời nền có sẵn.

Trạng thái `queued` chỉ nghĩa là đã tạo job. `fed_to_encoder` cần cả hai track báo thành công và publisher còn chạy; nó không khẳng định nền tảng live hay mọi người xem đã nhận được. Nếu feeder lỗi hoặc tiến trình dừng đúng lúc phát, trạng thái `uncertain` và không tự phát lại để tránh lặp lời. Comment cũ có `processed` được chuyển thành `legacy_processed` vì không có bằng chứng phát.

Chế độ chia sẻ liên tục có tác vụ chuẩn bị lời dẫn riêng, giữ khoảng bốn job nền trong pipeline và một kịch bản địa phương làm clip đầu hoặc dự phòng khi Luna quá 8 giây/lỗi. Nhận bình luận không xóa ngay lời nền đã chuẩn bị; chỉ khi video câu trả lời đã render xong mới hủy job nền chưa phát, và clip đang phát vẫn làm cầu nối. Lời mới của Luna được viết thành một clip trọn ý. Bộ đệm streamer có hạn chờ một giây khi chỉ có một clip, tránh chờ vô hạn.

## Đối chiếu các giai đoạn

| Giai đoạn | Kết quả trong repo | Kiểm chứng |
| --- | --- | --- |
| Trạng thái bình luận và outbox | `live_reply/state_store.py`, migration SQLite, lease, retry, TTL, many-to-many, job theo thứ tự | Test SQLite tạm: chọn/hoãn, restart, xác nhận phát, lỗi feeder |
| Chọn và nhóm bình luận | `live_reply/engine.py`, schema chọn nghiêm ngặt, kiểm tra ID, spam và câu nguy cơ | Test nhiều câu, nguồn thật, mẫu hỗ trợ nguy cơ |
| Nguồn và lời đáp | `live_reply/dharma_cards.json`, tìm kiếm từ khóa tiếng Việt, Responses API, JSON Schema, moderation hai chiều | Test engine với adapter giả; không có key để kiểm thử tài khoản thật |
| Media và vận hành | `live_reply/coordinator.py`, `streamer.py`, dashboard trạng thái, nguồn và nút tạm dừng | Test xác nhận cả hai track; chưa chạy GPU/livestream thật trong máy phát triển |
| Tài liệu và đo lường | README, thiết kế, test hồi quy trong repo | Bộ đánh giá chất lượng tiếng Việt trên dữ liệu live thật cần thu thập khi vận hành |

## Việc cần đo trên hệ thống thật

Các phép đo cần OpenAI key, ZeroTTS/MuseTalk đã cài và luồng thực: độ trễ P95 từ lúc nhận bình luận đến tiếng đầu, tỷ lệ chọn đúng câu, tỷ lệ lời đáp dễ hiểu/đúng ý, số lượt bị giữ lại, số job `uncertain`, token và chi phí. Cần dùng bình luận thực tế đã loại thông tin riêng tư để mở rộng bộ ca tiếng Việt. Đây là đo chất lượng để điều chỉnh prompt và catalog, không phải bước duyệt từng lời trước khi phát.
