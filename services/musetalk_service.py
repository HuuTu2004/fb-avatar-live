from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import WhisperModel

ROOT = Path(os.getenv("ROOT", "/workspace/fb-avatar-live")).resolve()
sys.path.insert(0, str(ROOT / "src"))
from fb_avatar_live.config import load_env
from fb_avatar_live.video import rotate_cycle, scene_frame_map, smooth_bboxes

load_env(ROOT / ".env")


class RenderRequest(BaseModel):
    audio_path: str
    output_name: str


class MuseTalkRenderer:
    def __init__(self) -> None:
        self.root = Path(os.getenv("MUSETALK_ROOT", "/workspace/MuseTalk")).resolve()
        self.shared_dir = Path(os.getenv("DATA_DIR", "/workspace/fb-avatar-live/data")).resolve()
        self.avatar_video = Path(os.getenv("AVATAR_PREP_VIDEO") or os.environ["AVATAR_VIDEO"]).resolve()
        self.avatar_id = os.getenv("AVATAR_ID", "live-avatar")
        self.fps = int(os.getenv("OUTPUT_FPS", "25"))
        self.width = int(os.getenv("OUTPUT_WIDTH", "1280"))
        self.height = int(os.getenv("OUTPUT_HEIGHT", "720"))
        self.batch_size = int(os.getenv("MUSETALK_BATCH_SIZE", "20"))
        self.encoder = os.getenv("VIDEO_ENCODER", "libx264")
        self.bitrate = os.getenv("VIDEO_BITRATE", "4500k")
        self.bbox_smooth_window = int(os.getenv("MUSETALK_BBOX_SMOOTH_WINDOW", "7"))
        self.lock = threading.Lock()
        self.frame_cursor = 0
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(self.root)
        sys.path.insert(0, str(self.root))

        from musetalk.utils.audio_processor import AudioProcessor
        from musetalk.utils.face_parsing import FaceParsing
        from musetalk.utils.utils import load_all_model
        from scripts import realtime_inference as realtime

        self.realtime = realtime
        realtime.args = SimpleNamespace(
            version="v15",
            extra_margin=int(os.getenv("MUSETALK_EXTRA_MARGIN", "10")),
            parsing_mode=os.getenv("MUSETALK_PARSING_MODE", "jaw"),
            audio_padding_length_left=int(os.getenv("MUSETALK_AUDIO_PAD_LEFT", "2")),
            audio_padding_length_right=int(os.getenv("MUSETALK_AUDIO_PAD_RIGHT", "2")),
            skip_save_images=False,
        )
        realtime.device = torch.device("cuda:0")
        realtime.vae, realtime.unet, realtime.pe = load_all_model(
            unet_model_path="models/musetalkV15/unet.pth",
            vae_type="sd-vae",
            unet_config="models/musetalkV15/musetalk.json",
            device=realtime.device,
        )
        realtime.timesteps = torch.tensor([0], device=realtime.device)
        realtime.pe = realtime.pe.half().to(realtime.device)
        realtime.vae.vae = realtime.vae.vae.half().to(realtime.device)
        realtime.unet.model = realtime.unet.model.half().to(realtime.device)
        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        if os.getenv("MUSETALK_XFORMERS", "true").lower() == "true":
            realtime.unet.model.enable_xformers_memory_efficient_attention()
        realtime.weight_dtype = realtime.unet.model.dtype
        realtime.audio_processor = AudioProcessor(feature_extractor_path="models/whisper")
        realtime.whisper = WhisperModel.from_pretrained("models/whisper")
        realtime.whisper = realtime.whisper.to(device=realtime.device, dtype=realtime.weight_dtype).eval()
        realtime.whisper.requires_grad_(False)
        realtime.fp = FaceParsing(
            left_cheek_width=int(os.getenv("MUSETALK_LEFT_CHEEK", "90")),
            right_cheek_width=int(os.getenv("MUSETALK_RIGHT_CHEEK", "90")),
        )

        detect_bboxes = realtime.get_landmark_and_bbox

        def detect_smoothed_bboxes(images, bbox_shift):
            coords, frames = detect_bboxes(images, bbox_shift)
            return smooth_bboxes(coords, self.bbox_smooth_window), frames

        realtime.get_landmark_and_bbox = detect_smoothed_bboxes

        model_avatar_id = f"{self.avatar_id}-smooth{self.bbox_smooth_window}v2"
        cache = self.root / "results" / "v15" / "avatars" / model_avatar_id / "latents.pt"
        self.avatar = realtime.Avatar(
            avatar_id=model_avatar_id,
            video_path=str(self.avatar_video),
            bbox_shift=0,
            batch_size=self.batch_size,
            preparation=not cache.exists(),
        )
        timeline_path = os.getenv("AVATAR_TIMELINE_FILE", "").strip()
        if timeline_path:
            timeline = json.loads(Path(timeline_path).read_text(encoding="utf-8"))
            frames_per_scene = int(timeline["frames_per_scene"])
            source_scenes = int(timeline["source_scenes"])
            prepared_frames = len(self.avatar.frame_list_cycle) // 2
            expected_frames = frames_per_scene * source_scenes
            if prepared_frames != expected_frames:
                raise ValueError(
                    f"Prepared avatar has {prepared_frames} source frames, expected {expected_frames}"
                )
            self.frame_map = scene_frame_map(timeline["scene_order"], frames_per_scene, source_scenes)
        else:
            self.frame_map = list(range(len(self.avatar.frame_list_cycle)))
        self.latent_cycle = [self.avatar.input_latent_list_cycle[index] for index in self.frame_map]
        if os.getenv("MUSETALK_WARMUP", "true").lower() == "true":
            self._warm_up()

    def _warm_up(self) -> None:
        audio_path = self.shared_dir / ".musetalk-warmup.wav"
        output_path = self.shared_dir / "video" / "musetalk-warmup.mp4"
        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        try:
            self.render(str(audio_path), "musetalk-warmup")
        finally:
            audio_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            self.frame_cursor = 0

    def _safe_shared_path(self, raw: str) -> Path:
        path = Path(raw).resolve()
        if self.shared_dir != path and self.shared_dir not in path.parents:
            raise ValueError("Path must be inside DATA_DIR")
        return path

    def _encoder_args(self) -> list[str]:
        if self.encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-b:v", self.bitrate]
        return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-b:v", self.bitrate]

    def _write_frames(
        self,
        frames: queue.Queue,
        count: int,
        temp_video: Path,
        errors: list[BaseException],
        start_cursor: int,
    ) -> None:
        first = self.avatar.frame_list_cycle[0]
        source_height, source_width = first.shape[:2]
        video_filter = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        )
        command = [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{source_width}x{source_height}", "-r", str(self.fps), "-i", "pipe:0", "-an",
            "-vf", video_filter, *self._encoder_args(), "-g", str(self.fps * 2), str(temp_video),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            assert process.stdin is not None
            for frame_offset in range(count):
                result = frames.get()
                index = self.frame_map[(start_cursor + frame_offset) % len(self.frame_map)]
                bbox = self.avatar.coord_list_cycle[index]
                original = self.avatar.frame_list_cycle[index].copy()
                x1, y1, x2, y2 = bbox
                resized = cv2.resize(result.astype(np.uint8), (x2 - x1, y2 - y1))
                combined = self.realtime.get_image_blending(
                    original,
                    resized,
                    bbox,
                    self.avatar.mask_list_cycle[index],
                    self.avatar.mask_coords_list_cycle[index],
                )
                process.stdin.write(np.ascontiguousarray(combined).tobytes())
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError("FFmpeg video encoder failed")
        except BaseException as exc:
            errors.append(exc)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.kill()

    @torch.no_grad()
    def render(self, audio_raw: str, output_name: str) -> tuple[Path, float, int]:
        audio_path = self._safe_shared_path(audio_raw)
        if not audio_path.is_file():
            raise FileNotFoundError(audio_path)
        if not output_name.replace("-", "").isalnum():
            raise ValueError("Invalid output_name")
        output_dir = self.shared_dir / "video"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{output_name}.mp4"
        temp_video = output_dir / f".{output_name}.video.mp4"

        with self.lock:
            started = time.perf_counter()
            rt = self.realtime
            features, librosa_length = rt.audio_processor.get_audio_feature(
                str(audio_path), weight_dtype=rt.weight_dtype
            )
            chunks = rt.audio_processor.get_whisper_chunk(
                features,
                rt.device,
                rt.weight_dtype,
                rt.whisper,
                librosa_length,
                fps=self.fps,
                audio_padding_length_left=rt.args.audio_padding_length_left,
                audio_padding_length_right=rt.args.audio_padding_length_right,
            )
            frame_queue: queue.Queue = queue.Queue()
            errors: list[BaseException] = []
            start_cursor = self.frame_cursor
            writer = threading.Thread(
                target=self._write_frames,
                args=(frame_queue, len(chunks), temp_video, errors, start_cursor),
                daemon=True,
            )
            writer.start()
            latent_cycle = rotate_cycle(self.latent_cycle, start_cursor)
            generator = rt.datagen(chunks, latent_cycle, self.batch_size)
            for whisper_batch, latent_batch in generator:
                audio_features = rt.pe(whisper_batch.to(rt.device))
                latent_batch = latent_batch.to(device=rt.device, dtype=rt.unet.model.dtype)
                predicted = rt.unet.model(
                    latent_batch,
                    rt.timesteps,
                    encoder_hidden_states=audio_features,
                ).sample
                decoded = rt.vae.decode_latents(predicted.to(device=rt.device, dtype=rt.vae.vae.dtype))
                for frame in decoded:
                    frame_queue.put(frame)
            writer.join()
            if errors:
                raise errors[0]
            self.frame_cursor = (start_cursor + len(chunks)) % len(self.frame_map)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error", "-i", str(temp_video), "-i", str(audio_path),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000",
                    "-ac", "2", "-shortest", "-movflags", "+faststart", str(output_path),
                ],
                check=True,
            )
            temp_video.unlink(missing_ok=True)
            return output_path, time.perf_counter() - started, len(chunks)


app = FastAPI(title="MuseTalk persistent renderer")
renderer: MuseTalkRenderer | None = None
startup_error: str | None = None


@app.on_event("startup")
async def startup() -> None:
    global renderer, startup_error
    try:
        renderer = await asyncio.to_thread(MuseTalkRenderer)
    except Exception as exc:
        startup_error = repr(exc)


@app.get("/health")
def health() -> dict:
    return {"ready": renderer is not None, "error": startup_error}


@app.post("/render")
async def render(request: RenderRequest) -> dict:
    if renderer is None:
        raise HTTPException(status_code=503, detail=startup_error or "renderer is starting")
    try:
        path, elapsed, frames = await asyncio.to_thread(
            renderer.render, request.audio_path, request.output_name
        )
        return {"video_path": str(path), "elapsed_seconds": elapsed, "frames": frames}
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=repr(exc)) from exc
