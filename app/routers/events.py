from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db
from app.models.usage_event import EventType, UsageEvent
from app.models.user import User

router = APIRouter(prefix="/events", tags=["events"])


class TrackEventRequest(BaseModel):
    event_type: EventType
    event_metadata: dict | None = None


def _persist_event(user_id: str, event_type: EventType, event_metadata: dict | None) -> None:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        event = UsageEvent(
            user_id=user_id,
            event_type=event_type,
            event_metadata=event_metadata,
        )
        db.add(event)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@router.post("/track", status_code=status.HTTP_202_ACCEPTED)
def track_event(
    payload: TrackEventRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    background_tasks.add_task(
        _persist_event,
        str(current_user.id),
        payload.event_type,
        payload.event_metadata,
    )
    return {"message": "Event accepted"}
