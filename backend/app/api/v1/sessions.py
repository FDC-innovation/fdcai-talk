import asyncio
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import get_current_user
from app.database import get_db
from app.models import Avatar, Message, Session, User
from app.schemas import SessionCreate, SessionResponse
from app.websocket import websocket_manager

logger = logging.getLogger(__name__)
router = APIRouter()


def _user_id(current_user: Optional[User]) -> str:
    if current_user:
        return current_user.id
    from app.config import settings
    if settings.DEBUG:
        return "demo-user"
    from fastapi import HTTPException, status
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


@router.post("/create", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_data: SessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Create a new conversation session for the current user."""
    try:
        result = await db.execute(select(Avatar).where(Avatar.id == session_data.avatar_id))
        avatar = result.scalar_one_or_none()

        if not avatar:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

        if avatar.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Avatar is not ready"
            )

        # Ensure user owns this avatar (or is demo)
        uid = _user_id(current_user)
        if avatar.user_id != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised to use this avatar"
            )

        session = Session(
            user_id=uid,
            avatar_id=session_data.avatar_id,
            status="active",
            settings=session_data.settings or {},
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)

        logger.info(f"Session created: {session.id} (user={uid})")
        return session

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create session"
        )


@router.get("/", response_model=List[SessionResponse])
async def list_sessions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """List sessions belonging to the current user."""
    try:
        result = await db.execute(
            select(Session)
            .where(Session.user_id == _user_id(current_user))
            .offset(skip)
            .limit(limit)
            .order_by(Session.started_at.desc())
        )
        return result.scalars().all()
    except Exception as e:
        logger.error(f"Failed to list sessions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list sessions"
        )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Get session by ID (must belong to current user)."""
    try:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        if session.user_id != _user_id(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorised to access this session",
            )

        return session
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get session"
        )


@router.post("/{session_id}/end", response_model=SessionResponse)
async def end_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """End an active session."""
    try:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        if session.user_id != _user_id(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised to end this session"
            )

        session.status = "ended"
        session.ended_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(session)

        await websocket_manager.disconnect(session_id)

        logger.info(f"Session ended: {session_id}")
        return session

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to end session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to end session"
        )


_EXPORT_MAX_MESSAGES = 5000


@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """
    Export a session and its messages as a downloadable JSON file.

    Capped at _EXPORT_MAX_MESSAGES so a 50k-message session can't be used
    as a DoS amplifier (5 MB response per request × repeated calls).
    For larger sessions the user should narrow the export by date range
    once that endpoint exists.
    """
    try:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.user_id != _user_id(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")

        msgs_result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at, Message.id)
            .limit(_EXPORT_MAX_MESSAGES + 1)  # +1 so we can detect truncation
        )
        messages = msgs_result.scalars().all()
        truncated = len(messages) > _EXPORT_MAX_MESSAGES
        if truncated:
            messages = messages[:_EXPORT_MAX_MESSAGES]

        payload = {
            "session": {
                "id": session.id,
                "avatar_id": session.avatar_id,
                "status": session.status,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            },
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "content_type": m.content_type,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "latency": m.latency,
                }
                for m in messages
            ],
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "truncated": truncated,
            "max_messages": _EXPORT_MAX_MESSAGES if truncated else None,
        }
        headers = {
            "Content-Disposition": f'attachment; filename="session-{session.id[:8]}.json"',
            "Cache-Control": "no-store",
        }
        return JSONResponse(content=payload, headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export session",
        )


@router.get("/{session_id}/download-video")
async def download_session_video(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """
    Merge all video chunks for a session into one MP4 and return it as a download.
    Uses FFmpeg concat (no re-encoding) so it completes in seconds.
    """
    try:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.user_id != _user_id(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")

        # Find all video chunks for this session (local storage path)
        videos_dir = Path("/app/uploads/videos") / session_id
        if not videos_dir.exists():
            raise HTTPException(status_code=404, detail="No videos found for this session")

        chunks = sorted(videos_dir.glob("*.mp4"))
        if not chunks:
            raise HTTPException(status_code=404, detail="No video chunks found")

        if len(chunks) == 1:
            # Only one chunk — return it directly
            return FileResponse(
                str(chunks[0]),
                media_type="video/mp4",
                filename=f"fdcai-session-{session_id[:8]}.mp4",
            )

        # Multiple chunks — merge with FFmpeg concat (copy, no re-encode)
        tmp_dir = Path(tempfile.mkdtemp(prefix="fdcai-merge-"))
        concat_list = tmp_dir / "list.txt"
        output_path = tmp_dir / f"merged-{session_id[:8]}.mp4"

        concat_list.write_text(
            "\n".join(f"file '{str(c)}'" for c in chunks),
            encoding="utf-8",
        )

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.error(f"FFmpeg merge failed for session {session_id}: {err}")
            raise HTTPException(status_code=500, detail=f"Video merge failed: {err}")

        logger.info(f"Merged {len(chunks)} chunks for session {session_id} → {output_path}")
        return FileResponse(
            str(output_path),
            media_type="video/mp4",
            filename=f"fdcai-session-{session_id[:8]}.mp4",
            headers={"Content-Disposition": f'attachment; filename="fdcai-session-{session_id[:8]}.mp4"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download session video {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to merge videos")


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Delete a session (must belong to current user)."""
    try:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        if session.user_id != _user_id(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorised to delete this session",
            )

        await db.delete(session)
        await db.commit()
        logger.info(f"Session deleted: {session_id}")

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to delete session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete session"
        )
