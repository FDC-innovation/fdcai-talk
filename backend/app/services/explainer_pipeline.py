"""
Explainer pipeline — turn a deck-walkthrough VIDEO into per-section avatar
scripts (Stage A), ready to be spoken by the talking-head engine (Stage B).

Chain:
    video → [ffmpeg extract audio] → Whisper transcript
          → Llama → N short section scripts (one avatar clip each)

Each section is intentionally short so the output works as B-roll an editor
can drop across a timeline. Reuses the proven services (stt, llm, llm_json)
exactly as the podcast pipeline does — no new model wiring.
"""
import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.services.llm import llm_service
from app.services.stt import stt_service
from app.services.llm_json import parse_llm_json_array

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a scriptwriter for an AI avatar that delivers short, punchy "
    "explainer clips used as B-roll in edited videos. You write natural, "
    "spoken-word scripts — conversational, clear, no slide numbers, no "
    "'as you can see', no meta references to the deck. Each script stands "
    "alone as something a presenter would say to camera."
)

_DEFAULT_SECTIONS = "3-6"


def _extract_audio(video_path: str, out_wav: str) -> None:
    """Pull a 16kHz mono WAV out of the video for Whisper."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", out_wav,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg audio extraction failed: {err}")


async def build_section_scripts(
    transcript: str,
    instructions: Optional[str] = None,
    section_range: str = _DEFAULT_SECTIONS,
) -> list:
    """Transcript → list of {title, script} sections via the LLM."""
    words = transcript.split()
    if len(words) > 3000:
        transcript = " ".join(words[:3000])

    user_instruction = f"\nUser instruction: {instructions}\n" if instructions else ""
    prompt = (
        f"Below is a transcript of a deck/presentation walkthrough video.\n"
        f"Break its content into {section_range} distinct sections, each covering "
        f"one clear idea.{user_instruction}\n"
        "For EACH section return:\n"
        "  - title: max 6 words, describes the idea\n"
        "  - script: 40-70 words the avatar will SPEAK aloud — natural, "
        "conversational, standalone. No slide references, no 'in this slide'.\n"
        "Return ONLY a raw JSON array of objects with keys 'title' and 'script'.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    raw = await llm_service.generate_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=SYSTEM_PROMPT,
        thinking=False,
    )
    sections = parse_llm_json_array(raw, context="explainer_sections")

    clean = []
    for s in sections:
        title = str(s.get("title", "")).strip()
        script = str(s.get("script", "")).strip()
        if script:
            clean.append({"title": title or "Section", "script": script})
    logger.info(f"build_section_scripts: {len(clean)} sections")
    return clean


async def transcribe_video(video_path: str, language: str = "en") -> str:
    """Video → transcript (extracts audio, runs Whisper)."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "audio.wav")
        await asyncio.to_thread(_extract_audio, video_path, wav)
        logger.info(f"Explainer: extracted audio -> {wav}, running Whisper")
        transcript = await stt_service.transcribe(wav, language=language)
    return transcript.strip()


async def video_to_scripts(
    video_path: str,
    language: str = "en",
    instructions: Optional[str] = None,
) -> dict:
    """Stage A end-to-end: video → transcript → section scripts."""
    transcript = await transcribe_video(video_path, language=language)
    if not transcript:
        return {"transcript": "", "sections": [], "message": "empty transcript"}
    sections = await build_section_scripts(transcript, instructions=instructions)
    return {"transcript": transcript, "sections": sections}
