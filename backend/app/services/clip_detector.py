"""
Clip detection service — finds clip-worthy moments in a transcript.

Ported from Chalchitra's detect microservice: builds sentences from
word-level timestamps, chunks them, prompts the LLM with [t=NNNs]
markers, then validates/snaps/dedupes the returned clips.
LLM calls go through llm_service (provider-agnostic).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.services.llm import llm_service

logger = logging.getLogger(__name__)

SENT_END = (".", "?", "!")
PAUSE = 0.6
CHUNK_CHARS = 5500
MARKER_GAP = 8.0

SYSTEM_PROMPT = (
    "You are a professional video editor. Return ONLY valid JSON, "
    "no markdown, no explanation."
)


def parse_duration_range(text: str) -> Tuple[Optional[float], Optional[float]]:
    m = re.search(
        r'(?:clip[s]?|each|duration|long|between)\D{0,20}?(\d+)\s*(?:and|[-to]+)\s*(\d+)\s*s(?:ec(?:onds?)?)?',
        text, re.I,
    )
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(
        r'(?:clip[s]?|each|duration|long|between)\D{0,20}?(\d+(?:\.\d+)?)\s*(?:and|[-to]+)\s*(\d+(?:\.\d+)?)\s*min(?:utes?)?',
        text, re.I,
    )
    if m:
        return float(m.group(1)) * 60, float(m.group(2)) * 60
    return None, None


def build_sentences(words: List[dict]) -> List[dict]:
    """Group word-level timestamps into sentences using punctuation and pauses."""
    sentences: List[dict] = []
    cur: List[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        token = str(w.get("word", "")).rstrip('"').rstrip("'")
        is_end = token.endswith(SENT_END)
        if not is_end and i + 1 < len(words):
            try:
                if float(words[i + 1]["start"]) - float(w["end"]) >= PAUSE:
                    is_end = True
            except (TypeError, ValueError, KeyError):
                pass
        if is_end or i == len(words) - 1:
            sentences.append({
                "text": " ".join(str(x.get("word", "")) for x in cur),
                "start": float(cur[0]["start"]),
                "end": float(cur[-1]["end"]),
            })
            cur = []
    return sentences


def chunk_sentences(sentences: List[dict], chunk_chars: int = CHUNK_CHARS) -> List[List[dict]]:
    chunks: List[List[dict]] = []
    cur: List[dict] = []
    cur_len = 0
    for s in sentences:
        if cur and cur_len + len(s["text"]) > chunk_chars:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(s)
        cur_len += len(s["text"]) + 1
    if cur:
        chunks.append(cur)
    return chunks


def render_chunk_text(chunk: List[dict]) -> str:
    """Render sentences with [t=NNNs] markers every MARKER_GAP seconds."""
    parts: List[str] = []
    last_marker = -MARKER_GAP
    for s in chunk:
        if s["start"] - last_marker >= MARKER_GAP:
            parts.append(f"[t={int(s['start'])}s]")
            last_marker = s["start"]
        parts.append(s["text"])
    return " ".join(parts)


def snap_to_sentences(start: float, end: float, sentences: List[dict]) -> Tuple[float, float]:
    """Snap clip bounds to real sentence boundaries."""
    s_start = min(sentences, key=lambda s: abs(s["start"] - start))["start"]
    candidates_end = [s for s in sentences if s["end"] > s_start]
    if not candidates_end:
        return s_start, end
    s_end = min(candidates_end, key=lambda s: abs(s["end"] - end))["end"]
    return round(s_start, 2), round(s_end + 0.15, 2)


def parse_clips_json(text: str) -> List[dict]:
    from app.services.llm_json import parse_llm_json_array
    # LLM may return {"clips":[...]} or just [...] — handle both
    text = text.replace("```json", "").replace("```", "").strip()
    if text.find("[") != -1 and (text.find("[") < text.find("{") or text.find("{") == -1):
        return parse_llm_json_array(text, context="clip_detector")
    start_idx = text.find("{")
    if start_idx == -1:
        raise ValueError("No JSON found in clip detector output")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start_idx:])
        if isinstance(data, dict):
            return data.get("clips", [])
        return data if isinstance(data, list) else []
    except Exception:
        return parse_llm_json_array(text, context="clip_detector_fallback")


MIN_MEDIA_DURATION = 10.0  # seconds — below this, skip LLM detection
MIN_WORDS_FOR_DETECTION = 5  # fewer words = nothing meaningful to detect

async def detect_clips(
    words: List[Dict[str, Any]],
    transcript: str = "",
    instructions: Optional[str] = None,
) -> List[dict]:
    """Find clip-worthy moments. Requires word-level timestamps."""
    if not words:
        raise ValueError("detect_clips requires word-level timestamps")

    user_min, user_max = (None, None)
    if instructions:
        user_min, user_max = parse_duration_range(instructions)
    lo = user_min if user_min else 20
    hi = user_max if user_max else 90

    sentences = build_sentences(words)
    total_duration = sentences[-1]["end"]
    if total_duration < MIN_MEDIA_DURATION:
        logger.warning(f"detect_clips: media too short ({total_duration:.1f}s < {MIN_MEDIA_DURATION}s) — returning empty")
        return []
    if len(words) < MIN_WORDS_FOR_DETECTION:
        logger.warning(f"detect_clips: too few words ({len(words)}) — returning empty")
        return []
    chunks = chunk_sentences(sentences)
    logger.info(
        f"detect_clips: {len(sentences)} sentences, {len(chunks)} chunks, "
        f"{total_duration:.0f}s, bounds {lo:.0f}-{hi:.0f}s"
    )

    all_clips: List[dict] = []
    for i, chunk in enumerate(chunks):
        chunk_text = render_chunk_text(chunk)
        c_start, c_end = int(chunk[0]["start"]), int(chunk[-1]["end"])
        timing_block = (
            f"This segment runs from {c_start}s to {c_end}s of the full video. "
            f"The [t=NNNs] markers in the text are REAL timestamps of the words that follow them.\n"
            f"- start_seconds MUST be the value of a [t=NNNs] marker where a strong moment begins.\n"
            f"- end_seconds MUST be start_seconds + a duration between {lo:.0f} and {hi:.0f} seconds, "
            f"and must not exceed {c_end}.\n"
            f"- Do NOT invent timestamps. Only use times visible in the markers."
        )
        user_instructions = instructions or "Find the 0-3 most engaging, self-contained clip-worthy moments."
        prompt = (
            f"Find clip-worthy moments in this transcript segment.\n\n"
            f"USER INSTRUCTIONS (follow exactly):\n{user_instructions}\n\n"
            f"TIMING RULES:\n{timing_block}\n\n"
            f"Transcript segment:\n{chunk_text}\n\n"
            f"Return ONLY valid JSON - do NOT copy the example values, write real titles "
            f"and timestamps based on the transcript content:\n"
            '{\n  "clips": [\n    {\n      "title": "<write a real punchy title here, max 6 words>",\n'
            '      "start_seconds": <number>,\n      "end_seconds": <number>,\n'
            '      "reason": "<why this works as a standalone clip>"\n    }\n  ]\n}'
        )

        for attempt in range(2):
            try:
                text = await llm_service.generate_response(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt=SYSTEM_PROMPT,
                )
                logger.info(f"chunk {i} raw output (first 200): {text[:200]}")
                chunk_clips = parse_clips_json(text)
                logger.info(f"chunk {i} parsed {len(chunk_clips)} clips")
                all_clips.extend(chunk_clips)
                break
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"chunk {i} JSON parse failed (attempt {attempt + 1}/2): {e}")
            except Exception as e:
                logger.error(f"chunk {i} FAILED: {type(e).__name__}: {e}")
                break

    min_dur, max_dur = lo * 0.85, hi * 1.15
    valid_clips: List[dict] = []
    for clip in all_clips:
        try:
            start = float(clip.get("start_seconds", 0))
            end = float(clip.get("end_seconds", 0))
        except (TypeError, ValueError):
            continue
        start, end = snap_to_sentences(start, end, sentences)
        duration = end - start
        if min_dur <= duration <= max_dur and start >= 0 and end <= total_duration + 1:
            valid_clips.append({
                "title": clip.get("title", f"Clip {len(valid_clips) + 1}"),
                "start_seconds": start,
                "end_seconds": end,
                "reason": clip.get("reason", ""),
            })
        else:
            logger.info(f"rejected clip start={start} end={end} dur={duration:.1f}")

    valid_clips.sort(key=lambda c: c["start_seconds"])
    non_overlapping: List[dict] = []
    last_end = -1.0
    for clip in valid_clips:
        if clip["start_seconds"] >= last_end:
            non_overlapping.append(clip)
            last_end = clip["end_seconds"]

    if not non_overlapping and total_duration >= 5:
        non_overlapping.append({
            "title": "Key Moment",
            "start_seconds": 0,
            "end_seconds": min(60, round(total_duration, 2)),
            "reason": "Best available segment",
        })

    logger.info(f"detect_clips: {len(non_overlapping)} final clips")
    return non_overlapping
