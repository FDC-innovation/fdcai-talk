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

VALID_PIPELINES = {"clips", "sadtalker", "podcast"}


def _user_id(current_user: Optional[User]) -> str:
    return current_user.id if current_user else "demo-user"


def _validate_uuid(job_id: str) -> None:
    # Same 404-for-malformed-ids convention as avatars.py.
    try:
        uuid.UUID(job_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Job not found")


@router.post("/{pipeline}", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    pipeline: str,
    payload: JobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Create a pipeline job. Returns immediately with status=pending."""
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

    queue = "gpu" if pipeline == "sadtalker" else "cpu"
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
