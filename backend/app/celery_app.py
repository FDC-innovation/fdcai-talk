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


@celery_app.task(name="process_avatar", bind=True, max_retries=3)
def process_avatar_task(self, avatar_id: str, image_path: str):
    """Background task to process avatar image"""
    try:
        logger.info(f"Processing avatar {avatar_id} from {image_path}")

        import asyncio

        from app.services.avatar_processor import avatar_processor

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Honor the actual system temp dir instead of hardcoding /tmp.
        processed_dir = Path(tempfile.gettempdir()) / "avatars"
        processed_dir.mkdir(parents=True, exist_ok=True)
        processed_path = str(processed_dir / f"{avatar_id}_processed.jpg")
        result_path, metadata = loop.run_until_complete(
            avatar_processor.process_image(image_path, processed_path)
        )
        loop.close()

        logger.info(f"Avatar {avatar_id} processed successfully: {result_path}")
        return {
            "avatar_id": avatar_id,
            "processed_path": result_path,
            "metadata": metadata,
            "status": "ready",
        }

    except Exception as e:
        logger.error(f"Failed to process avatar {avatar_id}: {e}")
        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="generate_video", bind=True, max_retries=2)
def generate_video_task(self, session_id: str, text: str, avatar_image_path: str):
    """Background task to generate avatar video"""
    try:
        logger.info(f"Generating video for session {session_id}")

        import asyncio

        from app.services.animator import avatar_animator
        from app.services.tts import tts_service

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # mkstemp creates the file atomically with owner-only perms (0600) —
        # mktemp only returned a name, leaving a TOCTOU race and world-readable
        # output. We close the fds immediately; the services write to the paths.
        afd, audio_path = tempfile.mkstemp(suffix=".wav")
        vfd, video_path = tempfile.mkstemp(suffix=".mp4")
        os.close(afd)
        os.close(vfd)

        loop.run_until_complete(tts_service.synthesize(text, audio_path))
        loop.run_until_complete(avatar_animator.animate(avatar_image_path, audio_path, video_path))

        loop.close()

        # Clean up audio temp file
        Path(audio_path).unlink(missing_ok=True)

        logger.info(f"Video generated for session {session_id}: {video_path}")
        return {"session_id": session_id, "video_path": video_path, "status": "completed"}

    except Exception as e:
        logger.error(f"Failed to generate video for session {session_id}: {e}")
        raise self.retry(exc=e, countdown=30 * (self.request.retries + 1))


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


@celery_app.task(name="run_pipeline_job", bind=True)
def run_pipeline_job(self, job_id: str):
    """Generic job runner (echo loop for now): pending -> running -> done."""
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
                    cut_results = cut_clips(media_path, clips, str(job.id))
                    job.progress = 90
                    await session.commit()
                    job.output = {
                        "clips": cut_results,
                        "transcript": r["text"],
                        "duration": r["duration"],
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
                logger.error(f"Pipeline job {job_id} failed: {e}")
                job.status = "failed"
                job.error = str(e)
                await session.commit()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())
    loop.close()
