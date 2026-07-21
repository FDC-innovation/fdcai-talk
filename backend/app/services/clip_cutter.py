import os
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

OUTPUT_DIR = "/tmp/videos/clips"


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


def cut_clips(media_path: str, clips: list, job_id: str, output_dir: str = OUTPUT_DIR) -> list:
    os.makedirs(output_dir, exist_ok=True)
    results = []
    for i, clip in enumerate(clips):
        start = float(clip.get("start_seconds", 0))
        end = float(clip.get("end_seconds", start + 30))
        duration = max(0.1, end - start)
        title = clip.get("title", f"clip_{i}")
        output_path = os.path.join(
            output_dir, f"{job_id}_clip{i}_{_safe_title(title)}.mp4"
        )
        coarse = max(0, start - 5)
        fine = start - coarse
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(coarse),
            "-i", media_path,
            "-ss", str(fine),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-r", "30",
            "-c:a", "aac", "-ar", "44100",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            output_path
        ]
        try:
            logger.info(f"[{job_id}] Cutting clip {i}: {start:.2f}-{end:.2f}s -> {output_path}")
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            probed = _probe_duration(output_path)
            if probed is None:
                results.append({**clip, "status": "failed", "error": "Output not seekable"})
            else:
                results.append({**clip, "file_path": output_path,
                                "cut_duration": probed, "status": "done"})
        except subprocess.TimeoutExpired:
            results.append({**clip, "status": "failed", "error": "ffmpeg timed out"})
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="replace")[-500:] if e.stderr else "unknown"
            results.append({**clip, "status": "failed", "error": err})
    logger.info(f"[{job_id}] Cutting done: {sum(1 for r in results if r['status']=='done')}/{len(results)} ok")
    return results
