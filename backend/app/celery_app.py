import logging
import os
import tempfile
import time
from pathlib import Path

from celery import Celery
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "avatar_system", broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    beat_schedule={
        "cleanup-old-files-daily": {
            "task": "cleanup_old_files",
            "schedule": crontab(hour=3, minute=0),  # Run daily at 3 AM
        },
    },
)


@celery_app.task(name="cleanup_old_files")
def cleanup_old_files_task():
    """Reap temp media older than 24h that a dropped/abandoned session left behind.

    The WebSocket pipeline writes per-turn audio/video into per-session dirs
    named ``avatar-session-<id>/`` under the system temp dir, and caches avatar
    images under ``avatars/`` there too — NOT the hardcoded ``/tmp/{avatars,
    videos,audio}`` this task used to scan. On a host where ``gettempdir()``
    isn't ``/tmp`` (or just because those dirs never existed) the old globs
    matched nothing and the real temp media leaked forever. Sweep the actual
    locations, oldest-first, and prune now-empty session dirs.
    """
    import shutil

    tmpdir = Path(tempfile.gettempdir())
    max_age_seconds = 24 * 60 * 60  # 24 hours
    now = time.time()
    total_cleaned = 0
    dirs_removed = 0

    try:
        # Per-session working dirs (input audio + per-chunk wav/mp4).
        for session_dir in tmpdir.glob("avatar-session-*"):
            if not session_dir.is_dir():
                continue
            # Snapshot the dir's mtime BEFORE deleting anything — unlinking a
            # child bumps the parent's mtime to now, which would otherwise make
            # a long-dead dir look freshly active and never get pruned.
            dir_stale = now - session_dir.stat().st_mtime > max_age_seconds
            for file_path in session_dir.rglob("*"):
                if file_path.is_file() and now - file_path.stat().st_mtime > max_age_seconds:
                    file_path.unlink(missing_ok=True)
                    total_cleaned += 1
            # Drop the session dir once it's empty and was itself stale — its
            # owning websocket is long gone.
            if dir_stale and not any(session_dir.iterdir()):
                shutil.rmtree(session_dir, ignore_errors=True)
                dirs_removed += 1

        # Cached avatar images + any legacy flat dirs that might still exist.
        for sub in ("avatars", "videos", "audio"):
            directory = tmpdir / sub
            if not directory.exists():
                continue
            for file_path in directory.iterdir():
                if file_path.is_file() and now - file_path.stat().st_mtime > max_age_seconds:
                    file_path.unlink(missing_ok=True)
                    total_cleaned += 1

        logger.info(
            f"Cleanup completed: {total_cleaned} file(s), {dirs_removed} session dir(s) removed"
        )
        return {"cleaned_files": total_cleaned, "dirs_removed": dirs_removed}

    except Exception as e:
        logger.error(f"Cleanup task failed: {e}")
        raise


@celery_app.task(name="run_pipeline_job", bind=True, max_retries=3)
def run_pipeline_job(self, job_id: str):
    """Pipeline job runner: pending -> running -> done/failed. Retries up to 3x on transient errors."""
    import asyncio

    logger.info(f"Starting pipeline job {job_id}")

    async def _run():
        from app.database import AsyncSessionLocal
        from app.models import Job

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None:
                logger.error(f"Job {job_id} not found")
                return

            try:
                job.status = "running"
                job.progress = 0
                await session.commit()

                if job.pipeline == "clips":
                    from app.services.clip_detector import detect_clips
                    from app.services.stt import stt_service

                    params = job.params or {}
                    media_path = params.get("media_path")
                    if not media_path:
                        raise ValueError("clips job requires params.media_path")

                    job.progress = 10
                    await session.commit()

                    r = await stt_service.transcribe_with_words(media_path)
                    job.progress = 50
                    await session.commit()

                    clips = await detect_clips(
                        r["words"], r["text"], params.get("instructions")
                    )
                    job.progress = 60
                    await session.commit()
                    from app.services.clip_cutter import cut_clips
                    cut_results = cut_clips(media_path, clips, str(job.id), words=r["words"])
                    job.progress = 90
                    await session.commit()
                    job.output = {
                        "clips": cut_results,
                        "transcript": r["text"],
                        "duration": r["duration"],
                    }
                elif job.pipeline == "podcast":
                    from app.services.stt import stt_service
                    from app.services.podcast_pipeline import detect_chapters, render_podcast
                    params = job.params or {}
                    media_path = params.get("media_path")
                    if not media_path:
                        raise ValueError("podcast job requires params.media_path")
                    job.progress = 10
                    await session.commit()
                    r = await stt_service.transcribe_with_words(media_path)
                    job.progress = 40
                    await session.commit()
                    chapters = await detect_chapters(
                        r["text"], r["duration"], params.get("instructions")
                    )
                    job.progress = 60
                    await session.commit()
                    result = render_podcast(media_path, chapters, r["words"], str(job.id))
                    job.progress = 90
                    await session.commit()
                    job.output = {
                        "chapters": result["chapters"],
                        "final_video": result["final_video"],
                        "transcript": r["text"],
                        "duration": r["duration"],
                    }
                elif job.pipeline == "talking-head":
                    from app.config import settings as _settings
                    params = job.params or {}
                    image_url = params.get("image_url")
                    audio_url = params.get("audio_url")
                    if not image_url or not audio_url:
                        raise ValueError("talking-head job requires params.image_url and params.audio_url")
                    job.progress = 10
                    await session.commit()

                    _engine = (params.get("engine") or _settings.AVATAR_ENGINE or "sadtalker").lower()
                    if _engine == "sadtalker":
                        # Proven HTTP path: SadTalker server (GPU box) or stub.
                        from app.services.sadtalker_engine import generate_avatar
                        result = await generate_avatar(image_url, audio_url, str(job.id))
                    else:
                        # MuseTalk / simple path via the animator. Animator needs
                        # LOCAL files, so download the URLs first, then animate.
                        # animator.animate() GPU-detects and auto-falls-back to
                        # simple (ffmpeg static image + audio) when MuseTalk/GPU
                        # is unavailable — safe on no-GPU dev machines.
                        import os as _os, httpx as _httpx
                        from app.services.animator import AvatarAnimator
                        _out_dir = f"/tmp/videos/animator/{job.id}"
                        _os.makedirs(_out_dir, exist_ok=True)
                        _img_path = f"{_out_dir}/source.png"
                        _aud_path = f"{_out_dir}/audio.wav"
                        _out_path = f"{_out_dir}/avatar.mp4"
                        async with _httpx.AsyncClient(timeout=120) as _c:
                            for _url, _dest in ((image_url, _img_path), (audio_url, _aud_path)):
                                _r = await _c.get(_url)
                                if _r.status_code != 200:
                                    raise ValueError(f"Could not fetch {_url} ({_r.status_code})")
                                with open(_dest, "wb") as _f:
                                    _f.write(_r.content)
                        _animator = AvatarAnimator()
                        _final = await _animator.animate(_img_path, _aud_path, _out_path)
                        result = {
                            "status": "done",
                            "file_path": _final,
                            "engine": _animator.engine,
                            "size_bytes": _os.path.getsize(_final) if _os.path.exists(_final) else 0,
                        }

                    job.progress = 90
                    await session.commit()
                    job.output = result
                elif job.pipeline == "explainer":
                    # Stage A (video -> section scripts) + Stage B (sections ->
                    # per-section avatar clips). Deck video arrives as a LOCAL
                    # media_path (POST /jobs/upload). Avatar image arrives as an
                    # image_url (avatar upload returns a URL), so download it to
                    # a local file before Stage B, mirroring the talking-head
                    # branch. Voice: pass speaker_wav for cloned voice if given.
                    import os as _os, httpx as _httpx
                    from app.services.explainer_pipeline import (
                        video_to_scripts,
                        sections_to_clips,
                    )
                    params = job.params or {}
                    media_path = params.get("media_path")
                    image_url = params.get("image_url")
                    if not media_path or not image_url:
                        raise ValueError(
                            "explainer job requires params.media_path and params.image_url"
                        )
                    language = params.get("language", "en")
                    instructions = params.get("instructions")
                    speaker_wav = params.get("speaker_wav")

                    job.progress = 10
                    await session.commit()
                    stage_a = await video_to_scripts(
                        media_path, language=language, instructions=instructions
                    )
                    sections = stage_a.get("sections", [])
                    if not sections:
                        raise ValueError(
                            f"explainer: no sections produced ({stage_a.get('message', 'empty')})"
                        )
                    job.progress = 45
                    await session.commit()

                    _work = f"/tmp/videos/explainer/{job.id}"
                    _os.makedirs(_work, exist_ok=True)
                    _img_path = f"{_work}/avatar.png"
                    # Avatar image_url is a user-facing URL (e.g. localhost:8000).
                    # Inside the worker container, localhost is the worker itself,
                    # so rewrite the host to the backend service on the compose
                    # network before fetching. No-op if already an internal URL.
                    _fetch_url = (
                        image_url
                        .replace("//localhost:8000", "//backend:8000")
                        .replace("//127.0.0.1:8000", "//backend:8000")
                    )
                    async with _httpx.AsyncClient(timeout=120) as _c:
                        _r = await _c.get(_fetch_url)
                        if _r.status_code != 200:
                            raise ValueError(
                                f"Could not fetch avatar image {image_url} ({_r.status_code})"
                            )
                        with open(_img_path, "wb") as _f:
                            _f.write(_r.content)
                    job.progress = 55
                    await session.commit()

                    clips = await sections_to_clips(
                        sections=sections,
                        image_path=_img_path,
                        speaker_wav=speaker_wav,
                        language=language,
                        out_dir=f"{_work}/clips",
                    )
                    job.progress = 90
                    await session.commit()
                    job.output = {
                        "clips": clips,
                        "sections": sections,
                        "transcript": stage_a.get("transcript", ""),
                        "engine": clips[0]["engine"] if clips else None,
                    }
                else:
                    for pct in (25, 50, 75):
                        await asyncio.sleep(3)
                        job.progress = pct
                        await session.commit()
                    job.output = {"echo": job.params}

                job.status = "done"
                job.progress = 100
                await session.commit()
                logger.info(f"Pipeline job {job_id} done")
            except Exception as e:
                logger.error(f"Pipeline job {job_id} failed (attempt {self.request.retries+1}): {e}")
                is_hard_error = isinstance(e, (ValueError, FileNotFoundError))
                if is_hard_error or self.request.retries >= self.max_retries:
                    job.status = "failed"
                    job.error = str(e)
                    await session.commit()
                else:
                    job.status = "pending"
                    job.progress = 0
                    job.error = f"Retrying ({self.request.retries+1}/{self.max_retries}): {e}"
                    await session.commit()
                    raise self.retry(exc=e, countdown=30 * (self.request.retries + 1))

    from app.database import engine
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(engine.dispose())
    loop.run_until_complete(_run())
    loop.close()
