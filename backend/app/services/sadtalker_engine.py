"""
SadTalker engine — device-independent caller.

Set SADTALKER_URL in .env to point at any running SadTalker server:
  - Local Docker on GPU box: http://sadtalker:8005
  - Colab ngrok:             https://xxxx.ngrok-free.app
  - Cloud GPU VM:            http://1.2.3.4:5003

If SADTALKER_URL is not set, jobs return a clear not_configured result
instead of crashing — safe for dev machines with no GPU.

The SadTalker server contract (from bandish-mvp-final/sadtalker/server.py):
  POST /generate  { image_url: str, audio_url: str } -> video/mp4 bytes
  GET  /health    -> { status: "ok" }
"""
import logging
import os
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

SADTALKER_URL = getattr(settings, "SADTALKER_URL", None) or os.environ.get("SADTALKER_URL", "")
TIMEOUT = 5400  # 90 min — inference.py can be slow on CPU


async def check_health() -> bool:
    if not SADTALKER_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{SADTALKER_URL.rstrip('/')}/health")
            return r.status_code == 200
    except Exception as e:
        logger.warning(f"SadTalker health check failed: {e}")
        return False


async def generate_avatar(image_url: str, audio_url: str, job_id: str) -> dict:
    """
    Call SadTalker server and save the returned mp4 to video_cache volume.
    Returns dict with file_path and status, never raises (logs errors instead).
    """
    if not SADTALKER_URL:
        logger.warning(f"[{job_id}] SADTALKER_URL not set — returning not_configured")
        return {
            "status": "not_configured",
            "message": "Set SADTALKER_URL in .env to point at a running SadTalker server",
        }

    output_dir = f"/tmp/videos/sadtalker/{job_id}"
    os.makedirs(output_dir, exist_ok=True)
    output_path = f"{output_dir}/avatar.mp4"

    logger.info(f"[{job_id}] Calling SadTalker at {SADTALKER_URL}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{SADTALKER_URL.rstrip('/')}/generate",
                json={"image_url": image_url, "audio_url": audio_url},
            )
        if r.status_code != 200:
            return {"status": "failed", "error": f"SadTalker returned {r.status_code}: {r.text[-500:]}"}
        with open(output_path, "wb") as f:
            f.write(r.content)
        logger.info(f"[{job_id}] SadTalker done — {len(r.content)//1024}KB -> {output_path}")
        return {"status": "done", "file_path": output_path, "size_bytes": len(r.content)}
    except httpx.TimeoutException:
        return {"status": "failed", "error": "SadTalker timed out (>90min)"}
    except Exception as e:
        return {"status": "failed", "error": str(e)[-500:]}
