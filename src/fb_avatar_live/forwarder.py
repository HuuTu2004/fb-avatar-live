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
        destination = os.getenv("FACEBOOK_STREAM_URL", "").strip()
        source = os.getenv("STREAM_URL", "rtsp://127.0.0.1:8554/avatar").strip()
        if not destination:
            log.info("FACEBOOK_STREAM_URL is empty; waiting")
            time.sleep(5)
            continue
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-rtsp_transport", "tcp",
            "-i", source, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-f", "flv", destination,
        ]
        log.info("Starting Facebook forwarder")
        result = subprocess.run(command)
        log.warning("Facebook forwarder exited with code %s; retrying", result.returncode)
        time.sleep(2)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
