"""Receive Vast RTMP and publish it as Windows camera and microphone inputs."""

from __future__ import annotations

import argparse
import threading

import av
import numpy as np
import pyvirtualcam
import sounddevice as sd


SAMPLE_RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 2
MAX_AUDIO_BYTES = SAMPLE_RATE * BYTES_PER_FRAME // 2


class LiveFrames:
    def __init__(self, width: int, height: int) -> None:
        self.video = np.zeros((height, width, 3), dtype=np.uint8)
        self.audio = bytearray()
        self.lock = threading.Lock()

    def put_video(self, frame: np.ndarray) -> None:
        with self.lock:
            self.video = frame

    def get_video(self) -> np.ndarray:
        with self.lock:
            return self.video

    def put_audio(self, data: bytes) -> None:
        with self.lock:
            self.audio.extend(data)
            if len(self.audio) > MAX_AUDIO_BYTES:
                del self.audio[: len(self.audio) - MAX_AUDIO_BYTES]

    def take_audio(self, size: int) -> bytes:
        with self.lock:
            data = bytes(self.audio[:size])
            del self.audio[:size]
        return data.ljust(size, b"\0")

    def clear_audio(self) -> None:
        with self.lock:
            self.audio.clear()


def cable_output() -> int:
    matches = [
        index for index, device in enumerate(sd.query_devices())
        if device["max_output_channels"] >= CHANNELS and device["name"].lower().startswith("cable input")
    ]
    for index in matches:
        if "WASAPI" in sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"]:
            return index
    if matches:
        return matches[0]
    raise RuntimeError("CABLE Input missing. Install VB-CABLE and reboot Windows.")


def receive(url: str, frames: LiveFrames, stop: threading.Event, width: int, height: int) -> None:
    while not stop.is_set():
        try:
            print(f"Connecting RTMP: {url}", flush=True)
            with av.open(url, timeout=(5.0, 5.0), options={"rtmp_buffer": "100"}) as container:
                video = container.streams.video[0]
                audio = container.streams.audio[0]
                resampler = av.AudioResampler(format="s16", layout="stereo", rate=SAMPLE_RATE)
                print("Receiving video and audio from Vast.", flush=True)
                for packet in container.demux(video, audio):
                    if stop.is_set():
                        return
                    for frame in packet.decode():
                        if packet.stream == video:
                            if frame.width != width or frame.height != height:
                                frame = frame.reformat(width=width, height=height)
                            frames.put_video(frame.to_ndarray(format="rgb24"))
                        else:
                            for converted in resampler.resample(frame):
                                frames.put_audio(converted.to_ndarray().tobytes())
        except (OSError, av.FFmpegError, IndexError) as exc:
            print(f"RTMP disconnected ({exc}); retrying in 2 seconds.", flush=True)
        frames.clear_audio()
        stop.wait(2)


def run(url: str, width: int, height: int, fps: int) -> None:
    device = cable_output()
    frames = LiveFrames(width, height)
    stop = threading.Event()

    def audio_callback(outdata, frame_count, _time, _status) -> None:
        outdata[:] = frames.take_audio(frame_count * BYTES_PER_FRAME)

    with pyvirtualcam.Camera(width=width, height=height, fps=fps, backend="obs") as camera, sd.RawOutputStream(
        device=device, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
        blocksize=960, latency="low", callback=audio_callback,
    ):
        print(f"Camera: {camera.device} | Vast audio -> CABLE Input -> CABLE Output microphone | {width}x{height} @{fps} FPS", flush=True)
        reader = threading.Thread(target=receive, args=(url, frames, stop, width, height), daemon=True)
        reader.start()
        try:
            while True:
                camera.send(frames.get_video())
                camera.sleep_until_next_frame()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            reader.join(timeout=6)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="rtmp://127.0.0.1:1935/avatar_studio")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=25)
    args = parser.parse_args()
    run(args.url, args.width, args.height, args.fps)
