import json
import logging
import os
import re
import shutil
import subprocess
import tempfile

from app.services.llm import llm_service
from app.services.clip_cutter import cut_clips, _probe_duration

logger = logging.getLogger(__name__)

OUTPUT_DIR = "/tmp/videos/podcast"
FPS = 30
CARD_DURATION = 3.5
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MIN_CHAPTER_SECONDS = 5.0

SYSTEM_PROMPT = "You are a professional podcast editor. You respond with ONLY valid JSON. No markdown, no backticks, no explanation."


def parse_chapter_count(text):
    m = re.search(r'(\d+)\s*[-\u2013to]+\s*(\d+)\s*chapters?', text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'(\d+)\s*chapters?', text, re.I)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None


async def detect_chapters(transcript: str, duration: float, instructions=None) -> list:
    if duration < 30.0:
        logger.warning(f"detect_chapters: media too short ({duration:.1f}s < 30s) — returning single chapter")
        return [{"title": "Full Video", "subtitle": "Complete recording", "start_seconds": 0.0, "end_seconds": duration}]
    words = transcript.split()
    if len(words) > 3000:
        transcript = " ".join(words[:3000])
    custom = instructions or ""
    min_ch, max_ch = parse_chapter_count(custom) if custom else (None, None)
    count_range = f"{min_ch}-{max_ch}" if min_ch and max_ch else "2-5"
    count_hint = f"\nCRITICAL: Return EXACTLY {min_ch} to {max_ch} chapters - no more, no fewer.\n" if min_ch else ""
    user_instruction = f"\nUser instruction: {custom}\n{count_hint}" if custom else ""
    prompt = (
        f"The video is exactly {duration:.0f} seconds long.\n"
        f"Given the transcript below, identify {count_range} major chapters covering distinct topics.{user_instruction}\n"
        "For each chapter return: title (max 6 words), subtitle (one sentence), "
        "start_seconds (REAL timestamps from topic shifts, NOT evenly spaced), "
        "end_seconds (= start_seconds of NEXT chapter).\n"
        "RULES: chapters CONTIGUOUS, first start=0, "
        f"last end={duration:.0f}.\n"
        "Return ONLY a raw JSON array.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    raw = await llm_service.generate_response(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=SYSTEM_PROMPT, thinking=False,
    )
    from app.services.llm_json import parse_llm_json_array
    chapters = parse_llm_json_array(raw, context="podcast_chapters")
    chapters.sort(key=lambda c: float(c.get("start_seconds", 0)))
    for i in range(len(chapters) - 1):
        chapters[i]["end_seconds"] = chapters[i + 1]["start_seconds"]
    if chapters:
        chapters[0]["start_seconds"] = 0.0
        chapters[-1]["end_seconds"] = duration
    for c in chapters:
        c["start_seconds"] = round(min(float(c.get("start_seconds", 0)), duration), 2)
        c["end_seconds"] = round(min(float(c.get("end_seconds", 0)), duration), 2)
    chapters = [c for c in chapters if c["end_seconds"] > c["start_seconds"] + MIN_CHAPTER_SECONDS]
    if max_ch and len(chapters) > max_ch:
        chapters = chapters[:max_ch]
        if chapters:
            chapters[-1]["end_seconds"] = duration
    logger.info(f"detect_chapters: {len(chapters)} chapters")
    return chapters


def _run(cmd, label):
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed: {result.stderr.decode(errors='replace')[-500:]}")


def _esc(t):
    return t.replace("'", "\u2019").replace(":", r"\:")


def make_chapter_card(source_video, title, subtitle, duration, output_path):
    fade = f"if(lt(t,0.4),t/0.4,if(gt(t,{duration:.2f}-0.3),({duration:.2f}-t)/0.3,1))"
    font_opt = f":fontfile={FONT_PATH}" if os.path.exists(FONT_PATH) else ""
    vf = (
        "loop=loop=-1:size=1:start=0,"
        "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "gblur=sigma=30,colorchannelmixer=rr=0.4:gg=0.4:bb=0.4,"
        f"drawtext=text='{_esc(title)}'{font_opt}:fontsize=72:fontcolor=white@1.0"
        f":x=(w-text_w)/2:y=(h-text_h)/2-60:shadowcolor=black@0.7:shadowx=3:shadowy=3:alpha='{fade}',"
        f"drawtext=text='{_esc(subtitle)}'{font_opt}:fontsize=36:fontcolor=FFE600@1.0"
        f":x=(w-text_w)/2:y=(h-text_h)/2+40:shadowcolor=black@0.7:shadowx=2:shadowy=2:alpha='{fade}'"
    )
    _run(["ffmpeg", "-y", "-ss", "0", "-i", source_video, "-vf", vf,
          "-t", str(duration), "-r", str(FPS), "-an",
          "-c:v", "libx264", "-preset", "fast", "-crf", "18",
          "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
          "-movflags", "+faststart", output_path], "Chapter card")


def add_silent_audio(video_path, output_path):
    _run(["ffmpeg", "-y", "-i", video_path,
          "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
          "-c:v", "copy", "-c:a", "aac", "-shortest",
          "-movflags", "+faststart", output_path], "Add silent audio")


def concat_files(paths, output_path, tmp_dir):
    list_file = os.path.join(tmp_dir, "concat.txt")
    with open(list_file, "w") as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
          "-c:v", "libx264", "-preset", "fast", "-crf", "18",
          "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-avoid_negative_ts", "make_zero",
          "-movflags", "+faststart", output_path], "Concat")


def render_podcast(media_path, chapters, words, job_id):
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    cut_results = cut_clips(media_path, chapters, job_id, words=words, output_dir=job_dir, vertical=False)
    rendered, rendered_paths = [], []
    for i, clip in enumerate(cut_results):
        if clip.get("status") != "done":
            rendered.append(clip)
            continue
        tmp_dir = tempfile.mkdtemp(prefix="pod_render_")
        try:
            clip_path = clip["file_path"]
            clip_dur = clip.get("cut_duration") or _probe_duration(clip_path) or 0
            card_raw = os.path.join(tmp_dir, "card_raw.mp4")
            make_chapter_card(clip_path, clip.get("title", f"Chapter {i+1}"),
                              clip.get("subtitle", ""), CARD_DURATION, card_raw)
            card_audio = os.path.join(tmp_dir, "card_audio.mp4")
            add_silent_audio(card_raw, card_audio)
            captioned = os.path.join(tmp_dir, "captioned.mp4")
            # Body from cut_clips is already captioned (same path as clips) — just use it.
            shutil.copy2(clip_path, captioned)
            chapter_out = os.path.join(job_dir, f"chapter_{i:02d}.mp4")
            concat_files([card_audio, captioned], chapter_out, tmp_dir)
            rendered.append({**clip, "rendered_path": chapter_out, "status": "done"})
            rendered_paths.append(chapter_out)
            logger.info(f"[{job_id}] Rendered chapter {i}: {chapter_out}")
        except Exception as e:
            logger.error(f"[{job_id}] Render failed for chapter {i}: {e}")
            rendered.append({**clip, "status": "failed", "error": str(e)[-500:]})
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    final_path = None
    if rendered_paths:
        final_path = os.path.join(job_dir, "podcast_final.mp4")
        tmp_dir = tempfile.mkdtemp(prefix="pod_stitch_")
        try:
            concat_files(rendered_paths, final_path, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info(f"[{job_id}] Podcast render done: {len(rendered_paths)} chapters, final={final_path}")
    return {"chapters": rendered, "final_video": final_path}
