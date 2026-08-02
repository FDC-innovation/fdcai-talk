import os
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

OUTPUT_DIR = "/tmp/videos/clips"
WATERMARK_TEXT = "Chalchitra"
CARD_SECONDS = 2.0
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _safe_title(title: str, max_len: int = 40) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:max_len]


def _probe_duration(path: str) -> float | None:
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries",
         "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    return float(probe.stdout.strip())


def _srt_ts(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _group_caption_lines(words: list, start: float, end: float, max_words: int = 5):
    in_range = []
    for w in words or []:
        try:
            ws = float(w["start"]); we = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if we <= start or ws >= end:
            continue
        in_range.append({
            "word": str(w.get("word", "")).strip(),
            "start": max(0.0, ws - start),
            "end": max(0.0, we - start),
        })
    lines = []
    for i in range(0, len(in_range), max_words):
        grp = in_range[i:i + max_words]
        if not grp:
            continue
        text = " ".join(g["word"] for g in grp).strip()
        if text:
            lines.append({"start": grp[0]["start"], "end": grp[-1]["end"], "text": text})
    return lines


def _write_srt(words: list, start: float, end: float, path: str) -> bool:
    lines = _group_caption_lines(words, start, end)
    if not lines:
        return False
    with open(path, "w", encoding="utf-8") as f:
        for i, ln in enumerate(lines, 1):
            f.write(f"{i}\n{_srt_ts(ln['start'])} --> {_srt_ts(ln['end'])}\n{ln['text']}\n\n")
    return True


def _esc_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
    return text


def _make_card(text: str, subtitle: str, out_path: str, job_id: str) -> bool:
    main = _esc_drawtext(text)[:60]
    sub = _esc_drawtext(subtitle)[:80] if subtitle else ""
    draw = (
        f"drawtext=fontfile={FONT}:text='{main}':fontcolor=white:fontsize=64:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-60:box=0"
    )
    if sub:
        draw += (
            f",drawtext=fontfile={FONT}:text='{sub}':fontcolor=0xAAAAAA:fontsize=40:"
            f"x=(w-text_w)/2:y=(h-text_h)/2+60:box=0"
        )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:d={CARD_SECONDS}:r=30",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-vf", draw,
        "-t", str(CARD_SECONDS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100",
        "-shortest",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return os.path.exists(out_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"[{job_id}] card render failed: {e}")
        return False


def _concat(parts: list, out_path: str, job_id: str) -> bool:
    list_path = out_path + ".txt"
    with open(list_path, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100",
        "-movflags", "+faststart",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        os.remove(list_path)
        return os.path.exists(out_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"[{job_id}] concat failed: {e}")
        return False


def cut_clips(media_path: str, clips: list, job_id: str, words: list = None,
              output_dir: str = OUTPUT_DIR) -> list:
    os.makedirs(output_dir, exist_ok=True)
    results = []
    for i, clip in enumerate(clips):
        start = float(clip.get("start_seconds", 0))
        end = float(clip.get("end_seconds", start + 30))
        duration = max(0.1, end - start)
        title = clip.get("title", f"clip_{i}")
        base = f"{job_id}_clip{i}_{_safe_title(title)}"
        output_path = os.path.join(output_dir, f"{base}.mp4")
        body_path = os.path.join(output_dir, f"{base}_body.mp4")
        srt_path = os.path.join(output_dir, f"{base}.srt")

        # Layout: 1080x1920 canvas, video centered, captions in bottom black bar.
        # ASS Fontsize is in PlayRes units (1920 tall) -> use ~64 for large readable text.
        vf = ("scale=1080:-2,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
        has_srt = _write_srt(words, start, end, srt_path)
        if has_srt:
            style = ("FontName=DejaVu Sans,Fontsize=64,Bold=1,"
                     "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                     "BorderStyle=1,Outline=4,Shadow=1,Alignment=2,MarginV=420,"
                     "PlayResX=1080,PlayResY=1920")
            vf += f",subtitles='{srt_path}':force_style='{style}'"

        coarse = max(0, start - 5)
        fine = start - coarse
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(coarse),
            "-i", media_path,
            "-ss", str(fine),
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-r", "30",
            "-c:a", "aac", "-ar", "44100",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            body_path,
        ]
        try:
            logger.info(f"[{job_id}] Cutting clip {i}: {start:.2f}-{end:.2f}s (captions={has_srt})")
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)

            title_card = os.path.join(output_dir, f"{base}_title.mp4")
            outro_card = os.path.join(output_dir, f"{base}_outro.mp4")
            parts = []
            if _make_card(title, clip.get("reason", ""), title_card, job_id):
                parts.append(title_card)
            parts.append(body_path)
            if _make_card(WATERMARK_TEXT, "AI-generated highlight", outro_card, job_id):
                parts.append(outro_card)

            final_ok = False
            if len(parts) > 1:
                final_ok = _concat(parts, output_path, job_id)
            if not final_ok:
                os.replace(body_path, output_path)

            probed = _probe_duration(output_path)
            if probed is None:
                results.append({**clip, "status": "failed", "error": "Output not seekable"})
            else:
                results.append({**clip, "file_path": output_path,
                                "cut_duration": probed, "captions": has_srt,
                                "status": "done"})
        except subprocess.TimeoutExpired:
            results.append({**clip, "status": "failed", "error": "ffmpeg timed out"})
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="replace")[-500:] if e.stderr else "unknown"
            results.append({**clip, "status": "failed", "error": err})
    logger.info(f"[{job_id}] Cutting done: {sum(1 for r in results if r['status']=='done')}/{len(results)} ok")
    return results
