import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import get_current_user
from app.database import get_db
from app.models import Job, User
from app.schemas import JobCreate, JobResponse
from app.celery_app import celery_app

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_PIPELINES = {"clips", "talking-head", "podcast"}


def _user_id(current_user: Optional[User]) -> str:
    if current_user:
        return current_user.id
    from app.config import settings
    if settings.DEBUG:
        return "demo-user"
    from fastapi import HTTPException, status
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def _validate_uuid(job_id: str) -> None:
    # Same 404-for-malformed-ids convention as avatars.py.
    try:
        uuid.UUID(job_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Job not found")


import os
import shutil
from fastapi import UploadFile, File
from fastapi.responses import FileResponse

UPLOAD_DIR = "/tmp/videos/uploads"
ALLOWED_MEDIA_TYPES = {
    "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm",
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/ogg",
    "application/octet-stream",  # curl and some clients send this for binary files
}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500MB


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    current_user: Optional[User] = Depends(get_current_user),
):
    """
    Upload a media file for use in pipeline jobs.
    Returns a media_path to pass as params.media_path in POST /jobs/{pipeline}.
    Supports video (mp4, mov, avi, webm) and audio (mp3, wav, m4a, ogg) up to 500MB.
    """
    uid = _user_id(current_user)
    if file.content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported media type '{file.content_type}'. Allowed: video/mp4, audio/wav, etc.",
        )
    user_dir = os.path.join(UPLOAD_DIR, uid)
    os.makedirs(user_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "upload")[1] or ".mp4"
    dest = os.path.join(user_dir, f"{uuid.uuid4()}{ext}")
    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                os.unlink(dest)
                raise HTTPException(status_code=413, detail="File too large (max 500MB)")
            f.write(chunk)
    logger.info(f"Media uploaded: {dest} ({size//1024}KB) user={uid}")
    return {
        "media_path": dest,
        "filename": file.filename,
        "size_bytes": size,
        "content_type": file.content_type,
    }


@router.get("/{job_id}/download/{artifact}")
async def download_artifact(
    job_id: str,
    artifact: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """
    Download an output file from a completed job.
    artifact: 'final' for the main output, or clip/chapter index like 'clip_0', 'chapter_1'
    Works with local storage now; swap FileResponse for S3 presigned URL when USE_LOCAL_STORAGE=false.
    """
    _validate_uuid(job_id)
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != _user_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorised")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job not done yet (status={job.status})")

    output = job.output or {}
    file_path = None

    if artifact == "final":
        # podcast final stitch, talking-head avatar
        file_path = output.get("final_video") or output.get("file_path")
    elif artifact.startswith("clip_"):
        idx = int(artifact.split("_")[1])
        clips = output.get("clips", [])
        if idx < len(clips):
            file_path = clips[idx].get("file_path")
    elif artifact.startswith("chapter_"):
        idx = int(artifact.split("_")[1])
        chapters = output.get("chapters", [])
        if idx < len(chapters):
            file_path = chapters[idx].get("rendered_path") or chapters[idx].get("file_path")

    if not file_path:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact}' not found in job output")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Output file missing on server: {file_path}")

    filename = os.path.basename(file_path)
    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.post("/{pipeline}", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    pipeline: str,
    payload: JobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Create a pipeline job. Returns immediately with status=pending."""
    if pipeline == "upload":
        raise HTTPException(status_code=400, detail="Use POST /jobs/upload to upload files")
    if pipeline not in VALID_PIPELINES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown pipeline '{pipeline}'. Valid: {sorted(VALID_PIPELINES)}",
        )

    job = Job(
        id=str(uuid.uuid4()),
        user_id=_user_id(current_user),
        pipeline=pipeline,
        status="pending",
        progress=0,
        params=payload.params,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    queue = "gpu" if pipeline == "talking-head" else "cpu"
    celery_app.send_task("run_pipeline_job", args=[job.id], queue=queue)

    logger.info(f"Job created: {job.id} pipeline={pipeline} user={job.user_id}")
    return job


@router.get("/", response_model=List[JobResponse])
async def list_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """List jobs belonging to the current user, newest first."""
    uid = _user_id(current_user)
    result = await db.execute(
        select(Job)
        .where(Job.user_id == uid)
        .offset(skip)
        .limit(limit)
        .order_by(Job.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Fetch one job (poll this for status/progress)."""
    _validate_uuid(job_id)
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != _user_id(current_user):
        raise HTTPException(status_code=403, detail="Not authorised to access this job")
    return job

