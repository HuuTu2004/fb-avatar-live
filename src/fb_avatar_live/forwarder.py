from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from .config import load_env


log = logging.getLogger(__name__)
ROOT = Path(os.getenv("ROOT", "/workspace/fb-avatar-live"))


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        load_env(ROOT / ".env")
        destinations = [
            ("Facebook", os.getenv("FACEBOOK_STREAM_URL", "").strip()),
            ("TikTok", os.getenv("TIKTOK_STREAM_URL", "").strip()),
        ]
        destinations = [(name, url) for name, url in destinations if url]
        source = os.getenv("STREAM_URL", "rtsp://127.0.0.1:8554/avatar").strip()
        if not destinations:
            log.info("No Facebook or TikTok stream destination; waiting")
            time.sleep(5)
            continue
        processes = []
        for name, destination in destinations:
            command = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-rtsp_transport", "tcp",
                "-i", source, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                "-f", "flv", destination,
            ]
            log.info("Starting %s forwarder", name)
            processes.append((name, subprocess.Popen(command)))
        while all(process.poll() is None for _, process in processes):
            time.sleep(1)
        for name, process in processes:
            if process.poll() is None:
                process.terminate()
            else:
                log.warning("%s forwarder exited with code %s", name, process.returncode)
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        time.sleep(2)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
