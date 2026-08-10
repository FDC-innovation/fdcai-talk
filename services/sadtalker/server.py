"""
Tiny HTTP wrapper around SadTalker's inference.py.

Accepts an image URL + audio URL (both reachable inside the Docker network -
e.g. presigned MinIO URLs), downloads them, shells out to inference.py, and
returns the resulting video bytes.

CPU-only - this will be slow (minutes, not seconds). That's the accepted
tradeoff for having zero external API dependency.
"""
import glob
import os
import subprocess
import tempfile

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI()

SADTALKER_DIR = "/app"


class GenerateRequest(BaseModel):
    image_url: str
    audio_url: str


# ngrok's free tier serves an HTML interstitial to unrecognized clients
# unless this header is present — without it we'd save HTML as .wav/.png.
NGROK_HEADERS = {"ngrok-skip-browser-warning": "true"}


def _download(url: str, dest_path: str):
    with httpx.stream("GET", url, timeout=120, headers=NGROK_HEADERS, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise HTTPException(502, f"Failed to download {url}: {r.status_code}")
        with open(dest_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


@app.post("/generate")
def generate(req: GenerateRequest):
    with tempfile.TemporaryDirectory() as tmp:
        image_path = os.path.join(tmp, "source.png")
        audio_path = os.path.join(tmp, "audio.wav")
        result_dir = os.path.join(tmp, "results")
        os.makedirs(result_dir, exist_ok=True)

        _download(req.image_url, image_path)
        _download(req.audio_url, audio_path)

        # NOTE: no --enhancer flag - gfpgan enhancement roughly doubles
        # runtime for a quality bump that matters less than turnaround
        # time for an MVP. Add --enhancer gfpgan back in if quality
        # matters more than speed once this is confirmed working.
        cmd = [
            "python", "inference.py",
            "--driven_audio", audio_path,
            "--source_image", image_path,
            "--result_dir", result_dir,
            "--still",
        ]

        proc = subprocess.run(
            cmd, cwd=SADTALKER_DIR, capture_output=True, text=True, timeout=5400
        )

        if proc.returncode != 0:
            raise HTTPException(
                500,
                f"SadTalker inference failed (exit {proc.returncode}):\n"
                f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}",
            )

        # inference.py writes to a timestamped subfolder under result_dir -
        # find the most recently created mp4 rather than assuming a fixed name.
        mp4_files = glob.glob(os.path.join(result_dir, "**", "*.mp4"), recursive=True)
        if not mp4_files:
            raise HTTPException(500, f"No output video found. stdout: {proc.stdout[-2000:]}")
        latest = max(mp4_files, key=os.path.getmtime)

        with open(latest, "rb") as f:
            return Response(content=f.read(), media_type="video/mp4")


@app.get("/health")
def health():
    return {"status": "ok"}
