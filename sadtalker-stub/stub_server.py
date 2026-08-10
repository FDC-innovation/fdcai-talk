"""
Demo stub for SadTalker — returns a pre-baked talking-head clip.
Matches the real contract (bandish-mvp/sadtalker/server.py):
  POST /generate {image_url, audio_url} -> video/mp4 bytes
  GET  /health -> {"status": "ok"}
Ignores inputs, always returns the baked avatar. Zero GPU, instant.
Swap SADTALKER_URL to a real GPU box later and the same path runs.
"""
import os
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI()
BAKED = os.environ.get("BAKED_CLIP", "/app/baked.mp4")

class GenerateRequest(BaseModel):
    image_url: str
    audio_url: str

@app.post("/generate")
def generate(req: GenerateRequest):
    with open(BAKED, "rb") as f:
        return Response(content=f.read(), media_type="video/mp4")

@app.get("/health")
def health():
    return {"status": "ok"}
