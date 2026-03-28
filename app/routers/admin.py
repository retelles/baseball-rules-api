from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, require_admin
from app.models.usage_event import EventType, UsageEvent
from app.models.user import User
from app.schemas.user import UserResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats/overview")
def stats_overview(
    _current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    active_users_30d = (
        db.query(func.count(func.distinct(UsageEvent.user_id)))
        .filter(
            UsageEvent.created_at >= thirty_days_ago,
            UsageEvent.user_id.isnot(None),
        )
        .scalar()
        or 0
    )

    total_logins_today = (
        db.query(func.count(UsageEvent.id))
        .filter(
            UsageEvent.event_type == EventType.login,
            UsageEvent.created_at >= today_start,
        )
        .scalar()
        or 0
    )

    total_searches_today = (
        db.query(func.count(UsageEvent.id))
        .filter(
            UsageEvent.event_type == EventType.search,
            UsageEvent.created_at >= today_start,
        )
        .scalar()
        or 0
    )

    total_users = db.query(func.count(User.id)).scalar() or 0

    return {
        "active_users_30d": active_users_30d,
        "total_logins_today": total_logins_today,
        "total_searches_today": total_searches_today,
        "total_users": total_users,
    }


@router.get("/users", response_model=list[UserResponse])
def list_users(
    _current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    email: str | None = Query(default=None, description="Filter by email (partial match)"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[User]:
    query = db.query(User)
    if email:
        query = query.filter(User.email.ilike(f"%{email}%"))
    return query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/users/{user_id}/disable", response_model=UserResponse)
def disable_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if str(user_id) == str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot disable your own account",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/enable", response_model=UserResponse)
def enable_user(
    user_id: UUID,
    _current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user
