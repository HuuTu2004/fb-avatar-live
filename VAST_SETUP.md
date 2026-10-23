# Dựng lại preview Vast.ai như cấu hình hiện tại

Ghi nhận ngày **18/10/2026** trên instance đang dùng. Đây là cấu hình để xem avatar nói liên tục trên web. Khóa OpenAI và khóa phát Facebook **không** nằm trong tài liệu. IP và cổng Vast.ai sẽ đổi khi tạo instance mới.

## 1. Cấu hình đã kiểm tra

| Thành phần | Trạng thái hiện tại |
| --- | --- |
| Image | Vast.ai **PyTorch** base image, CUDA hệ thống 13.0, NVIDIA driver 580.159.03 |
| GPU | RTX 3090 24 GiB; lúc đo dùng khoảng 12.5 GiB VRAM |
| CPU / RAM / ổ đĩa | 12 vCPU, 62 GiB RAM, ổ 100 GiB (đang dùng khoảng 21 GiB) |
| Mã hóa / preview | FFmpeg 6.1.1, MediaMTX 1.21.1, H.264 1280×720 **25 FPS** + Opus 48 kHz qua RTSP → WebRTC |
| MuseTalk | MuseTalk 1.5, Python 3.10.22, PyTorch 2.0.1+cu118, xformers 0.0.20; một worker GPU giữ model trong VRAM |
| ZeroTTS | `zerotts==0.1.5`, Python 3.11.17, ONNX Runtime GPU 1.30.0; giọng có sẵn `huuduc`, CUDA, 2 threads |
| LLM | OpenAI endpoint, model đang chọn `gpt-6-luna`; API key nhập riêng trong giao diện hoặc `.env` |
| Nội dung | `COMMENT_SOURCE=demo`, chia sẻ Phật pháp liên tục, ưu tiên bình luận thử trong hàng đợi |
| Phát ra ngoài | `fb-avatar-forwarder` có `autostart=false`; trạng thái thực tế cần xem bằng `supervisorctl status` |

Code chạy tại `/workspace/fb-avatar-live`. MuseTalk ở `/workspace/MuseTalk` (commit `0a89dec45a0192b824e3cf4daf96c239440c5ed8`), ZeroTTS ở `/workspace/ZeroTTS` (commit `319c3e07c8575c1e38617f07c773ec76a780a1f1`). Model lần lượt ở `/workspace/MuseTalk/models` (~5.5 GiB) và `/workspace/models/zerotts` (~869 MiB). Cache avatar MuseTalk ở `/workspace/MuseTalk/results/v15/avatars` (~3.1 GiB) sẽ được tạo lại lúc chạy đầu tiên.

**Quan trọng về lưu trữ:** `workspace_is_volume=false` trên instance này. Stop/start vẫn giữ dữ liệu; **recycle/destroy sẽ xóa** `/workspace`, model, `.env`, avatar và cache. Bản avatar đã sao lưu trong repo ở `assets/avatar.mp4`; nhạc ở `assets/meditation-ambient.ogg`. Giữ một bản repo ở máy riêng hoặc Git trước khi hủy instance. Sao lưu `.env` vào nơi bí mật riêng, đừng commit API key/stream key.

## 2. Dựng instance mới

Chọn máy có **RTX 3090 24 GiB hoặc GPU tương đương**, khoảng **12 vCPU, 62 GiB RAM và 100 GiB ổ đĩa** để gần với tốc độ hiện tại. Trên image PyTorch của Vast.ai, kiểm tra GPU trước:

```bash
nvidia-smi
vast-capabilities | jq '{image: .image.name, gpu: .hardware.gpu.summary, volume: .instance.workspace_is_volume}'
```

Từ PowerShell trên máy Windows, chép repo hiện tại lên instance mới. Thay `<IP>` và `<SSH_PORT>` bằng thông tin instance mới; dùng SSH key của bạn:

```powershell
scp -i $HOME\.ssh\id_ed25519_vastai -P <SSH_PORT> -r D:\Live\fb-avatar-live root@<IP>:/workspace/
```

Trên Vast.ai, cố định source MuseTalk/ZeroTTS ở đúng commit đã đo, rồi chạy script cài đặt. Script tải model nên cần mạng và vài GiB trống:

```bash
git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git /workspace/MuseTalk
git -C /workspace/MuseTalk fetch --depth 1 origin 0a89dec45a0192b824e3cf4daf96c239440c5ed8
git -C /workspace/MuseTalk checkout --detach FETCH_HEAD
git clone --depth 1 https://github.com/zeroweight-ai/ZeroTTS.git /workspace/ZeroTTS
git -C /workspace/ZeroTTS fetch --depth 1 origin 319c3e07c8575c1e38617f07c773ec76a780a1f1
git -C /workspace/ZeroTTS checkout --detach FETCH_HEAD

cd /workspace/fb-avatar-live
chmod +x scripts/*.sh
./scripts/bootstrap_vast.sh
mkdir -p /workspace/assets
cp assets/avatar.mp4 /workspace/assets/avatar.mp4
```

`bootstrap_vast.sh` nay cài thêm ONNX Runtime GPU 1.30.0 vào `.venv/ortgpu`; `run_pipeline.sh` tự nạp thư mục đó. Không cần sửa NVIDIA driver của host. Script tải các checkpoint MuseTalk/ZeroTTS từ nguồn công khai; nếu một nguồn đổi file hoặc ngừng phục vụ, xem tên checkpoint và lỗi tải trong terminal trước khi thay nguồn.

## 3. Cấu hình để giữ độ mượt hiện tại

Tạo `.env` từ mẫu, rồi **sửa giá trị sẵn có**, đừng thêm dòng trùng tên biến vì loader dùng giá trị đầu tiên:

```bash
cd /workspace/fb-avatar-live
cp .env.example .env
chmod 600 .env
nano .env
```

Giữ các giá trị sau (những mục khác trong `.env.example` có thể giữ nguyên):

```dotenv
COMMENT_SOURCE=demo
DEMO_SALES_MODE=true
FB_BATCH_SECONDS=2
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-6-luna
LLM_MAX_REPLY_CHARS=260
ZEROTTS_VOICE=huuduc
ZEROTTS_PROVIDER=cuda
ZEROTTS_THREADS=2
ZEROTTS_WARMUP=true
SPEECH_CHUNK_CHARS=260
MUSETALK_BATCH_SIZE=20
MUSETALK_XFORMERS=true
MUSETALK_WARMUP=true
MUSETALK_BBOX_SMOOTH_WINDOW=7
AVATAR_VIDEO=/workspace/assets/avatar.mp4
AVATAR_IDLE_FRAME_SECONDS=5
BACKGROUND_MUSIC_FILE=/workspace/fb-avatar-live/assets/meditation-ambient.ogg
BACKGROUND_MUSIC_VOLUME=0.10
OUTPUT_WIDTH=1280
OUTPUT_HEIGHT=720
OUTPUT_FPS=25
VIDEO_ENCODER=h264_nvenc
VIDEO_BITRATE=4500k
IDLE_CHUNK_SECONDS=0.2
STREAM_PREBUFFER_SEGMENTS=2
STREAM_URL=rtsp://127.0.0.1:8554/avatar
```

`DEMO_SALES_MODE` là tên biến cũ trong code; ở giao diện hiện nay nó bật chế độ **chia sẻ Phật pháp liên tục**. Prompt Phật pháp nằm sẵn trong `.env.example`. Nhập `LLM_API_KEY` riêng vào `.env` hoặc giao diện **OpenAI API**; chọn model thực sự có trên tài khoản nếu `gpt-6-luna` không còn khả dụng. Để trống `FACEBOOK_STREAM_URL` và Page token khi chỉ preview. Khóa luồng đã từng nhập ở instance hiện tại; không sao chép nó vào repo hay hướng dẫn.

Avatar trong repo là bản đã chuẩn hóa **8 giây, 1280×720, 25 FPS, không có tiếng**. Khuôn mặt đủ lớn để MuseTalk ghép miệng; `AVATAR_IDLE_FRAME_SECONDS=5` giữ một frame khi chờ thoại để tránh miệng video gốc tự động cử động. Nhạc nền được trộn ở mức 10% độc lập với từng câu, nên vẫn chạy qua lúc AI chuẩn bị câu tiếp theo. Nếu đổi avatar, cần hiệu chỉnh lại frame chờ và cho MuseTalk tạo lại cache; không thể kỳ vọng cùng độ khớp môi chỉ bằng thay đường dẫn file.

## 4. Khởi động và xem trên web

```bash
cd /workspace/fb-avatar-live
./scripts/install_supervisor.sh
supervisorctl status fb-avatar-control fb-avatar-mediamtx fb-avatar-musetalk fb-avatar-pipeline fb-avatar-forwarder
```

Script cài bốn service preview tự chạy; **forwarder Facebook mặc định không tự chạy**. Trong web, nút **Bắt đầu preview** không phát ra ngoài. Khi muốn live Facebook cá nhân, nhập URL máy chủ và khóa luồng trong mục Avatar & Live, lưu, chờ preview rồi bấm **Bắt đầu gửi lên Facebook**; kiểm tra và bấm **Phát trực tiếp** trong Facebook Live Producer. Trên instance hiện tại, Supervisor của forwarder Facebook đặt `autostart=false`.

Đọc bình luận live trên **trang cá nhân** dùng [Social Stream Ninja](https://github.com/steveseguin/social_stream) ở Chrome của người phát. Mở video như người xem, bật capture Facebook, bật hai tùy chọn **Enable remote API control of extension** và **Send chat messages to API server**, rồi lấy Session ID trong URL dock. Trên web chọn **Facebook cá nhân · Social Stream Ninja**, dán Session ID và lưu. `COMMENT_SOURCE=facebook_personal` và `SSN_SESSION_ID` sẽ được lưu trong `.env`; cần OpenAI key để trả lời. Relay `io.socialstream.ninja` phải truy cập được từ Vast. Giữ tab Facebook mở; nếu SSN không thu được comment thì pipeline vẫn nói nội dung nền khi bật chia sẻ liên tục. Dừng kênh gửi Facebook trước khi đổi cấu hình Live để pipeline khởi động lại an toàn.

Mở web qua SSH tunnel riêng từ PowerShell; thay IP/cổng mới:

```powershell
ssh -i $HOME\.ssh\id_ed25519_vastai -p <SSH_PORT> -N `
  -L 8088:127.0.0.1:18080 -L 8189:127.0.0.1:8189 root@<IP>
```

Truy cập `http://localhost:8088`, nhập API key trong **OpenAI API** và xem **Dashboard**. Từ Vast Instance Portal cũng có thể mở **Avatar Live Studio** qua cổng được Vast cấp; cổng công khai thay đổi theo instance và được bảo vệ bằng token của Vast. Giữ terminal SSH mở để tunnel hoạt động.

## 5. Kiểm tra và xử lý nhanh

```bash
supervisorctl status fb-avatar-control fb-avatar-mediamtx fb-avatar-musetalk fb-avatar-pipeline fb-avatar-forwarder
curl -fsS http://127.0.0.1:18081/health
curl -I http://127.0.0.1:18080/
ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name,width,height,r_frame_rate,sample_rate -of compact=p=0 rtsp://127.0.0.1:8554/avatar
cd /workspace/fb-avatar-live
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
```

Kỳ vọng pipeline/MuseTalk/MediaMTX/control đều `RUNNING`, forwarder `STOPPED`, RTSP có H.264 720p/25 FPS và Opus 48 kHz. Sau lần khởi động đầu, chờ MuseTalk chuẩn bị avatar/cache rồi mới đánh giá preview. Xem lỗi bằng `tail -n 100 /var/log/portal/fb-avatar-pipeline.log` hoặc `.../fb-avatar-musetalk.log`; khi chỉnh `.env`, restart service liên quan bằng `supervisorctl restart fb-avatar-pipeline` hoặc `fb-avatar-musetalk`.

Nếu preview có tiếng nhưng WebRTC không hiện hình, kiểm tra cả hai cổng SSH tunnel `8088` và `8189`. Nếu câu nói bị đứt, kiểm tra tốc độ render trong log: với máy này MuseTalk đã đo khoảng 40 FPS sau warm-up cho một avatar, cao hơn nhịp phát 25 FPS; GPU khác, mạng khác, avatar khác hoặc LLM/TTS chậm hơn có thể làm hàng đợi hết. Giữ `STREAM_PREBUFFER_SEGMENTS=2` trước khi giảm thời gian đệm.

Để dùng TikTok LIVE Studio qua nguồn **Camera** và **micro** trên Windows, xem [LIVE_STUDIO_CAMERA.md](LIVE_STUDIO_CAMERA.md). MediaMTX mở RTMP tại `127.0.0.1:1935` chỉ để SSH tunnel đọc; path `avatar_studio` đổi Opus sang AAC theo yêu cầu để giữ một luồng hình/tiếng đồng bộ.
