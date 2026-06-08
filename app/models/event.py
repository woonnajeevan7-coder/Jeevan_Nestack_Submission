import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, JSON, DateTime, Integer, Index
from sqlalchemy.orm import relationship
from app.database.connection import Base

def get_utc_now():
    """Returns the current UTC time as a timezone-naive datetime object (for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Event(Base):
    __tablename__ = "events"
    
    # Composite Index on status and next_retry_at to optimize worker queries
    __table_args__ = (
        Index("ix_events_status_next_retry_at", "status", "next_retry_at"),
    )

    # UUID stored as a 36-character string
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String(255), nullable=False)
    payload_json = Column(JSON, nullable=False)
    payload_raw = Column(String, nullable=False)
    webhook_url = Column(String(500), nullable=False)
    
    # Status can be: 'pending', 'processing', 'delivered', 'failed', 'dead'
    status = Column(String(50), nullable=False, default="pending")
    
    created_at = Column(DateTime, nullable=False, default=get_utc_now)
    next_retry_at = Column(DateTime, nullable=True, default=get_utc_now)
    retry_count = Column(Integer, nullable=False, default=0)

    # One Event -> Many Attempts relationship
    attempts = relationship(
        "Attempt",
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
