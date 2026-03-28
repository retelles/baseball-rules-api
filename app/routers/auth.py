from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from jose import JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_user
from app.models.password_reset_token import PasswordResetToken
from app.models.usage_event import UsageEvent, EventType
from app.models.user import User
from app.schemas.auth import ForgotPasswordRequest, RefreshTokenRequest, ResetPasswordRequest
from app.schemas.user import Token, UserCreate, UserLogin, UserResponse
from app.services.auth_service import AuthService
from app.services.email_service import email_service
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
def register(
    request: Request,
    payload: UserCreate,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        email=payload.email,
        hashed_password=AuthService.hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    background_tasks.add_task(email_service.send_welcome_email, user.email, user.email.split("@")[0])
    return user


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
def login(
    request: Request,
    payload: UserLogin,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> Token:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not AuthService.verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    def _log_login(user_id: str) -> None:
        log_db = next(get_db())
        try:
            event = UsageEvent(user_id=user_id, event_type=EventType.login)
            log_db.add(event)
            log_db.commit()
        finally:
            log_db.close()

    background_tasks.add_task(_log_login, str(user.id))

    access_token = AuthService.create_access_token(subject=str(user.id), extra_claims={"role": user.role})
    refresh_token = AuthService.create_refresh_token(subject=str(user.id))
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
def refresh(
    payload: RefreshTokenRequest,
    db: Annotated[Session, Depends(get_db)],
) -> Token:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
    try:
        data = AuthService.decode_token(payload.refresh_token)
        user_id: str | None = data.get("sub")
        token_type: str | None = data.get("type")
        if user_id is None or token_type != "refresh":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise credentials_exception

    access_token = AuthService.create_access_token(subject=str(user.id), extra_claims={"role": user.role})
    refresh_token = AuthService.create_refresh_token(subject=str(user.id))
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    # Token invalidation is client-side. For production, maintain a token denylist.
    return {"message": "Logged out successfully"}


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    # Always return 200 to avoid email enumeration
    user = db.query(User).filter(User.email == payload.email).first()
    if user and user.is_active:
        raw_token = AuthService.generate_reset_token()
        hashed = AuthService.hash_reset_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        reset_token = PasswordResetToken(
            user_id=user.id,
            token=hashed,
            expires_at=expires_at,
        )
        db.add(reset_token)
        db.commit()

        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={raw_token}"
        background_tasks.add_task(email_service.send_password_reset_email, user.email, reset_url)

    return {"message": "If that email is registered, a reset link has been sent"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
def reset_password(
    payload: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    hashed_token = AuthService.hash_reset_token(payload.token)

    reset_record = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token == hashed_token)
        .first()
    )

    now = datetime.now(timezone.utc)

    if not reset_record:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    if reset_record.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token has already been used")

    if reset_record.expires_at < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token has expired")

    user = db.query(User).filter(User.id == reset_record.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")

    user.hashed_password = AuthService.hash_password(payload.new_password)
    reset_record.used_at = now
    db.commit()

    return {"message": "Password updated successfully"}
