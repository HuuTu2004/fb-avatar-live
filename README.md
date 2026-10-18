# Facebook + TikTok Live AI Avatar

Pipeline chạy liên tục trên một máy Vast.ai:

```text
Facebook Graph API ─┐
TikTok LIVE Webcast ─┴─> SQLite dedupe/batch -> hosted LLM
                       -> short speech chunks -> ZeroTTS (CPU)
                       -> persistent MuseTalk 1.5 (GPU)
                       -> FFmpeg H.264/Opus -> MediaMTX
                          ├─ WebRTC/WHEP preview in control panel
                          ├─ H.264/AAC -> Facebook RTMPS
                          └─ H.264/AAC -> TikTok RTMP
```

MuseTalk và ZeroTTS chạy ở hai môi trường Python riêng. MuseTalk giữ model trong VRAM và nhận từng đoạn audio qua HTTP nội bộ; ZeroTTS, LLM và bộ điều phối chạy trong tiến trình còn lại. FFmpeg luôn phát một idle clip ngắn khi chưa có câu trả lời. MediaMTX cấp preview WebRTC độ trễ thấp và forwarder đổi Opus sang AAC để gửi cùng video H.264 lên Facebook.

Control panel có bốn khu vực hoạt động trực tiếp với backend:

1. Dashboard xem WebRTC, trạng thái service, start/stop và đưa câu thử vào hàng đợi.
2. Groq API kiểm tra key, lấy model hiện có và lưu cấu hình.
3. Clone voice cài voice pack ZeroTTS, chọn giọng và tổng hợp bản nghe thử.
4. Avatar & Live tải video, chuẩn hóa 720p/25 FPS, chọn nguồn comment và lưu đích Facebook/TikTok.

## Yêu cầu

- Linux, NVIDIA GPU, FFmpeg và `uv`.
- Video avatar có mặt nhìn rõ. Một loop idle khoảng 1 giây, đầu/cuối khớp nhau sẽ chuyển cảnh mượt nhất.
- Facebook Page access token có quyền đọc comment của live video/post mà bạn quản lý. `FB_OBJECT_ID` là ID của live video hoặc post.
- RTMPS ingest URL lấy từ Facebook Live Producer.
- TikTok `@username` đang LIVE để đọc chat và RTMP URL + stream key lấy từ TikTok LIVE Center.
- Groq API key. Backend dùng endpoint OpenAI-compatible `https://api.groq.com/openai/v1`.

Ứng dụng chỉ đọc bình luận và nói trên livestream; nó không đăng câu trả lời trở lại Facebook hoặc TikTok.

TikTok hiện không có API công khai chính thức để đọc comment LIVE. Tích hợp này dùng thư viện TikTokLive để đọc Webcast công khai như một người xem, nên có thể cần cập nhật nếu TikTok thay đổi giao thức.

## Cài trên Vast.ai

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

Giao diện sẽ hướng dẫn điền Groq, avatar và Facebook. Nếu muốn cấu hình bằng tay, các giá trị chính là:

```dotenv
FB_OBJECT_ID=...
FB_PAGE_ACCESS_TOKEN=...
TIKTOK_UNIQUE_ID=ten_tai_khoan
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=...
LLM_MODEL=...
STREAM_URL=rtsp://127.0.0.1:8554/avatar
FACEBOOK_STREAM_URL=rtmps://...private-stream-key...
TIKTOK_STREAM_URL=rtmp://...private-stream-key...
```

Để xem WebRTC trước khi live thật, chọn **Chỉ preview · dùng câu thử**, để trống cả hai stream URL, bấm **Bắt đầu preview / live**, rồi gửi câu kiểm tra trên Dashboard. Đích Facebook/TikTok là tùy chọn; forwarder chỉ chờ khi chưa có URL nên không phát ra ngoài.

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
echo '{"id":"1","author":"An","message":"Bạn giới thiệu sản phẩm này giúp mình nhé"}' >> data/demo_comments.jsonl
```

Pipeline vẫn gọi LLM, ZeroTTS, MuseTalk và stream thật. Preview nằm tại dashboard; đường WHEP nội bộ là `/preview/avatar/whep`.

## Điều chỉnh độ trễ

- `FB_BATCH_SECONDS=2-3`: nhanh hơn nhưng gọi LLM nhiều hơn.
- `FB_BATCH_SIZE=5-10`: số comment trong một lượt trả lời.
- `LLM_MAX_REPLY_CHARS=180-260`: câu ngắn giúp clip đầu xuất hiện sớm.
- `ZEROTTS_THREADS=4`: đo trên instance này nhanh hơn 8 threads (`1,2× realtime` thay vì `0,8×`).
- `ZEROTTS_WARMUP=true`: tổng hợp một câu rất ngắn khi khởi động để lượt bình luận đầu không chịu chi phí khởi tạo ONNX.
- `SPEECH_CHUNK_CHARS=45`: giữ mỗi đoạn nói đủ ngắn để câu đầu lên hình sớm; tăng nếu giọng bị chia vụn.
- `MUSETALK_BATCH_SIZE=20`: tăng đến khi GPU gần đầy. RTX 3090 24 GB thường đủ dư cho inference FP16.
- `MUSETALK_XFORMERS=true`: dùng attention tối ưu; script bootstrap cài bản khớp PyTorch 2.0.1.
- `MUSETALK_WARMUP=true`: chạy một giây audio im lặng lúc khởi động để lượt nói đầu không chịu chi phí warm-up CUDA.
- `IDLE_CHUNK_SECONDS=1`: giới hạn thời gian chờ trước khi chen clip nói vào stream.

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

Tham khảo: [MuseTalk chính thức](https://github.com/TMElyralab/MuseTalk), [ZeroTTS chính thức](https://github.com/zeroweight-ai/ZeroTTS), [Groq API](https://console.groq.com/docs/api-reference), [MediaMTX WebRTC](https://mediamtx.org/docs/read/webrtc), và [Meta Graph API object comments](https://developers.facebook.com/docs/graph-api/reference/object/comments/).
