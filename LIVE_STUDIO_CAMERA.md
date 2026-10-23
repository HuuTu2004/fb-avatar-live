# Vast → camera và micro ảo cho TikTok LIVE Studio (Windows)

App này đọc RTMP từ Vast qua SSH, đưa hình vào **OBS Virtual Camera** và phát tiếng vào **CABLE Input** trên Windows. VB-CABLE chuyển tiếng sang đầu micro **CABLE Output** để TikTok LIVE Studio thu. OBS chỉ cung cấp driver camera ảo; **không cần mở OBS**. Trong LIVE Studio, thêm nguồn **Camera**, không dùng Link hoặc Window Capture.

## Cài một lần trên máy Windows

1. Cài [OBS Studio](https://obsproject.com/download) để có driver **OBS Virtual Camera**. Không cần chạy OBS.
2. Cài [VB-CABLE](https://vb-audio.com/Cable/) bằng `VBCABLE_Setup_x64.exe` với quyền Administrator, rồi khởi động lại Windows. Sau đó phải thấy thiết bị phát **CABLE Input** và thiết bị ghi **CABLE Output**.
3. Trong PowerShell tại `D:\Live\fb-avatar-live`, chạy:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r scripts\live_studio_requirements.txt
```

Trên Vast, `config/mediamtx.yml` phải bật RTMP loopback `127.0.0.1:1935` và có path `avatar_studio` như repo này. Nguồn preview gốc vẫn là `rtsp://127.0.0.1:8554/avatar`; MediaMTX đổi âm thanh Opus sang AAC khi Windows yêu cầu RTMP. Không cần stream key TikTok.

## Chạy

Khi preview Vast đang có hình/tiếng, chạy trong PowerShell:

```powershell
cd D:\Live\fb-avatar-live
powershell -ExecutionPolicy Bypass -File scripts\start_live_studio_camera.ps1
```

Giữ cửa sổ PowerShell này mở. Script tự mở SSH tunnel chỉ trên `127.0.0.1:1935`, rồi chạy app. Nếu đổi máy Vast, truyền `-VastHost`, `-SshPort` và `-IdentityFile` vào script. Bấm **Ctrl+C** để dừng camera, micro và tunnel.

Trong TikTok LIVE Studio:

1. **Add source → Camera → OBS Virtual Camera**. Chọn vùng/crop trong LIVE Studio nếu muốn khung dọc; app giữ nguyên video 1280×720, 25 FPS từ Vast.
2. Chọn nguồn micro/âm thanh đầu vào **CABLE Output (VB-Audio Virtual Cable)**. Tắt micro vật lý và desktop audio trong LIVE Studio nếu không muốn lẫn/nhân đôi tiếng.
3. Kiểm tra khung xem trước có hình chuyển động và thanh âm lượng nhảy, rồi mới bấm Go LIVE trong LIVE Studio.

Nếu không thấy OBS Virtual Camera, kiểm tra driver camera ảo của OBS đã được cài. Nếu không thấy CABLE Output, cài VB-CABLE và khởi động lại Windows. Nếu app báo mất RTMP, kiểm tra preview Vast và kết nối SSH; app giữ khung hình cuối và tự thử kết nối lại. Cổng RTMP chỉ mở trên localhost của Vast và máy Windows.
