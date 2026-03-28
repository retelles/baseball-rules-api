import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Enum as SAEnum, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class EventType(str, enum.Enum):
    login = "login"
    search = "search"
    pdf_view = "pdf_view"
    pdf_download = "pdf_download"


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(SAEnum(EventType), nullable=False, index=True)
    event_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("User", back_populates="usage_events")
