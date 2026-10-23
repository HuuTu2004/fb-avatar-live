from __future__ import annotations

import logging
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import load_env


log = logging.getLogger(__name__)
ROOT = Path(os.getenv("ROOT", "/workspace/fb-avatar-live"))


def _write_health(channel: str, state: str, exit_code: int | None = None) -> None:
    path = Path(f"/tmp/fb-avatar-forwarder-{channel}.json")
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"state": state, "updated_at": time.time(), "exit_code": exit_code}), encoding="utf-8")
    os.replace(temp, path)


def _destinations(channel: str) -> list[tuple[str, str]]:
    if channel != "facebook":
        raise ValueError(f"Unsupported stream destination: {channel}")
    url = os.getenv("FACEBOOK_STREAM_URL", "").strip()
    return [("Facebook", url)] if url else []


def run(channel: str = "facebook") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _write_health(channel, "starting")
    while True:
        load_env(ROOT / ".env")
        destinations = _destinations(channel)
        source = os.getenv("STREAM_URL", "rtsp://127.0.0.1:8554/avatar").strip()
        if not destinations:
            log.info("No Facebook stream destination; waiting")
            _write_health(channel, "unconfigured")
            time.sleep(5)
            continue
        processes = []
        started_at = time.monotonic()
        last_health = 0.0
        _write_health(channel, "connecting")
        for name, destination in destinations:
            command = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "quiet", "-rtsp_transport", "tcp",
                "-i", source, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                "-f", "flv", destination,
            ]
            log.info("Starting %s forwarder", name)
            processes.append((name, subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        while all(process.poll() is None for _, process in processes):
            now = time.monotonic()
            if now - started_at >= 5 and now - last_health >= 5:
                _write_health(channel, "sending")
                last_health = now
            time.sleep(1)
        for name, process in processes:
            if process.poll() is None:
                process.terminate()
            else:
                log.warning("%s forwarder exited with code %s", name, process.returncode)
                _write_health(channel, "retrying", process.returncode)
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        time.sleep(2)


if __name__ == "__main__":
    try:
        run(sys.argv[1] if len(sys.argv) > 1 else "facebook")
    except KeyboardInterrupt:
        pass
