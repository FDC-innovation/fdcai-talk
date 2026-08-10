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
        "For EACH section return an object with:\n"
        "  - title: max 6 words\n"
        "  - script: EXACTLY 8-12 words, one short spoken sentence, conversational, standalone.\n\n"
        "CRITICAL: Respond with ONLY a raw JSON array. No prose, no markdown, no bullets, "
        "no code fences, no explanation. Start your reply with [ and end with ].\n"
        "EXACT FORMAT:\n"
        '[{"title": "Example Title", "script": "This is a short spoken sentence example here."}]\n\n'
        f"TRANSCRIPT:\n{transcript}\n\n"
        "JSON array:"
    )

    raw = await llm_service.generate_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=SYSTEM_PROMPT,
        thinking=False,
        json_mode=True,
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


# ============================================================================
# Stage B — sections -> per-section avatar clips
# ============================================================================
# For each {title, script} section from Stage A:
#     script -> tts_service.synthesize(speaker_wav=optional) -> audio.wav
#     avatar image + audio.wav -> AvatarAnimator().animate() -> clip.mp4
#
# Voice choice is honored via `speaker_wav`:
#     speaker_wav set   -> cloned voice (Chatterbox on GPU box)
#     speaker_wav None  -> plain TTS (male/female handled inside tts_service)
#
# Engine: routes through the animator (local-file path), mirroring the
# talking-head task's `else` branch in celery_app.py. animate() GPU-detects
# and auto-falls-back to `simple` (ffmpeg static image + audio) on no-GPU
# machines, so this is safe on the Mac and does real MuseTalk on the GPU box.
#
# NOTE: SadTalker (sadtalker_engine.generate_avatar) is URL-only -- it posts
# image_url/audio_url to an HTTP server. Per-section audio is generated locally
# and has no URL mid-pipeline, so SadTalker-per-section needs an audio-upload
# step to mint URLs first. That's deferred to Stage C wiring; Stage B uses the
# animator path, which is the correct no-GPU-safe route regardless.


async def sections_to_clips(
    sections: list,
    image_path: str,
    speaker_wav: Optional[str] = None,
    language: str = "en",
    out_dir: Optional[str] = None,
) -> list:
    """
    Stage B: each section script -> spoken audio -> avatar clip.

    Args:
        sections:    list of {title, script} from Stage A.
        image_path:  LOCAL path to the avatar source image (png/jpg).
        speaker_wav: optional reference wav for voice cloning. If None,
                     plain TTS is used (male/female decided by tts_service).
        language:    2-letter language code passed to TTS.
        out_dir:     base output dir; a temp dir is made if not given.

    Returns:
        list of {index, title, script, audio_path, clip_path, engine,
                 size_bytes} -- one entry per section that produced a clip.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Avatar image not found: {image_path}")

    # Import here (not at module top) so Stage A stays importable even if the
    # animator's heavier deps are unavailable in a given environment.
    from app.services.tts import tts_service
    from app.services.animator import AvatarAnimator

    base = out_dir or tempfile.mkdtemp(prefix="explainer_clips_")
    os.makedirs(base, exist_ok=True)

    animator = AvatarAnimator()
    clips = []

    for i, section in enumerate(sections):
        script = str(section.get("script", "")).strip()
        title = str(section.get("title", "")).strip() or f"Section {i + 1}"
        if not script:
            logger.warning(f"sections_to_clips: section {i} has empty script, skipping")
            continue

        sec_dir = os.path.join(base, f"section_{i:02d}")
        os.makedirs(sec_dir, exist_ok=True)
        audio_path = os.path.join(sec_dir, "audio.wav")
        clip_path = os.path.join(sec_dir, "clip.mp4")

        logger.info(f"sections_to_clips: [{i}] '{title}' -- synthesizing audio")
        await tts_service.synthesize(
            text=script,
            output_path=audio_path,
            speaker_wav=speaker_wav,
            language=language,
        )

        logger.info(f"sections_to_clips: [{i}] '{title}' -- animating clip")
        final_path = await animator.animate(image_path, audio_path, clip_path)

        size = os.path.getsize(final_path) if os.path.exists(final_path) else 0
        clips.append({
            "index": i,
            "title": title,
            "script": script,
            "audio_path": audio_path,
            "clip_path": final_path,
            "engine": animator.engine,
            "size_bytes": size,
        })
        logger.info(
            f"sections_to_clips: [{i}] done -- {size} bytes via {animator.engine}"
        )

    logger.info(f"sections_to_clips: produced {len(clips)} clip(s)")
    return clips
