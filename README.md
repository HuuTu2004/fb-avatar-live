# Facebook + TikTok Live AI Avatar

Pipeline chạy liên tục trên một máy Vast.ai:

```text
Facebook Graph API ─┐
TikTok LIVE Webcast ─┴─> SQLite ledger -> gpt-6-luna (OpenAI Responses)
                       -> source catalog + automatic safety gate
                       -> speech outbox -> ZeroTTS (CPU)
                       -> persistent MuseTalk 1.5 (GPU)
                       -> FFmpeg H.264/Opus -> MediaMTX
                          ├─ WebRTC/WHEP endpoint (optional)
                          ├─ H.264/AAC -> Facebook RTMPS
                          └─ H.264/AAC -> TikTok LIVE Studio trên Windows (camera + micro ảo)
```

MuseTalk và ZeroTTS chạy ở hai môi trường Python riêng. MuseTalk giữ model trong VRAM và nhận từng đoạn audio qua HTTP nội bộ; ZeroTTS, LLM và bộ điều phối chạy trong tiến trình còn lại. FFmpeg luôn phát một idle clip ngắn khi chưa có câu trả lời. MediaMTX cấp preview WebRTC độ trễ thấp và forwarder đổi Opus sang AAC để gửi cùng video H.264 lên Facebook.

Control panel có bốn khu vực hoạt động trực tiếp với backend:

1. Dashboard xem WebRTC, trạng thái service, start/stop và đưa câu thử vào hàng đợi.
2. OpenAI API kiểm tra key và một lượt Luna trả JSON đúng schema trước khi lưu cấu hình.
3. Clone voice cài voice pack ZeroTTS, chọn giọng và tổng hợp bản nghe thử.
4. Avatar & Live tải video, chuẩn hóa 720p/25 FPS, chọn nguồn comment và lưu đích Facebook/TikTok.

## Yêu cầu

- Linux, NVIDIA GPU, FFmpeg và `uv`.
- Video avatar có mặt nhìn rõ. Đầu/cuối khớp nhau sẽ chuyển cảnh mượt nhất.
- Facebook Page access token có quyền đọc comment của live video/post mà bạn quản lý. `FB_OBJECT_ID` là ID của live video hoặc post.
- RTMPS ingest URL lấy từ Facebook Live Producer.
- TikTok `@username` đang LIVE để đọc chat; hình và tiếng đưa vào TikTok LIVE Studio qua camera và micro ảo trên Windows.
- OpenAI API key. Backend dùng endpoint `https://api.openai.com/v1`.

Ứng dụng chỉ đọc bình luận và nói trên livestream; nó không đăng câu trả lời trở lại Facebook hoặc TikTok.

TikTok hiện không có API công khai chính thức để đọc comment LIVE. Tích hợp này dùng thư viện TikTokLive để đọc Webcast công khai như một người xem, nên có thể cần cập nhật nếu TikTok thay đổi giao thức.

## Cài trên Vast.ai

Hướng dẫn dựng lại đúng cấu hình preview đang chạy: [VAST_SETUP.md](VAST_SETUP.md).

Đưa hình/tiếng Vast vào TikTok LIVE Studio như camera và micro ảo trên Windows: [LIVE_STUDIO_CAMERA.md](LIVE_STUDIO_CAMERA.md).

Repo nên nằm tại `/workspace/fb-avatar-live`:

```bash
cd /workspace/fb-avatar-live
chmod +x scripts/*.sh
./scripts/bootstrap_vast.sh
./scripts/install_supervisor.sh
```

`install_supervisor.sh` tạo `.env` nếu cần, cài năm service và đăng ký **Avatar Live Studio** vào Vast Instance Portal. Mở app đó từ portal, hoặc dùng SSH tunnel riêng:

```bash
ssh -p 21929 root@220.86.159.73 \
  -L 8088:127.0.0.1:18080 \
  -L 8189:127.0.0.1:8189
# mở http://localhost:8088
```

Giao diện sẽ hướng dẫn điền OpenAI, avatar và Facebook. Nếu muốn cấu hình bằng tay, các giá trị chính là:

```dotenv
FB_OBJECT_ID=...
FB_PAGE_ACCESS_TOKEN=...
TIKTOK_UNIQUE_ID=ten_tai_khoan
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=gpt-6-luna
STREAM_URL=rtsp://127.0.0.1:8554/avatar
FACEBOOK_STREAM_URL=rtmps://...private-stream-key...
```

Để chuẩn bị luồng trước khi live thật, chọn nguồn bình luận, bấm **Khởi động luồng**, rồi gửi câu kiểm tra trên Dashboard. Dashboard chỉ điều khiển và hiển thị trạng thái, không nhúng player WebRTC; xem hình/tiếng qua cửa sổ stream RTMP hoặc kênh phát. Nút này chưa gửi tín hiệu ra Facebook/TikTok.

Để phát trên Facebook cá nhân, mở [Live Producer](https://www.facebook.com/live/producer/), chọn trang cá nhân và **Phần mềm phát trực tiếp**. Trong mục Avatar & Live, nhập riêng **URL máy chủ** và **khóa luồng**, bấm **Lưu cấu hình Live**, khởi động luồng và chờ trạng thái **Có tín hiệu** rồi bấm **Bắt đầu gửi lên Facebook**. Kiểm tra hình/tiếng trong Live Producer và bấm **Phát trực tiếp** tại Facebook. **Dừng gửi Facebook** chỉ tắt đường phát ra Facebook; luồng nội bộ vẫn chạy.

Để đọc bình luận live **trang cá nhân**, cài [Social Stream Ninja](https://github.com/steveseguin/social_stream) trên Chrome. Đăng nhập Facebook và mở chính video live ở chế độ người xem, bật thu thập chat trong SSN. Trong SSN bật **Enable remote API control of extension** và **Send chat messages to API server**, rồi lấy Session ID từ URL dock của SSN. Trong Avatar & Live chọn **Facebook cá nhân · Social Stream Ninja**, dán Session ID, bật **Chia sẻ liên tục**, lưu cấu hình. Cần OpenAI API key để trả lời bình luận thật. SSN gửi bình luận qua máy chủ WebSocket của họ tới pipeline; giữ tab Facebook mở trong lúc live. Page token chỉ dành cho nguồn Facebook Page.

Để kiểm tra avatar nói liên tục, bật **Chia sẻ liên tục** trong mục Avatar & Live. Khi chưa có OpenAI key, pipeline dùng vòng kịch bản Phật pháp nội bộ; sau khi lưu key, lời dẫn mới dùng Luna và thẻ nguồn trong `src/fb_avatar_live/live_reply/dharma_cards.json`. Bình luận thật cần key để có lời đáp AI.

Khi bật **Chia sẻ liên tục**, bộ điều phối chuẩn bị sẵn tối thiểu vài đoạn lời dẫn. Một đoạn kịch bản địa phương được xếp hàng ngay lúc khởi động; Luna tạo các đoạn tiếp theo, còn kịch bản địa phương làm dự phòng nếu Luna chậm hoặc lỗi. Bình luận mới không cắt lời đang phát: lời dẫn chuẩn bị sẵn tiếp tục làm cầu nối, câu trả lời được ưu tiên khi video của nó đã render xong. Mỗi lượt AI là một clip nói trọn ý để tránh khoảng dừng giữa hai phần của cùng câu trả lời. Bộ đệm đầu chỉ chờ tối đa một giây khi đã có một clip; nó không đợi mãi clip thứ hai.

**Trạm Bình An:** Lời mở đầu ngắn, niệm danh hiệu Phật rồi đi thẳng vào chuyện đời và lời mời chia sẻ. Trên hình phát có nhãn “Avatar AI”; khi có người hỏi danh tính, hệ thống trả lời rõ và ngắn, không tự nhận là tu sĩ thật. Lời dẫn xưng “tôi”, gọi “quý vị”, giải thích giáo lý bằng ví dụ đời sống. Lời mời chia sẻ xuất hiện khoảng mỗi 3 phút và nhắc rằng khung chat là công khai.

**Giới thiệu vòng:** Trong Dashboard có công tắc xác nhận **Vòng Chú Lăng Nghiêm đã ở giỏ hàng của phiên live**. Mặc định tắt. Khi bật, hệ thống có thể nhắc sản phẩm lần đầu sau khoảng 5 phút, rồi cách tối thiểu 15 phút, chỉ trong phần lời dẫn chung và khi đã ít nhất 3 phút không có bình luận mới. Lời phát chỉ nhắc nhẹ vòng trong giỏ hàng; video hiển thị nhãn “Giỏ hàng: liên kết tiếp thị” ở góc dưới. Vòng chỉ là vật nhắc chánh niệm, không được hứa hộ thân, đổi vận hay chữa bệnh. Điều kiện đổi trả cần xem trên **trang sản phẩm cụ thể**; không có lời cam kết hoàn trả chung cho mọi gian hàng. Mọi lời gọi LLM vẫn dùng Luna; lời mở đầu, lời mời và dự phòng là kịch bản cố định.

Xem bản lời và quy tắc đầy đủ tại [TRAM_BINH_AN_PROMPT.md](docs/TRAM_BINH_AN_PROMPT.md), cùng [kết quả thử Luna qua luồng điều phối](docs/LLM_QUALITY_EVAL_2026-10-30.md). Nếu nền tảng live có công cụ công bố nội dung thương mại, hãy bật công bố tương ứng bên cạnh nhãn trên video.

Trong **Dashboard → Nội dung AI**, chỉnh giọng trò chuyện, chủ đề (mỗi dòng một chủ đề) và cách gọi chung khi không đọc được tên người xem. Bấm **Lưu nội dung AI** để áp dụng từ lượt AI tạo tiếp theo. Các trường này là dữ liệu phong cách; quy tắc an toàn và phạm vi Phật học vẫn nằm trong code. Nút **Tạm dừng tiếng AI** ngưng tạo và đưa lời mới vào hàng phát, còn video idle tiếp tục chạy.

Với bình luận, AI chọn một câu đáng trả lời, gom những câu thật sự cùng ý và hoãn các câu khác. Luna tra các thẻ nguồn rồi soạn 1–2 đoạn nói trọn câu; ứng dụng kiểm tra ID, nguồn, độ dài và moderation trước khi phát. Bình luận tự hại hoặc đe dọa rõ ràng nhận mẫu hỗ trợ an toàn. Bản nháp không đạt được tự giữ lại, không có bước người duyệt. Dashboard hiển thị câu đã chọn, trạng thái lời phát và URL nguồn. Tên được chuẩn hóa; tên khó đọc dùng “bạn”. Cấu hình tùy chỉnh lưu trong `LLM_REPLY_SETTINGS` của `.env`.

Không commit `.env`; token Facebook, API key LLM và stream key đều là bí mật.

Các service được Supervisor quản lý:

```bash
supervisorctl status \
  fb-avatar-control fb-avatar-mediamtx fb-avatar-musetalk \
  fb-avatar-pipeline fb-avatar-forwarder
```

Control panel được bảo vệ bởi lớp token của Vast/Caddy. MediaMTX chỉ bind HTTP/RTSP vào localhost; WebRTC dùng STUN để thiết lập media path. Mạng chặn UDP hoặc symmetric NAT nên cấu hình thêm TURN trong `config/mediamtx.yml`.

## Clone voice

Bản ZeroTTS công khai có thể **nạp và chạy** giọng tùy chỉnh nhưng không phát hành voice encoder, vì vậy máy chủ không thể tự biến một WAV thành latent. Tạo giọng từ audio mẫu tại [ZeroWeight Audio](https://platform.zeroweight.ai/audio), tải nguyên voice pack ZIP, rồi thả ZIP vào mục **Clone voice**. Backend kiểm tra `voice.npz`, cài pack vào `data/voices`, cập nhật `.env`, restart pipeline và cho nghe thử ngay.

## Kiểm tra không cần Facebook

Đổi `.env`:

```dotenv
COMMENT_SOURCE=demo
DEMO_COMMENTS_FILE=/workspace/fb-avatar-live/data/demo_comments.jsonl
```

Sau khi hai service chạy, thêm comment giả:

```bash
mkdir -p data
echo '{"id":"1","author":"An","message":"Vô thường là gì?"}' >> data/demo_comments.jsonl
```

Pipeline vẫn gọi LLM, ZeroTTS, MuseTalk và stream thật. Dashboard không nhúng player; endpoint WHEP nội bộ `/preview/avatar/whep` vẫn được giữ cho các công cụ cần dùng.

## Điều chỉnh độ trễ

- `FB_BATCH_SECONDS=2-3`: nhanh hơn nhưng gọi LLM nhiều hơn.
- `FB_BATCH_SIZE=8`: tối đa 8 bình luận trong một lượt gọi AI, dùng cả khi bật chia sẻ Phật pháp liên tục. Cứ mỗi `FB_BATCH_SECONDS` (mặc định 3 giây), lấy tối đa số này từ hàng đợi; ít hơn vẫn trả lời, phần còn lại chờ batch kế tiếp.
- `LLM_MAX_REPLY_CHARS=180-260`: câu ngắn giúp clip đầu xuất hiện sớm.
- `ZEROTTS_PROVIDER=cuda`: trên instance RTX 3090, cài riêng ONNX Runtime GPU bằng `uv pip install --python .venv/bin/python --target .venv/ortgpu --no-deps onnxruntime-gpu==1.30.0`, rồi khởi động lại pipeline. Script chạy tự thêm thư mục này vào Python path. Nếu không có GPU runtime tương thích, giữ `ZEROTTS_PROVIDER=cpu`.
- `ZEROTTS_THREADS=2`: số luồng ONNX đang dùng với CUDA; với CPU có thể dùng 4.
- `ZEROTTS_WARMUP=true`: tổng hợp một câu rất ngắn khi khởi động để lượt bình luận đầu không chịu chi phí khởi tạo ONNX.
- `SPEECH_CHUNK_CHARS=260`: giữ trọn một lượt trả lời trong cùng ngữ cảnh audio của MuseTalk, tránh đoạn đuôi ngắn và điểm nối giữa câu.
- Khi bật chia sẻ Phật pháp liên tục và có OpenAI key, Luna giữ một chủ đề qua khoảng năm đoạn nối tiếp: tình huống đời thường, cách nhìn Phật học, một góc cần hiểu rõ, bước thực tập và câu khép ý. Sau đó mới chuyển sang chủ đề kế tiếp. Lời mời chia sẻ và câu trả lời bình luận có thể xen vào; đoạn giảng tiếp theo nối lại ý trước khi bị ngắt. Bình luận được xét theo batch, nhưng chỉ câu đã chọn hoặc thật sự cùng ý mới gắn với lời đáp; các câu khác được hoãn tới lượt sau. Luna dùng `none` để chọn và `low` để viết. Khi API lỗi, bình luận được thử lại có giới hạn; khi lời không đạt kiểm tra, câu được giữ lại và không phát. `DEMO_SALES_MODE` giữ tên cũ để tương thích cấu hình.
- `STREAM_PREBUFFER_SEGMENTS=2`: giữ hai lượt đầy đủ để che dao động thời gian của ZeroTTS/MuseTalk; nếu chỉ có một clip, bộ đệm đầu cho phát sau tối đa một giây.
- `MUSETALK_BATCH_SIZE=20`: tăng đến khi GPU gần đầy. RTX 3090 24 GB thường đủ dư cho inference FP16.
- `MUSETALK_XFORMERS=true`: dùng attention tối ưu; script bootstrap cài bản khớp PyTorch 2.0.1.
- `MUSETALK_WARMUP=true`: chạy một giây audio im lặng lúc khởi động để lượt nói đầu không chịu chi phí warm-up CUDA.
- `MUSETALK_BBOX_SMOOTH_WINDOW=7`: lọc dao động của face detector để vùng miệng không co giãn hoặc rung giữa các frame.
- `IDLE_CHUNK_SECONDS=0.2`: giới hạn độ lệch khi video và audio cùng chuyển từ idle sang clip nói.
- `AVATAR_IDLE_FRAME_SECONDS=4`: tùy chọn lấy một frame miệng khép từ video gốc để hiển thị khi chờ câu nói; tránh cảnh miệng cử động trong lúc âm thanh im lặng. Bỏ trống để giữ cách phát video idle cũ.
- `BACKGROUND_MUSIC_FILE=assets/meditation-ambient.ogg` và `BACKGROUND_MUSIC_VOLUME=0.10`: lặp nhạc nền nhẹ liên tục dưới giọng nói trong preview. Để trống đường dẫn để tắt nhạc; chỉnh âm lượng từ 0 đến 1.

Nhạc nền mặc định: [“Promise” của Keith Adler](https://commons.wikimedia.org/wiki/File:Promise.ogg), tác giả đã phát hành vào phạm vi công cộng (public domain). Bản trong `assets/` đã cắt đoạn im lặng và nối chéo đầu/cuối để lặp êm hơn.

Các đoạn trả lời được chia theo câu và chạy theo dây chuyền CPU/GPU, nên ZeroTTS có thể chuẩn bị đoạn kế tiếp trong lúc MuseTalk render đoạn hiện tại. Nếu lượng comment lớn hơn tốc độ phát lời, hàng đợi tạo backpressure thay vì mở thêm model hoặc làm tràn VRAM.

## Kết quả đo trên instance hiện tại

Instance Vast.ai dùng RTX 3090 24 GB, 12 vCPU và 62 GB RAM. Với avatar mẫu 1280×720 ở 25 FPS:

- MuseTalk 1.5 sau warm-up render 198 khung trong 4,93 giây, tương đương 40,2 FPS. Tốc độ này cao hơn nhịp phát 25 FPS nên đủ duy trì realtime với một avatar.
- ZeroTTS với 4 CPU threads đạt khoảng 1,2× realtime trong phép đo lặp; 8 threads chậm hơn trên máy này.
- Kiểm thử toàn tuyến đã tạo một FLV dài 31,54 giây với 789 khung hình, H.264 25 FPS và AAC 48 kHz stereo. FFmpeg giải mã lại không có lỗi; hai đoạn tiếng nói được giữ nguyên giữa các đoạn idle.
- Với batch 2 giây, LLM giả lập chạy ngay tại máy và đoạn nói tối đa 45 ký tự, âm thanh đầu tiên bắt đầu khoảng 14 giây sau khi nhận comment. Endpoint LLM thật sẽ cộng thêm độ trễ mạng/model. Đặt 30–40 ký tự có thể giảm thêm vài giây nhưng giọng dễ bị chia vụn hơn.
- Kiểm thử WebRTC/MediaMTX nhận đúng H.264 1280×720/25 FPS và Opus 48 kHz stereo. Bản ghi RTSP 25,02 giây có 578 video packet và 1.250 audio packet, giải mã lại không lỗi. Forwarder tạo FLV 20 giây với H.264 + AAC cho Facebook.
- Sau khi cả ZeroTTS và MuseTalk đã warm, lượt tích hợp gần nhất đưa clip đầu vào stream khoảng 8,4 giây sau khi nhận comment.

Vì pipeline render nhanh hơn nhịp phát, livestream vẫn chạy mượt và liên tục khi tải trung bình không vượt quá một luồng lời nói. Khi comment đến nhanh hơn khả năng đọc, hệ thống gộp theo batch và giữ hàng đợi thay vì cố đọc mọi comment.

## Giới hạn hiện tại

- Polling chỉ đọc tối đa `FB_FETCH_LIMIT` comment mới nhất mỗi nhịp. Livestream có hơn 100 comment trong khoảng poll nên chuyển sang Page Webhooks để không bỏ sót.
- Chuyển từ idle clip sang clip nói phụ thuộc vào loop avatar. Dùng một loop ngắn, ổn định và cùng khung hình đầu/cuối để tránh giật hình.
- WebRTC dùng STUN mặc định. Mạng doanh nghiệp chặn UDP hoặc NAT khó nên cấp một TURN server để preview hoạt động ổn định từ mọi nơi.
- `/workspace` của instance hiện tại không gắn volume. Stop/start giữ dữ liệu, nhưng recycle hoặc destroy sẽ xóa repo, model và cache; đồng bộ repo/assets ra nơi khác trước khi làm hai thao tác đó.

## Kiểm tra mã

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Tham khảo: [MuseTalk chính thức](https://github.com/TMElyralab/MuseTalk), [ZeroTTS chính thức](https://github.com/zeroweight-ai/ZeroTTS), [OpenAI API](https://platform.openai.com/docs/api-reference), [MediaMTX WebRTC](https://mediamtx.org/docs/read/webrtc), và [Meta Graph API object comments](https://developers.facebook.com/docs/graph-api/reference/object/comments/).
